"""Template-driven layout inference for matching user-supplied reference pages."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from app.config import AppConfig
from app.detect_regions import detect_line_positions
from app.io_utils import is_pdf_path, load_image_page, load_pdf_pages
from app.layout_engine import resolve_page_size
from app.models import LayoutTemplateSpec, PageImage
from app.preprocess import preprocess_page


def resolve_layout_template(config: AppConfig) -> LayoutTemplateSpec | None:
    """Resolve an optional user-supplied layout template into page geometry."""

    template_path = config.layout.template_path.strip()
    if not template_path:
        return None

    page = load_template_page(Path(template_path), config)
    spec = infer_layout_template(page, config)
    if spec is None:
        raise RuntimeError(f"Unable to infer a stable layout from template: {page.source_path}")
    return spec


def load_template_page(path: Path, config: AppConfig) -> PageImage:
    """Load the first page of a template PDF/image at the configured template DPI."""

    template_path = path.expanduser().resolve()
    if is_pdf_path(template_path, config.io):
        pages = load_pdf_pages(template_path, config.layout.template_dpi)
        if not pages:
            raise RuntimeError(f"Template PDF contains no pages: {template_path}")
        page = pages[0]
        page.page_index = 0
        return page
    return load_image_page(template_path, config.layout.template_dpi)


def infer_layout_template(page: PageImage, config: AppConfig) -> LayoutTemplateSpec | None:
    """Infer the outer frame, header band, and grid boundaries from one template page."""

    artifacts = preprocess_page(page, config.detection)
    horizontal_positions = detect_line_positions(
        artifacts.foreground,
        axis="horizontal",
        kernel_ratio=max(0.22, config.detection.grid_line_kernel_ratio * 0.75),
        projection_threshold_ratio=max(0.34, config.detection.grid_projection_threshold_ratio * 0.8),
        merge_gap_px=config.detection.grid_line_merge_gap_px,
        support_span_ratio=max(0.45, config.detection.grid_support_span_ratio * 0.85),
    )
    vertical_positions = detect_line_positions(
        artifacts.foreground,
        axis="vertical",
        kernel_ratio=max(0.22, config.detection.grid_line_kernel_ratio * 0.75),
        projection_threshold_ratio=max(0.34, config.detection.grid_projection_threshold_ratio * 0.8),
        merge_gap_px=config.detection.grid_line_merge_gap_px,
        support_span_ratio=max(0.45, config.detection.grid_support_span_ratio * 0.85),
    )

    horizontal_boundaries = select_template_boundaries(horizontal_positions, axis_length=page.height)
    vertical_boundaries = select_template_boundaries(vertical_positions, axis_length=page.width)
    if len(horizontal_boundaries) < 2 or len(vertical_boundaries) < 2:
        return None

    page_size_pt = resolve_page_size(config.layout.page_size)
    x_scale = page_size_pt[0] / max(page.width, 1)
    y_scale = page_size_pt[1] / max(page.height, 1)

    column_boundaries = tuple(sorted(value * x_scale for value in vertical_boundaries))
    row_boundaries = tuple(sorted(page_size_pt[1] - (value * y_scale) for value in horizontal_boundaries))
    outer_frame = (
        column_boundaries[0],
        row_boundaries[0],
        column_boundaries[-1],
        row_boundaries[-1],
    )
    header_frame = infer_header_frame(
        artifacts.foreground,
        page_size_pt=page_size_pt,
        page_width_px=page.width,
        page_height_px=page.height,
        outer_left_px=vertical_boundaries[0],
        outer_right_px=vertical_boundaries[-1],
        outer_top_px=horizontal_boundaries[0],
        default_gap_pt=config.layout.header_text_gap_pt,
    )

    return LayoutTemplateSpec(
        source_path=page.source_path,
        page_size_pt=page_size_pt,
        outer_frame=outer_frame,
        header_frame=header_frame,
        column_boundaries=column_boundaries,
        row_boundaries=row_boundaries,
        metadata={
            "horizontal_boundaries_px": horizontal_boundaries,
            "vertical_boundaries_px": vertical_boundaries,
            "template_page_name": page.source_path.name,
        },
    )


def select_template_boundaries(positions: list[int], *, axis_length: int) -> list[int]:
    """Select usable grid boundaries from long-line detector output."""

    if len(positions) < 2:
        return []

    lower_limit = axis_length * 0.03
    upper_limit = axis_length * 0.97
    usable = [value for value in positions if lower_limit <= value <= upper_limit]
    if len(usable) < 2:
        usable = list(positions)

    min_gap = max(12, int(axis_length * 0.055))
    filtered: list[int] = [usable[0]]
    for value in usable[1:]:
        if value - filtered[-1] >= min_gap:
            filtered.append(value)

    if len(filtered) < 2:
        return [usable[0], usable[-1]]
    return filtered


def infer_header_frame(
    foreground: np.ndarray,
    *,
    page_size_pt: tuple[float, float],
    page_width_px: int,
    page_height_px: int,
    outer_left_px: int,
    outer_right_px: int,
    outer_top_px: int,
    default_gap_pt: float,
) -> tuple[float, float, float, float]:
    """Infer a usable header text band above the detected outer frame."""

    page_width_pt, page_height_pt = page_size_pt
    x1_pt = page_width_pt * (outer_left_px / max(page_width_px, 1))
    x2_pt = page_width_pt * (outer_right_px / max(page_width_px, 1))
    outer_top_pt = page_height_pt - (outer_top_px / max(page_height_px, 1)) * page_height_pt

    band = foreground[: max(outer_top_px, 1), outer_left_px:outer_right_px]
    ys, xs = np.where(band > 0)
    if ys.size == 0:
        baseline_y = outer_top_pt + default_gap_pt
        return (x1_pt, baseline_y, x2_pt, min(page_height_pt - 8.0, baseline_y + 24.0))

    text_top_px = int(ys.min())
    text_bottom_px = int(ys.max())
    baseline_y = page_height_pt - (text_bottom_px / max(page_height_px, 1)) * page_height_pt
    top_y = page_height_pt - (max(0, text_top_px - 6) / max(page_height_px, 1)) * page_height_pt
    return (x1_pt, baseline_y, x2_pt, max(baseline_y + 6.0, top_y))
