"""Safe cropping and whitespace trimming for detected problem regions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray

from app.config import CropConfig
from app.models import BBox, CroppedProblem, PageImage, ProblemRegion, clamp_bbox


@dataclass(slots=True)
class TrimResult:
    """Result of gentle outer-whitespace trimming on a crop."""

    image: NDArray[np.uint8]
    content_bbox: BBox
    final_bbox: BBox
    padding: tuple[int, int, int, int]
    metadata: dict[str, Any] = field(default_factory=dict)


def crop_problem_regions(
    page: PageImage,
    regions: list[ProblemRegion],
    config: CropConfig,
) -> list[CroppedProblem]:
    """Safely crop a list of detected regions from one page."""

    return [
        safe_crop(
            page,
            region,
            config,
            problem_id=f"p{page.page_index + 1}_{index + 1:03d}",
        )
        for index, region in enumerate(regions)
    ]


def safe_crop(
    page: PageImage,
    region: ProblemRegion,
    config: CropConfig,
    *,
    problem_id: str | None = None,
) -> CroppedProblem:
    """Expand a detected region conservatively and trim only excess outer whitespace.

    The crop strategy prioritizes preserving question numbers, diagrams, arrows, and
    lower-edge annotations over producing the tightest possible box.
    """

    if page.image is None:
        raise ValueError("PageImage.image is required for cropping.")

    expanded_bbox, expansion_metadata = compute_safe_crop_bbox(page, region, config)
    x1, y1, x2, y2 = expanded_bbox
    crop_image = page.image[y1:y2, x1:x2].copy()

    trim_result = trim_outer_whitespace(
        crop_image,
        config,
        shape_hint=str(expansion_metadata["shape_type"]),
        fill_ratio=float(expansion_metadata["estimated_fill_ratio"]),
    )

    final_bbox = (
        x1 + trim_result.final_bbox[0],
        y1 + trim_result.final_bbox[1],
        x1 + trim_result.final_bbox[2],
        y1 + trim_result.final_bbox[3],
    )
    trim_bbox = (
        x1 + trim_result.content_bbox[0],
        y1 + trim_result.content_bbox[1],
        x1 + trim_result.content_bbox[2],
        y1 + trim_result.content_bbox[3],
    )

    resolved_problem_id = problem_id or region.problem_no or (
        f"p{page.page_index + 1}_{region.bbox[0]}_{region.bbox[1]}"
    )

    metadata = dict(region.metadata)
    metadata.update(expansion_metadata)
    metadata["expanded_bbox"] = expanded_bbox
    metadata["trim_metadata"] = trim_result.metadata

    return CroppedProblem(
        problem_id=resolved_problem_id,
        page_index=page.page_index,
        original_bbox=region.bbox,
        cropped_bbox=final_bbox,
        image=trim_result.image,
        trim_bbox=trim_bbox,
        padding=trim_result.padding,
        metadata=metadata,
    )


def trim_outer_whitespace(
    image: NDArray[np.uint8],
    config: CropConfig,
    *,
    shape_hint: str | None = None,
    fill_ratio: float | None = None,
) -> TrimResult:
    """Remove only excess outer whitespace while keeping a safety margin.

    This is intentionally conservative. It trims broad blank borders but keeps
    additional padding for sparse, tall, wide, and near-round diagrams.
    """

    if image.size == 0:
        raise ValueError("Cannot trim whitespace from an empty image.")

    height, width = image.shape[:2]
    gray = to_grayscale(image)
    binary_foreground = (gray < int(config.whitespace_threshold)).astype(np.uint8) * 255

    # Slight dilation helps preserve thin arrows and table lines during trimming.
    dilated = cv2.dilate(binary_foreground, np.ones((3, 3), dtype=np.uint8), iterations=1)
    ys, xs = np.where(dilated > 0)

    if len(xs) == 0 or len(ys) == 0:
        full_bbox = (0, 0, width, height)
        return TrimResult(
            image=image.copy(),
            content_bbox=full_bbox,
            final_bbox=full_bbox,
            padding=(0, 0, 0, 0),
            metadata={"empty_foreground": True},
        )

    scan_guard = max(1, int(config.trim_scan_step))
    content_bbox = clamp_relative_bbox(
        (
            int(xs.min()) - scan_guard,
            int(ys.min()) - scan_guard,
            int(xs.max()) + 1 + scan_guard,
            int(ys.max()) + 1 + scan_guard,
        ),
        width=width,
        height=height,
    )

    measured_fill_ratio = (len(xs) / max(width * height, 1)) if fill_ratio is None else float(fill_ratio)
    content_width = content_bbox[2] - content_bbox[0]
    content_height = content_bbox[3] - content_bbox[1]
    shape_type = shape_hint or classify_problem_shape(content_width, content_height, preserve_round_shapes=config.preserve_round_shapes)
    padding = compute_trim_padding(
        content_width=content_width,
        content_height=content_height,
        crop_width=width,
        crop_height=height,
        config=config,
        shape_type=shape_type,
        fill_ratio=measured_fill_ratio,
    )

    left_pad, top_pad, right_pad, bottom_pad = padding
    final_bbox = clamp_relative_bbox(
        (
            content_bbox[0] - left_pad,
            content_bbox[1] - top_pad,
            content_bbox[2] + right_pad,
            content_bbox[3] + bottom_pad,
        ),
        width=width,
        height=height,
    )

    if should_keep_original_crop(full_size=(width, height), trimmed_bbox=final_bbox):
        final_bbox = (0, 0, width, height)

    fx1, fy1, fx2, fy2 = final_bbox
    trimmed_image = image[fy1:fy2, fx1:fx2].copy()

    effective_padding = (
        content_bbox[0] - final_bbox[0],
        content_bbox[1] - final_bbox[1],
        final_bbox[2] - content_bbox[2],
        final_bbox[3] - content_bbox[3],
    )

    return TrimResult(
        image=trimmed_image,
        content_bbox=content_bbox,
        final_bbox=final_bbox,
        padding=effective_padding,
        metadata={
            "shape_type": shape_type,
            "measured_fill_ratio": measured_fill_ratio,
            "original_size": (width, height),
            "trimmed_size": (fx2 - fx1, fy2 - fy1),
        },
    )


def compute_safe_crop_bbox(
    page: PageImage,
    region: ProblemRegion,
    config: CropConfig,
) -> tuple[BBox, dict[str, Any]]:
    """Expand a detected region with asymmetric safety margins."""

    width = region.width
    height = region.height

    if region.source == "pdf_text_numbering":
        left_pad = max(4, int(round(width * 0.012)))
        right_pad = max(4, int(round(width * 0.014)))
        top_pad = max(3, int(round(height * 0.020)))
        bottom_pad = max(5, int(round(height * 0.028)))
        expanded_bbox = clamp_bbox(
            (
                region.bbox[0] - left_pad,
                region.bbox[1] - top_pad,
                region.bbox[2] + right_pad,
                region.bbox[3] + bottom_pad,
            ),
            width=page.width,
            height=page.height,
        )
        return expanded_bbox, {
            "shape_type": "text_numbered",
            "estimated_fill_ratio": 0.35,
            "sparse_problem": False,
            "safe_padding": (left_pad, top_pad, right_pad, bottom_pad),
        }

    shape_type = classify_problem_shape(width, height, preserve_round_shapes=config.preserve_round_shapes)

    fill_ratio = float(region.metadata.get("fill_ratio", 0.08))
    sparse_problem = fill_ratio < 0.12 or int(region.metadata.get("source_count", 1)) > 1

    left_pad = bounded_padding(width, config.expand_left_ratio, config)
    right_pad = bounded_padding(width, config.expand_right_ratio, config)
    top_pad = bounded_padding(height, config.expand_top_ratio, config)
    bottom_pad = bounded_padding(height, config.expand_bottom_ratio, config)

    if shape_type == "tall":
        top_pad = int(round(top_pad * 1.08))
        bottom_pad = int(round(bottom_pad * config.tall_problem_height_bias))
    elif shape_type == "wide":
        left_pad = int(round(left_pad * config.wide_problem_width_bias))
        right_pad = int(round(right_pad * config.wide_problem_width_bias))
    elif shape_type == "round":
        round_boost = 1.10 if config.preserve_round_shapes else 1.0
        left_pad = int(round(left_pad * round_boost))
        right_pad = int(round(right_pad * round_boost))
        top_pad = int(round(top_pad * round_boost))
        bottom_pad = int(round(bottom_pad * round_boost))

    if sparse_problem:
        extra = max(config.min_padding_px, int(round(max(width, height) * config.sparse_extra_margin_ratio)))
        left_pad += extra
        right_pad += extra
        top_pad += int(round(extra * 0.8))
        bottom_pad += int(round(extra * 1.15))

    expanded_bbox = clamp_bbox(
        (
            region.bbox[0] - left_pad,
            region.bbox[1] - top_pad,
            region.bbox[2] + right_pad,
            region.bbox[3] + bottom_pad,
        ),
        width=page.width,
        height=page.height,
    )

    return expanded_bbox, {
        "shape_type": shape_type,
        "estimated_fill_ratio": fill_ratio,
        "sparse_problem": sparse_problem,
        "safe_padding": (left_pad, top_pad, right_pad, bottom_pad),
    }


def compute_trim_padding(
    *,
    content_width: int,
    content_height: int,
    crop_width: int,
    crop_height: int,
    config: CropConfig,
    shape_type: str,
    fill_ratio: float,
) -> tuple[int, int, int, int]:
    """Compute conservative padding retained after whitespace trimming."""

    base_x = max(config.min_padding_px, int(round(content_width * 0.04)))
    base_y = max(config.min_padding_px, int(round(content_height * 0.04)))

    if shape_type == "tall":
        top_pad = int(round(base_y * 1.05))
        bottom_pad = int(round(base_y * config.tall_problem_height_bias))
        left_pad = right_pad = int(round(base_x * 0.95))
    elif shape_type == "wide":
        left_pad = right_pad = int(round(base_x * config.wide_problem_width_bias))
        top_pad = int(round(base_y * 0.95))
        bottom_pad = int(round(base_y * 1.10))
    elif shape_type == "round":
        left_pad = right_pad = int(round(base_x * 1.12))
        top_pad = bottom_pad = int(round(base_y * 1.12))
    else:
        left_pad = right_pad = base_x
        top_pad = base_y
        bottom_pad = int(round(base_y * 1.12))

    if fill_ratio < 0.10:
        extra = max(2, int(round(max(content_width, content_height) * config.sparse_extra_margin_ratio)))
        left_pad += extra
        right_pad += extra
        top_pad += int(round(extra * 0.75))
        bottom_pad += int(round(extra * 1.15))

    horizontal_limit = max(config.min_padding_px, int(round(crop_width * config.max_padding_ratio)))
    vertical_limit = max(config.min_padding_px, int(round(crop_height * config.max_padding_ratio)))

    return (
        min(left_pad, horizontal_limit),
        min(top_pad, vertical_limit),
        min(right_pad, horizontal_limit),
        min(bottom_pad, vertical_limit),
    )


def bounded_padding(reference_size: int, ratio: float, config: CropConfig) -> int:
    """Return a padding value bounded by the configured minimum and maximum ratios."""

    minimum = int(config.min_padding_px)
    maximum = max(minimum, int(round(reference_size * config.max_padding_ratio)))
    computed = max(minimum, int(round(reference_size * ratio)))
    return min(computed, maximum)


def should_keep_original_crop(*, full_size: tuple[int, int], trimmed_bbox: BBox) -> bool:
    """Avoid re-cropping when trimming would produce a negligible improvement."""

    full_width, full_height = full_size
    full_area = full_width * full_height
    trimmed_area = max(1, (trimmed_bbox[2] - trimmed_bbox[0]) * (trimmed_bbox[3] - trimmed_bbox[1]))
    removed_ratio = 1.0 - (trimmed_area / max(full_area, 1))
    return removed_ratio < 0.03


def clamp_relative_bbox(bbox: BBox, *, width: int, height: int) -> BBox:
    """Clamp a bbox inside a local crop coordinate system."""

    x1, y1, x2, y2 = bbox
    x1 = max(0, min(int(x1), width - 1))
    y1 = max(0, min(int(y1), height - 1))
    x2 = max(x1 + 1, min(int(x2), width))
    y2 = max(y1 + 1, min(int(y2), height))
    return (x1, y1, x2, y2)


def classify_problem_shape(width: int, height: int, *, preserve_round_shapes: bool) -> str:
    """Classify a problem crop for aspect-sensitive trimming and layout."""

    aspect_ratio = width / max(height, 1)
    if aspect_ratio <= 0.72:
        return "tall"
    if aspect_ratio >= 1.60:
        return "wide"
    if preserve_round_shapes and 0.82 <= aspect_ratio <= 1.22:
        return "round"
    return "standard"


def to_grayscale(image: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """Convert a crop to grayscale."""

    if image.ndim == 2:
        return image.copy()
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
