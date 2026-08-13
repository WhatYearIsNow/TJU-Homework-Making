"""Candidate problem region detection based on traditional vision methods."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

import cv2
import numpy as np

from app.config import DetectionConfig
from app.io_utils import import_pymupdf
from app.models import BBox, DetectedGrid, PageImage, ProblemRegion
from app.preprocess import PreprocessArtifacts, preprocess_page
from app.refine_regions import refine_problem_regions

QUESTION_LINE_PATTERN = re.compile(r"^\s*(\d{1,3})\s*[\.\uFF0E\u3001\)]\s*")


@dataclass(slots=True)
class DetectionResult:
    """Detection output for a single page."""

    page: PageImage
    artifacts: PreprocessArtifacts
    candidate_regions: list[ProblemRegion]
    refined_regions: list[ProblemRegion]
    page_grid: DetectedGrid | None = None


def detect_problem_regions(page: PageImage, config: DetectionConfig) -> DetectionResult:
    """Run preprocess, candidate extraction, page-grid detection, and refinement."""

    pdf_numbered_regions = detect_numbered_pdf_regions(page, config)
    artifacts = preprocess_page(page, config)
    if pdf_numbered_regions:
        candidate_regions = pdf_numbered_regions
        refined_regions = pdf_numbered_regions
        page_grid = None
        return DetectionResult(
            page=page,
            artifacts=artifacts,
            candidate_regions=candidate_regions,
            refined_regions=refined_regions,
            page_grid=page_grid,
        )

    is_pdf_source = str(page.metadata.get("source_type", "")).lower() == "pdf"
    if (
        config.strict_single_question_mode
        and config.strict_projection_fallback_enabled
        and not is_pdf_source
    ):
        projection_regions = detect_projection_question_bands(page, artifacts, config)
        if projection_regions:
            return DetectionResult(
                page=page,
                artifacts=artifacts,
                candidate_regions=projection_regions,
                refined_regions=projection_regions,
                page_grid=None,
            )

    candidate_regions = extract_candidate_regions(page, artifacts, config)
    page_grid = detect_page_grid(page, artifacts, config)
    refined_regions = refine_problem_regions(
        candidate_regions,
        page_size=(page.width, page.height),
        config=config,
        page_grid=page_grid,
    )
    return DetectionResult(
        page=page,
        artifacts=artifacts,
        candidate_regions=candidate_regions,
        refined_regions=refined_regions,
        page_grid=page_grid,
    )


def detect_numbered_pdf_regions(page: PageImage, config: DetectionConfig) -> list[ProblemRegion]:
    """Infer question regions by numbered text lines for selectable-text PDF pages."""

    if not config.enable_pdf_text_number_detection:
        return []
    if str(page.metadata.get("source_type", "")).lower() != "pdf":
        return []
    if page.source_path.suffix.lower() != ".pdf":
        return []

    fitz = import_pymupdf()
    pdf_page_index = int(page.metadata.get("input_page_index", page.metadata.get("document_page_number", 1) - 1))
    if pdf_page_index < 0:
        return []

    try:
        with fitz.open(str(page.source_path)) as document:
            if pdf_page_index >= document.page_count:
                return []
            pdf_page = document.load_page(pdf_page_index)
            pdf_width = float(pdf_page.rect.width)
            pdf_height = float(pdf_page.rect.height)
            line_items = extract_pdf_text_lines(pdf_page)
    except Exception:
        return []

    spans = infer_numbered_question_spans(
        line_items,
        min_questions=config.pdf_text_min_questions,
        max_anchor_step=config.pdf_text_anchor_max_step,
    )
    if not spans:
        return []

    x_scale = page.width / max(pdf_width, 1.0)
    y_scale = page.height / max(pdf_height, 1.0)
    side_padding = max(4.0, pdf_width * config.pdf_text_side_margin_ratio)
    top_padding = max(2.0, pdf_height * config.pdf_text_top_padding_ratio)
    bottom_padding = max(4.0, pdf_height * config.pdf_text_bottom_padding_ratio)

    regions: list[ProblemRegion] = []
    for span_index, span in enumerate(spans):
        sx1, sy1, sx2, sy2 = span["bbox"]
        x1_pdf = max(0.0, sx1 - side_padding)
        y1_pdf = max(0.0, sy1 - top_padding)
        x2_pdf = min(pdf_width, sx2 + side_padding)
        y2_pdf = min(pdf_height, sy2 + bottom_padding)
        if span_index + 1 < len(spans):
            next_start_y = float(spans[span_index + 1]["bbox"][1])
            y2_pdf = min(y2_pdf, max(y1_pdf + 1.0, next_start_y - 2.0))
        if span_index > 0 and regions:
            prev_y2_px = float(regions[-1].bbox[3]) / max(y_scale, 1e-6)
            y1_pdf = max(y1_pdf, min(y2_pdf - 1.0, prev_y2_px + 1.0))

        bbox = (
            max(0, int(round(x1_pdf * x_scale))),
            max(0, int(round(y1_pdf * y_scale))),
            min(page.width, int(round(x2_pdf * x_scale))),
            min(page.height, int(round(y2_pdf * y_scale))),
        )
        next_start_y_px = None
        if span_index + 1 < len(spans):
            next_start_y_px = int(round(float(spans[span_index + 1]["bbox"][1]) * y_scale))
        bbox = trim_sparse_bottom_noise_rows(
            page.image,
            bbox,
            threshold=config.pdf_text_bottom_trim_threshold,
            next_start_y_px=next_start_y_px,
        )
        if bbox[2] - bbox[0] < 8 or bbox[3] - bbox[1] < 8:
            continue

        regions.append(
            ProblemRegion(
                page_index=page.page_index,
                bbox=bbox,
                score=0.95,
                source="pdf_text_numbering",
                metadata={
                    "question_number": span["number"],
                    "line_count": span["line_count"],
                },
            )
        )

    regions.sort(key=lambda item: (item.bbox[1], item.bbox[0]))
    return regions


def extract_pdf_text_lines(pdf_page: Any) -> list[dict[str, Any]]:
    """Extract flattened text lines with bboxes from a PyMuPDF page dict."""

    text_dict = pdf_page.get_text("dict")
    lines: list[dict[str, Any]] = []
    for block in text_dict.get("blocks", []):
        if int(block.get("type", -1)) != 0:
            continue
        for line in block.get("lines", []):
            raw_bbox = line.get("bbox")
            if not raw_bbox or len(raw_bbox) != 4:
                continue
            spans = line.get("spans", [])
            text = "".join(str(span.get("text", "")) for span in spans).strip()
            if not text:
                continue
            x1, y1, x2, y2 = [float(value) for value in raw_bbox]
            lines.append(
                {
                    "text": text,
                    "bbox": (x1, y1, x2, y2),
                }
            )
    lines.sort(key=lambda item: (item["bbox"][1], item["bbox"][0]))
    return lines


def infer_numbered_question_spans(
    line_items: list[dict[str, Any]],
    *,
    min_questions: int,
    max_anchor_step: int = 3,
) -> list[dict[str, Any]]:
    """Group PDF lines into question spans based on numbered line anchors."""

    if not line_items:
        return []

    anchors: list[dict[str, Any]] = []
    for index, item in enumerate(line_items):
        matched = QUESTION_LINE_PATTERN.match(str(item["text"]))
        if matched is None:
            continue
        number = int(matched.group(1))
        anchors.append(
            {
                "index": index,
                "number": number,
                "y": float(item["bbox"][1]),
            }
        )
    if len(anchors) < min_questions:
        return []

    anchors.sort(key=lambda item: item["y"])
    filtered = filter_number_anchors(anchors, max_anchor_step=max_anchor_step)
    if len(filtered) < min_questions:
        return []

    page_bottom = max(float(item["bbox"][3]) for item in line_items)

    spans: list[dict[str, Any]] = []
    for anchor_position, anchor in enumerate(filtered):
        number = int(anchor["number"])
        start_y = float(anchor["y"])
        end_y = page_bottom + 1.0
        if anchor_position + 1 < len(filtered):
            end_y = float(filtered[anchor_position + 1]["y"]) - 1.0

        segment_items = [
            item
            for item in line_items
            if float(item["bbox"][1]) >= start_y and float(item["bbox"][1]) <= end_y
        ]
        if not segment_items:
            continue
        x1 = min(float(item["bbox"][0]) for item in segment_items)
        y1 = min(float(item["bbox"][1]) for item in segment_items)
        x2 = max(float(item["bbox"][2]) for item in segment_items)
        y2 = max(float(item["bbox"][3]) for item in segment_items)
        spans.append(
            {
                "number": number,
                "line_count": len(segment_items),
                "bbox": (x1, y1, x2, y2),
            }
        )
    return spans


def filter_number_anchors(
    anchors: list[dict[str, Any]],
    *,
    max_anchor_step: int,
) -> list[dict[str, Any]]:
    """Filter anchors by monotonicity and reasonable number increments."""

    if not anchors:
        return []

    filtered: list[dict[str, Any]] = [anchors[0]]
    for anchor in anchors[1:]:
        previous = filtered[-1]
        number = int(anchor["number"])
        previous_number = int(previous["number"])
        y = float(anchor["y"])
        previous_y = float(previous["y"])

        if y <= previous_y:
            continue
        if number < previous_number:
            continue
        if previous_number >= 1 and number - previous_number > max(1, max_anchor_step):
            continue
        if number == previous_number and (y - previous_y) < 10.0:
            continue
        filtered.append(anchor)
    return filtered


def trim_sparse_bottom_noise_rows(
    image: np.ndarray | None,
    bbox: BBox,
    *,
    threshold: int,
    next_start_y_px: int | None,
) -> BBox:
    """Trim sparse trailing rows from a text bbox bottom to reduce question bleed."""

    if image is None:
        return bbox

    x1, y1, x2, y2 = bbox
    if x2 - x1 <= 8 or y2 - y1 <= 8:
        return bbox

    crop = image[y1:y2, x1:x2]
    if crop.size == 0:
        return bbox
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    foreground = gray < int(threshold)
    if not np.any(foreground):
        return bbox

    row_density = foreground.sum(axis=1) / max(crop.shape[1], 1)
    positive_density = row_density[row_density > 0.0]
    if positive_density.size == 0:
        return bbox
    base_threshold = float(np.percentile(positive_density, 25)) * 0.55
    dense_threshold = max(0.012, min(0.12, base_threshold))
    dense_rows = np.where(row_density >= dense_threshold)[0]
    if dense_rows.size == 0:
        return bbox

    # Split dense rows into clusters; trim isolated tiny tail clusters as noise.
    clusters: list[np.ndarray] = []
    start = 0
    for idx in range(1, len(dense_rows)):
        if dense_rows[idx] - dense_rows[idx - 1] > 6:
            clusters.append(dense_rows[start:idx])
            start = idx
    clusters.append(dense_rows[start:])

    last_cluster = clusters[-1]
    if len(clusters) >= 2:
        previous_cluster = clusters[-2]
        gap = int(last_cluster[0] - previous_cluster[-1])
        if len(last_cluster) <= 3 and gap >= max(12, int(round(crop.shape[0] * 0.05))):
            last_cluster = previous_cluster

    last_dense = int(last_cluster[-1])
    tail_padding = max(3, int(round(crop.shape[0] * 0.010)))
    new_y2 = y1 + min(crop.shape[0], last_dense + 1 + tail_padding)
    if next_start_y_px is not None:
        new_y2 = min(new_y2, max(y1 + 8, next_start_y_px - 6))
    if new_y2 <= y1 + 8:
        return bbox
    return (x1, y1, x2, new_y2)


def detect_projection_question_bands(
    page: PageImage,
    artifacts: PreprocessArtifacts,
    config: DetectionConfig,
) -> list[ProblemRegion]:
    """Detect question bands by horizontal projection as strict-mode fallback."""

    mask = artifacts.foreground
    page_height, page_width = mask.shape[:2]
    if page_height <= 0 or page_width <= 0:
        return []

    row_density = np.count_nonzero(mask, axis=1) / max(page_width, 1)
    active_rows = np.where(row_density >= float(config.strict_projection_row_density_threshold))[0]
    if active_rows.size == 0:
        return []

    gap_px = max(6, int(round(page_height * config.strict_projection_gap_ratio)))
    spans = group_active_rows(active_rows.tolist(), max_gap=gap_px)
    min_band_height = max(12, int(round(page_height * config.strict_projection_min_band_height_ratio)))
    side_padding = max(6, int(round(page_width * config.strict_projection_side_padding_ratio)))
    vertical_padding = max(4, int(round(page_height * config.strict_projection_vertical_padding_ratio)))

    regions: list[ProblemRegion] = []
    for start_y, end_y in spans:
        if end_y - start_y < min_band_height:
            continue
        band = mask[start_y:end_y, :]
        col_nonzero = np.where(np.count_nonzero(band, axis=0) > 0)[0]
        if col_nonzero.size == 0:
            continue
        x1 = max(0, int(col_nonzero[0]) - side_padding)
        x2 = min(page_width, int(col_nonzero[-1]) + 1 + side_padding)
        y1 = max(0, start_y - vertical_padding)
        y2 = min(page_height, end_y + vertical_padding)
        if x2 - x1 < 8 or y2 - y1 < 8:
            continue
        regions.append(
            ProblemRegion(
                page_index=page.page_index,
                bbox=(x1, y1, x2, y2),
                score=0.78,
                source="strict_projection_fallback",
                metadata={"band_height": y2 - y1},
            )
        )

    if len(regions) < int(config.strict_projection_min_bands):
        return []
    return regions


def group_active_rows(active_rows: list[int], *, max_gap: int) -> list[tuple[int, int]]:
    """Group active row indices into continuous y-band spans."""

    if not active_rows:
        return []

    groups: list[list[int]] = [[active_rows[0]]]
    for value in active_rows[1:]:
        if value - groups[-1][-1] <= max_gap:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [(group[0], group[-1] + 1) for group in groups if group]


def extract_candidate_regions(
    page: PageImage,
    artifacts: PreprocessArtifacts,
    config: DetectionConfig,
) -> list[ProblemRegion]:
    """Extract raw candidate regions from a closed foreground mask."""

    mask = artifacts.closed
    num_labels, _, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    page_area = page.width * page.height

    min_area = max(1, int(page_area * config.min_region_area_ratio))
    min_width = max(1, int(page.width * config.min_region_width_ratio))
    min_height = max(1, int(page.height * config.min_region_height_ratio))

    regions: list[ProblemRegion] = []
    for label in range(1, num_labels):
        x = int(stats[label, cv2.CC_STAT_LEFT])
        y = int(stats[label, cv2.CC_STAT_TOP])
        width = int(stats[label, cv2.CC_STAT_WIDTH])
        height = int(stats[label, cv2.CC_STAT_HEIGHT])
        component_area = int(stats[label, cv2.CC_STAT_AREA])

        bbox = (x, y, x + width, y + height)
        metrics = describe_component(width, height, component_area, page_area)
        if not passes_component_filters(
            width=width,
            height=height,
            component_area=component_area,
            min_area=min_area,
            min_width=min_width,
            min_height=min_height,
            max_noise_aspect_ratio=config.max_noise_aspect_ratio,
        ):
            continue

        score = estimate_region_score(metrics)
        regions.append(
            ProblemRegion(
                page_index=page.page_index,
                bbox=bbox,
                score=score,
                source="connected_component",
                metadata={
                    "component_label": label,
                    "component_area": component_area,
                    "bbox_area": width * height,
                    "fill_ratio": metrics["fill_ratio"],
                    "aspect_ratio": metrics["aspect_ratio"],
                    "centroid": (
                        float(centroids[label][0]),
                        float(centroids[label][1]),
                    ),
                },
            )
        )

    regions.sort(key=lambda region: (region.bbox[1], region.bbox[0]))
    return regions


def detect_page_grid(
    page: PageImage,
    artifacts: PreprocessArtifacts,
    config: DetectionConfig,
) -> DetectedGrid | None:
    """Detect a stable 2x2 page grid using long horizontal and vertical lines."""

    if not config.enable_grid_detection:
        return None

    horizontal_positions = detect_line_positions(
        artifacts.foreground,
        axis="horizontal",
        kernel_ratio=config.grid_line_kernel_ratio,
        projection_threshold_ratio=config.grid_projection_threshold_ratio,
        merge_gap_px=config.grid_line_merge_gap_px,
        support_span_ratio=config.grid_support_span_ratio,
    )
    vertical_positions = detect_line_positions(
        artifacts.foreground,
        axis="vertical",
        kernel_ratio=config.grid_line_kernel_ratio,
        projection_threshold_ratio=config.grid_projection_threshold_ratio,
        merge_gap_px=config.grid_line_merge_gap_px,
        support_span_ratio=config.grid_support_span_ratio,
    )

    horizontal_triplet = choose_reference_lines(
        horizontal_positions,
        axis_length=page.height,
        center_tolerance_ratio=config.grid_center_tolerance_ratio,
        outer_margin_ratio=config.grid_outer_margin_ratio,
    )
    vertical_triplet = choose_reference_lines(
        vertical_positions,
        axis_length=page.width,
        center_tolerance_ratio=config.grid_center_tolerance_ratio,
        outer_margin_ratio=config.grid_outer_margin_ratio,
    )
    if horizontal_triplet is None or vertical_triplet is None:
        return None

    outer_bbox = (
        int(vertical_triplet[0]),
        int(horizontal_triplet[0]),
        int(vertical_triplet[2]),
        int(horizontal_triplet[2]),
    )
    return DetectedGrid(
        page_index=page.page_index,
        outer_bbox=outer_bbox,
        vertical_lines=vertical_triplet,
        horizontal_lines=horizontal_triplet,
        metadata={
            "horizontal_positions": horizontal_positions,
            "vertical_positions": vertical_positions,
        },
    )


def detect_line_positions(
    mask: np.ndarray,
    *,
    axis: str,
    kernel_ratio: float,
    projection_threshold_ratio: float,
    merge_gap_px: int,
    support_span_ratio: float,
) -> list[int]:
    """Detect stable long-line positions along one axis."""

    height, width = mask.shape[:2]
    if axis == "horizontal":
        kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT,
            (max(25, int(width * kernel_ratio)), 1),
        )
        opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        support = np.count_nonzero(opened, axis=1) / max(width, 1)
    elif axis == "vertical":
        kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT,
            (1, max(25, int(height * kernel_ratio))),
        )
        opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        support = np.count_nonzero(opened, axis=0) / max(height, 1)
    else:
        raise ValueError("axis must be 'horizontal' or 'vertical'")

    indices = np.where(support >= max(projection_threshold_ratio, support_span_ratio))[0]
    if indices.size == 0:
        return []
    return collapse_line_indices(indices.tolist(), merge_gap_px)


def collapse_line_indices(indices: list[int], merge_gap_px: int) -> list[int]:
    """Collapse neighboring line pixels into a single center position."""

    if not indices:
        return []

    groups: list[list[int]] = [[indices[0]]]
    for value in indices[1:]:
        if value - groups[-1][-1] <= merge_gap_px:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [int(round(sum(group) / len(group))) for group in groups]


def choose_reference_lines(
    positions: list[int],
    *,
    axis_length: int,
    center_tolerance_ratio: float,
    outer_margin_ratio: float,
) -> tuple[int, int, int] | None:
    """Choose outer-top/bottom and center line positions for a 2x2 page grid."""

    if len(positions) < 3:
        return None

    center = axis_length / 2.0
    center_tolerance = axis_length * center_tolerance_ratio
    outer_limit = axis_length * outer_margin_ratio

    outer_candidates = [value for value in positions if value <= outer_limit]
    center_candidates = [value for value in positions if abs(value - center) <= center_tolerance]
    far_candidates = [value for value in positions if value >= axis_length - outer_limit]
    if not outer_candidates or not center_candidates or not far_candidates:
        return None

    first = outer_candidates[0]
    middle = min(center_candidates, key=lambda value: abs(value - center))
    last = far_candidates[-1]
    if not (first < middle < last):
        return None
    return (int(first), int(middle), int(last))


def passes_component_filters(
    *,
    width: int,
    height: int,
    component_area: int,
    min_area: int,
    min_width: int,
    min_height: int,
    max_noise_aspect_ratio: float,
) -> bool:
    """Filter out tiny or obviously noisy connected components."""

    if width <= 0 or height <= 0 or component_area <= 0:
        return False

    bbox_area = width * height
    aspect_ratio = max(width / max(height, 1), height / max(width, 1))
    fill_ratio = component_area / max(bbox_area, 1)

    too_small = component_area < min_area and (width < min_width or height < min_height)
    if too_small:
        return False

    extremely_slender_noise = aspect_ratio > max_noise_aspect_ratio and component_area < min_area * 4
    if extremely_slender_noise:
        return False

    sparse_tiny_noise = fill_ratio < 0.015 and component_area < min_area * 2
    if sparse_tiny_noise:
        return False

    return True


def describe_component(width: int, height: int, component_area: int, page_area: int) -> dict[str, Any]:
    """Return a compact metric dictionary for a connected component."""

    bbox_area = max(width * height, 1)
    return {
        "bbox_area": bbox_area,
        "fill_ratio": component_area / bbox_area,
        "aspect_ratio": width / max(height, 1),
        "page_area_ratio": component_area / max(page_area, 1),
    }


def estimate_region_score(metrics: dict[str, Any]) -> float:
    """Estimate a loose confidence score for downstream prioritization."""

    area_term = min(1.0, float(metrics["page_area_ratio"]) * 28.0)
    fill_term = min(1.0, float(metrics["fill_ratio"]) * 1.4)
    score = 0.65 * area_term + 0.35 * fill_term
    return max(0.05, min(1.0, score))
