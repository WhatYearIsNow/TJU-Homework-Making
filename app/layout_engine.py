"""Fixed-layout pagination and placement for cropped problems."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil
from typing import Any

from app.config import LayoutConfig
from app.models import CroppedProblem, FloatBBox, LayoutCell, LayoutTemplateSpec

A4_PORTRAIT_PT: tuple[float, float] = (595.2756, 841.8898)
ROW_PATTERN_PRESETS: dict[int, dict[str, list[int]]] = {
    2: {
        "vertical": [1, 1],
        "horizontal": [2],
    },
    3: {
        "top1_bottom2": [1, 2],
        "top2_bottom1": [2, 1],
        "three_rows": [1, 1, 1],
        "horizontal": [3],
    },
}


@dataclass(slots=True)
class GridFrames:
    """Page frame geometry used by the renderer."""

    page_number: int
    page_size_pt: tuple[float, float]
    outer_frame: FloatBBox
    header_frame: FloatBBox
    body_frame: FloatBBox
    cells: list[LayoutCell]


@dataclass(slots=True)
class ProblemPlacement:
    """Placement of one cropped problem inside a grid cell."""

    page_number: int
    cell: LayoutCell
    problem: CroppedProblem | None
    image_frame: FloatBBox | None
    writing_frame: FloatBBox
    scale: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PageLayout:
    """Fully paginated page containing grid geometry and placements."""

    page_number: int
    frames: GridFrames
    placements: list[ProblemPlacement]


def paginate_problems(
    problems: list[CroppedProblem],
    config: LayoutConfig,
    *,
    template_spec: LayoutTemplateSpec | None = None,
) -> list[PageLayout]:
    """Paginate cropped problems into a resolved page grid while preserving blank cells."""

    page_capacity = resolve_page_capacity(config, template_spec=template_spec)
    if page_capacity <= 0:
        raise ValueError("Layout must contain at least one slot per page.")
    if not problems:
        return []

    page_count = ceil(len(problems) / page_capacity)
    layouts: list[PageLayout] = []

    for page_number in range(1, page_count + 1):
        frames = compute_grid_frames(config, page_number=page_number, template_spec=template_spec)
        page_slice = problems[(page_number - 1) * page_capacity : page_number * page_capacity]
        placements: list[ProblemPlacement] = []

        for cell_index, cell in enumerate(frames.cells):
            problem = page_slice[cell_index] if cell_index < len(page_slice) else None
            if problem is not None:
                cell.problem_id = problem.problem_id
                placements.append(fit_problem_to_cell(problem, cell, config))
            else:
                placements.append(
                    ProblemPlacement(
                        page_number=page_number,
                        cell=cell,
                        problem=None,
                        image_frame=None,
                        writing_frame=cell.content_frame,
                        scale=1.0,
                        metadata={"blank_cell": True},
                    )
                )

        layouts.append(PageLayout(page_number=page_number, frames=frames, placements=placements))

    return layouts


def compute_grid_frames(
    config: LayoutConfig,
    *,
    page_number: int = 1,
    template_spec: LayoutTemplateSpec | None = None,
) -> GridFrames:
    """Compute page geometry from either the built-in layout or an imported template."""

    if template_spec is not None and config.template_lock_per_page:
        return compute_template_grid_frames(template_spec, config, page_number=page_number)
    if template_spec is not None:
        return compute_template_style_grid_frames(template_spec, config, page_number=page_number)
    return compute_auto_grid_frames(config, page_number=page_number)


def compute_auto_grid_frames(config: LayoutConfig, *, page_number: int = 1) -> GridFrames:
    """Compute page frames for a resolved auto-generated row pattern."""

    page_width, page_height = resolve_page_size(config.page_size)
    outer_frame = (
        float(config.page_margin_left_pt),
        float(config.page_margin_bottom_pt),
        float(page_width - config.page_margin_right_pt),
        float(page_height - config.page_margin_top_pt),
    )

    header_baseline_y = outer_frame[3] + float(config.header_text_gap_pt)
    header_frame = (
        outer_frame[0],
        header_baseline_y,
        outer_frame[2],
        min(page_height - 12.0, header_baseline_y + 24.0),
    )
    row_patterns = resolve_row_patterns(config)
    cells = build_cells_from_row_patterns(
        outer_frame,
        row_patterns,
        page_number=page_number,
        cell_padding_pt=config.cell_padding_pt,
        page_capacity=config.problems_per_page,
        blank_space_ratio_target=select_reserved_write_ratio(config.problems_per_page, config),
    )
    return GridFrames(
        page_number=page_number,
        page_size_pt=(page_width, page_height),
        outer_frame=outer_frame,
        header_frame=header_frame,
        body_frame=outer_frame,
        cells=cells,
    )


def compute_template_grid_frames(
    template_spec: LayoutTemplateSpec,
    config: LayoutConfig,
    *,
    page_number: int = 1,
) -> GridFrames:
    """Compute page frames directly from a user-imported template."""

    cells: list[LayoutCell] = []
    page_capacity = template_spec.problems_per_page
    reserved_write_ratio = select_reserved_write_ratio(page_capacity, config)
    cell_index = 0

    for row in range(template_spec.rows):
        row_bottom = template_spec.row_boundaries[template_spec.rows - row - 1]
        row_top = template_spec.row_boundaries[template_spec.rows - row]
        for column in range(template_spec.columns):
            frame = (
                template_spec.column_boundaries[column],
                row_bottom,
                template_spec.column_boundaries[column + 1],
                row_top,
            )
            cells.append(
                LayoutCell(
                    page_number=page_number,
                    cell_index=cell_index,
                    row=row,
                    column=column,
                    frame=frame,
                    content_frame=inset_frame(frame, config.cell_padding_pt),
                    reserved_write_ratio=reserved_write_ratio,
                    metadata={
                        "page_capacity": page_capacity,
                        "row_columns": template_spec.columns,
                        "template_source": str(template_spec.source_path),
                    },
                )
            )
            cell_index += 1

    return GridFrames(
        page_number=page_number,
        page_size_pt=template_spec.page_size_pt,
        outer_frame=template_spec.outer_frame,
        header_frame=template_spec.header_frame,
        body_frame=template_spec.outer_frame,
        cells=cells,
    )


def compute_template_style_grid_frames(
    template_spec: LayoutTemplateSpec,
    config: LayoutConfig,
    *,
    page_number: int = 1,
) -> GridFrames:
    """Use template page geometry while generating a dynamic cell count."""

    page_capacity = max(1, int(config.problems_per_page))
    row_patterns = resolve_row_patterns(config)
    cells = build_cells_from_row_patterns(
        template_spec.outer_frame,
        row_patterns,
        page_number=page_number,
        cell_padding_pt=config.cell_padding_pt,
        page_capacity=page_capacity,
        blank_space_ratio_target=select_reserved_write_ratio(page_capacity, config),
    )
    return GridFrames(
        page_number=page_number,
        page_size_pt=template_spec.page_size_pt,
        outer_frame=template_spec.outer_frame,
        header_frame=template_spec.header_frame,
        body_frame=template_spec.outer_frame,
        cells=cells,
    )


def build_cells_from_row_patterns(
    outer_frame: FloatBBox,
    row_patterns: list[int],
    *,
    page_number: int,
    cell_padding_pt: float,
    page_capacity: int,
    blank_space_ratio_target: float,
) -> list[LayoutCell]:
    """Create layout cells from a top-to-bottom row pattern."""

    if not row_patterns or any(count <= 0 for count in row_patterns):
        raise ValueError("row_patterns must contain positive column counts.")

    x1, y1, x2, y2 = outer_frame
    row_count = len(row_patterns)
    total_height = y2 - y1
    row_height = total_height / row_count
    cells: list[LayoutCell] = []
    cell_index = 0

    for row_index, column_count in enumerate(row_patterns):
        row_top = y2 - (row_index * row_height)
        row_bottom = y2 - ((row_index + 1) * row_height)
        column_width = (x2 - x1) / column_count
        for column_index in range(column_count):
            frame = (
                x1 + (column_index * column_width),
                row_bottom,
                x1 + ((column_index + 1) * column_width),
                row_top,
            )
            cells.append(
                LayoutCell(
                    page_number=page_number,
                    cell_index=cell_index,
                    row=row_index,
                    column=column_index,
                    frame=frame,
                    content_frame=inset_frame(frame, cell_padding_pt),
                    reserved_write_ratio=blank_space_ratio_target,
                    metadata={
                        "page_capacity": page_capacity,
                        "row_columns": column_count,
                        "row_count": row_count,
                    },
                )
            )
            cell_index += 1
    return cells


def resolve_page_capacity(config: LayoutConfig, *, template_spec: LayoutTemplateSpec | None = None) -> int:
    """Return the active number of problems per page."""

    if template_spec is not None and config.template_lock_per_page:
        return template_spec.problems_per_page
    return max(1, int(config.problems_per_page))


def resolve_row_patterns(config: LayoutConfig) -> list[int]:
    """Resolve a sensible top-to-bottom row pattern for the requested per-page count."""

    target = max(1, int(config.problems_per_page))
    custom_pattern = str(getattr(config, "custom_row_pattern", "") or "").strip()
    if custom_pattern:
        return parse_row_pattern_spec(custom_pattern, expected_total=target)

    pattern_mode = str(getattr(config, "layout_pattern_mode", "auto") or "auto").strip().lower()
    if pattern_mode != "auto":
        preset = ROW_PATTERN_PRESETS.get(target, {}).get(pattern_mode)
        if preset is not None:
            return list(preset)

    explicit_capacity = max(1, int(config.rows) * int(config.columns))
    if explicit_capacity == target:
        return [int(config.columns)] * int(config.rows)
    return choose_balanced_row_patterns(target)


def parse_row_pattern_spec(pattern: str, *, expected_total: int | None = None) -> list[int]:
    """Parse a user row-pattern string like '1+2' or '1,1,1'."""

    cleaned = str(pattern).strip()
    if not cleaned:
        raise ValueError("Custom row pattern cannot be empty.")

    tokens = [token.strip() for token in cleaned.replace("x", "+").replace("*", "+").replace(",", "+").split("+")]
    if not tokens or any(not token for token in tokens):
        raise ValueError(f"Invalid custom row pattern: {pattern!r}")

    try:
        row_pattern = [int(token) for token in tokens]
    except ValueError as exc:
        raise ValueError(f"Invalid custom row pattern: {pattern!r}") from exc

    if any(value <= 0 for value in row_pattern):
        raise ValueError("Custom row pattern values must be positive integers.")

    if expected_total is not None and sum(row_pattern) != int(expected_total):
        raise ValueError(
            f"Custom row pattern total must equal {expected_total}, got {sum(row_pattern)}."
        )
    return row_pattern


def choose_balanced_row_patterns(target: int) -> list[int]:
    """Choose an exact row pattern that keeps cell shapes reasonably balanced."""

    best_pattern: list[int] | None = None
    best_score: float | None = None
    max_rows = min(target, 5)

    for row_count in range(1, max_rows + 1):
        pattern = distribute_cells_across_rows(target, row_count)
        score = score_row_pattern(pattern)
        if best_score is None or score < best_score:
            best_pattern = pattern
            best_score = score

    assert best_pattern is not None
    return best_pattern


def distribute_cells_across_rows(target: int, row_count: int) -> list[int]:
    """Distribute an exact number of cells across rows with near-uniform density."""

    base = target // row_count
    extras = target % row_count
    pattern: list[int] = []
    for row_index in range(row_count):
        count = base + (1 if row_index < extras else 0)
        pattern.append(max(1, count))
    return pattern


def score_row_pattern(pattern: list[int]) -> float:
    """Score a row pattern by how balanced the resulting portrait-page cells will feel."""

    row_count = len(pattern)
    score = 0.0
    for column_count in pattern:
        cell_aspect = row_count / max(column_count * 1.414, 0.1)
        score += abs(cell_aspect - 0.95)
        if column_count >= 4:
            score += 0.35
    score += (row_count - 1) * 0.05
    score += (max(pattern) - min(pattern)) * 0.08
    return score


def select_reserved_write_ratio(page_capacity: int, config: LayoutConfig) -> float:
    """Adjust the writing-space target based on how dense the page layout is."""

    target = float(config.blank_space_ratio_target)
    if page_capacity <= 4:
        return target
    reduction = min(0.18, (page_capacity - 4) * 0.035)
    return max(0.38, target - reduction)


def fit_problem_to_cell(
    problem: CroppedProblem,
    cell: LayoutCell,
    config: LayoutConfig,
) -> ProblemPlacement:
    """Fit one cropped problem into the upper portion of a layout cell."""

    problem_width, problem_height = resolve_problem_size(problem)
    shape_type = classify_problem_shape(problem_width, problem_height)
    content_x1, content_y1, content_x2, content_y2 = cell.content_frame
    content_width = content_x2 - content_x1
    content_height = content_y2 - content_y1

    preferred_write_ratio = cell.reserved_write_ratio
    preferred_image_ratio = min(
        select_problem_height_ratio(shape_type, config, cell=cell),
        max(0.20, 1.0 - preferred_write_ratio),
    )

    max_height = content_height * preferred_image_ratio
    width_scale = content_width / max(problem_width, 1.0)
    height_scale = max_height / max(problem_height, 1.0)
    scale = min(width_scale, height_scale)

    render_width = problem_width * scale
    render_height = problem_height * scale
    top_gap = min(12.0, max(4.0, content_height * 0.03))

    image_x1 = content_x1 + (content_width - render_width) / 2.0
    image_y2 = content_y2 - top_gap
    image_y1 = image_y2 - render_height
    image_frame = (image_x1, image_y1, image_x1 + render_width, image_y1 + render_height)

    writing_frame = (
        content_x1,
        content_y1,
        content_x2,
        max(content_y1, image_y1 - 4.0),
    )
    writing_height = max(0.0, writing_frame[3] - writing_frame[1])
    writing_ratio = writing_height / max(content_height, 1.0)

    return ProblemPlacement(
        page_number=cell.page_number,
        cell=cell,
        problem=problem,
        image_frame=image_frame,
        writing_frame=writing_frame,
        scale=scale,
        metadata={
            "shape_type": shape_type,
            "image_height_ratio": render_height / max(content_height, 1.0),
            "render_size_pt": (render_width, render_height),
            "writing_height_pt": writing_height,
            "writing_ratio": writing_ratio,
        },
    )


def resolve_page_size(page_size: str) -> tuple[float, float]:
    """Resolve a supported page size into PDF points."""

    normalized = page_size.upper().strip()
    if normalized != "A4":
        raise ValueError(f"Unsupported page size: {page_size}. Only A4 is currently supported.")
    return A4_PORTRAIT_PT


def resolve_problem_size(problem: CroppedProblem) -> tuple[float, float]:
    """Return the problem image size in pixels."""

    if problem.image is not None:
        height, width = problem.image.shape[:2]
        return float(width), float(height)
    return float(problem.width), float(problem.height)


def inset_frame(frame: FloatBBox, inset: float) -> FloatBBox:
    """Inset a frame by a uniform amount without collapsing it."""

    x1, y1, x2, y2 = frame
    effective = max(0.0, float(inset))
    if (x2 - x1) <= effective * 2 or (y2 - y1) <= effective * 2:
        return frame
    return (x1 + effective, y1 + effective, x2 - effective, y2 - effective)


def classify_problem_shape(width: float, height: float) -> str:
    """Classify problem geometry for layout heuristics."""

    aspect_ratio = width / max(height, 1.0)
    if aspect_ratio <= 0.72:
        return "tall"
    if aspect_ratio >= 1.60:
        return "wide"
    if 0.82 <= aspect_ratio <= 1.22:
        return "round"
    return "standard"


def select_problem_height_ratio(shape_type: str, config: LayoutConfig, *, cell: LayoutCell | None = None) -> float:
    """Choose a target image height ratio for the current cell density."""

    if shape_type == "tall":
        base_ratio = config.tall_problem_height_ratio
    elif shape_type == "wide":
        base_ratio = config.wide_problem_height_ratio
    elif shape_type == "round":
        base_ratio = config.round_problem_height_ratio
    else:
        base_ratio = config.standard_problem_height_ratio

    if cell is None:
        return base_ratio

    page_capacity = int(cell.metadata.get("page_capacity", 4))
    if page_capacity <= 4:
        return base_ratio
    boost = min(0.18, (page_capacity - 4) * 0.03)
    return min(0.72, base_ratio + boost)
