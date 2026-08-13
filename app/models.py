"""Core data models shared across the PDF exam formatter pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray

ProcessingMode = Literal["normal", "fast"]
BBox = tuple[int, int, int, int]
FloatBBox = tuple[float, float, float, float]


def _validate_bbox(bbox: BBox | FloatBBox) -> None:
    """Validate that a bounding box uses the (x1, y1, x2, y2) convention."""

    x1, y1, x2, y2 = bbox
    if x2 <= x1 or y2 <= y1:
        raise ValueError(f"Invalid bbox {bbox!r}: x2/y2 must be greater than x1/y1.")


def bbox_width(bbox: BBox | FloatBBox) -> float:
    """Return the width of a bbox."""

    _validate_bbox(bbox)
    return float(bbox[2] - bbox[0])


def bbox_height(bbox: BBox | FloatBBox) -> float:
    """Return the height of a bbox."""

    _validate_bbox(bbox)
    return float(bbox[3] - bbox[1])


def bbox_area(bbox: BBox | FloatBBox) -> float:
    """Return the area of a bbox."""

    return bbox_width(bbox) * bbox_height(bbox)


def merge_bboxes(*boxes: BBox | FloatBBox) -> BBox:
    """Merge one or more bounding boxes into a single enclosing bbox."""

    if not boxes:
        raise ValueError("At least one bbox is required for merging.")

    normalized = [tuple(int(round(value)) for value in box) for box in boxes]
    for box in normalized:
        _validate_bbox(box)

    x1 = min(box[0] for box in normalized)
    y1 = min(box[1] for box in normalized)
    x2 = max(box[2] for box in normalized)
    y2 = max(box[3] for box in normalized)
    return (x1, y1, x2, y2)


def clamp_bbox(bbox: BBox | FloatBBox, width: int, height: int) -> BBox:
    """Clamp a bbox into image boundaries."""

    _validate_bbox(bbox)
    x1, y1, x2, y2 = bbox
    clamped = (
        max(0, min(int(round(x1)), width)),
        max(0, min(int(round(y1)), height)),
        max(0, min(int(round(x2)), width)),
        max(0, min(int(round(y2)), height)),
    )
    _validate_bbox(clamped)
    return clamped


@dataclass(slots=True)
class PageImage:
    """A rasterized source page used by downstream detection and crop stages."""

    source_path: Path
    page_index: int
    width: int
    height: int
    dpi: int
    image: NDArray[np.uint8] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.source_path = Path(self.source_path)
        if self.page_index < 0:
            raise ValueError("page_index must be non-negative.")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Page width and height must be positive.")
        if self.dpi <= 0:
            raise ValueError("dpi must be positive.")

    @property
    def size(self) -> tuple[int, int]:
        """Return the page size as (width, height)."""

        return (self.width, self.height)


@dataclass(slots=True)
class DetectedGrid:
    """Detected page grid structure used to prevent cross-cell region merges."""

    page_index: int
    outer_bbox: BBox
    vertical_lines: tuple[int, int, int]
    horizontal_lines: tuple[int, int, int]
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.page_index < 0:
            raise ValueError("page_index must be non-negative.")
        _validate_bbox(self.outer_bbox)
        if len(self.vertical_lines) != 3 or len(self.horizontal_lines) != 3:
            raise ValueError("DetectedGrid requires exactly 3 vertical and 3 horizontal lines.")

    @property
    def center_x(self) -> int:
        """Return the x coordinate of the center divider."""

        return int(self.vertical_lines[1])

    @property
    def center_y(self) -> int:
        """Return the y coordinate of the center divider."""

        return int(self.horizontal_lines[1])

    def cell_bbox(self, row: int, column: int) -> BBox:
        """Return the bbox for one 2x2 grid cell using image coordinates."""

        if row not in (0, 1) or column not in (0, 1):
            raise ValueError("row and column must be 0 or 1 for a 2x2 grid.")

        x_positions = self.vertical_lines
        y_positions = self.horizontal_lines
        return (
            int(x_positions[column]),
            int(y_positions[row]),
            int(x_positions[column + 1]),
            int(y_positions[row + 1]),
        )

    def cell_index_for_point(self, x: float, y: float) -> int | None:
        """Return the row-major cell index for a point, or None if outside the outer frame."""

        ox1, oy1, ox2, oy2 = self.outer_bbox
        if x < ox1 or x > ox2 or y < oy1 or y > oy2:
            return None

        column = 0 if x < self.center_x else 1
        row = 0 if y < self.center_y else 1
        return row * 2 + column


@dataclass(slots=True)
class ProblemRegion:
    """A detected candidate problem area on a page."""

    page_index: int
    bbox: BBox
    score: float = 1.0
    source: str = "detected"
    problem_no: str | None = None
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.page_index < 0:
            raise ValueError("page_index must be non-negative.")
        _validate_bbox(self.bbox)
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("score must be between 0.0 and 1.0.")

    @property
    def width(self) -> int:
        """Return bbox width in pixels."""

        return int(bbox_width(self.bbox))

    @property
    def height(self) -> int:
        """Return bbox height in pixels."""

        return int(bbox_height(self.bbox))

    @property
    def aspect_ratio(self) -> float:
        """Return width / height for heuristic layout decisions."""

        return self.width / max(self.height, 1)


@dataclass(slots=True)
class CroppedProblem:
    """A safely cropped problem image ready for layout."""

    problem_id: str
    page_index: int
    original_bbox: BBox
    cropped_bbox: BBox
    image: NDArray[np.uint8] | None = None
    trim_bbox: BBox | None = None
    padding: tuple[int, int, int, int] = (0, 0, 0, 0)
    rotation_degrees: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.problem_id.strip():
            raise ValueError("problem_id must not be empty.")
        if self.page_index < 0:
            raise ValueError("page_index must be non-negative.")
        _validate_bbox(self.original_bbox)
        _validate_bbox(self.cropped_bbox)
        if self.trim_bbox is not None:
            _validate_bbox(self.trim_bbox)

    @property
    def width(self) -> int:
        """Return cropped width in pixels."""

        return int(bbox_width(self.cropped_bbox))

    @property
    def height(self) -> int:
        """Return cropped height in pixels."""

        return int(bbox_height(self.cropped_bbox))


@dataclass(slots=True)
class LayoutCell:
    """A single cell inside the fixed 2x2 page grid."""

    page_number: int
    cell_index: int
    row: int
    column: int
    frame: FloatBBox
    content_frame: FloatBBox
    problem_id: str | None = None
    reserved_write_ratio: float = 0.18
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.page_number < 1:
            raise ValueError("page_number must start from 1.")
        if self.cell_index < 0:
            raise ValueError("cell_index must be non-negative.")
        if self.row < 0 or self.column < 0:
            raise ValueError("row and column must be non-negative.")
        _validate_bbox(self.frame)
        _validate_bbox(self.content_frame)
        if not 0.0 <= self.reserved_write_ratio < 1.0:
            raise ValueError("reserved_write_ratio must be between 0.0 and 1.0.")


@dataclass(slots=True)
class LayoutTemplateSpec:
    """Resolved page template geometry inferred from a user-supplied sample."""

    source_path: Path
    page_size_pt: tuple[float, float]
    outer_frame: FloatBBox
    header_frame: FloatBBox
    column_boundaries: tuple[float, ...]
    row_boundaries: tuple[float, ...]
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.source_path = Path(self.source_path)
        _validate_bbox(self.outer_frame)
        _validate_bbox(self.header_frame)
        if len(self.column_boundaries) < 2 or len(self.row_boundaries) < 2:
            raise ValueError("Template boundaries must include at least two positions per axis.")
        if tuple(sorted(self.column_boundaries)) != self.column_boundaries:
            raise ValueError("column_boundaries must be in ascending order.")
        if tuple(sorted(self.row_boundaries)) != self.row_boundaries:
            raise ValueError("row_boundaries must be in ascending order.")

    @property
    def rows(self) -> int:
        """Return the number of row bands in the template grid."""

        return len(self.row_boundaries) - 1

    @property
    def columns(self) -> int:
        """Return the number of columns in the template grid."""

        return len(self.column_boundaries) - 1

    @property
    def problems_per_page(self) -> int:
        """Return the template grid capacity."""

        return self.rows * self.columns


@dataclass(slots=True)
class RenderConfig:
    """Resolved render settings used by the PDF output layer."""

    page_width_pt: float = 595.2756
    page_height_pt: float = 841.8898
    outer_margin_pt: float = 24.0
    header_height_pt: float = 96.0
    cell_padding_pt: float = 12.0
    grid_line_width_pt: float = 1.0
    image_fill_ratio: float = 0.81
    max_image_fill_ratio: float = 0.84
    min_image_fill_ratio: float = 0.78
    default_font_name: str = "Helvetica"
    default_font_size: float = 11.0
    title_font_size: float = 16.0
    mode: ProcessingMode = "normal"

    def __post_init__(self) -> None:
        if self.page_width_pt <= 0 or self.page_height_pt <= 0:
            raise ValueError("Page dimensions must be positive.")
        if self.header_height_pt <= 0:
            raise ValueError("header_height_pt must be positive.")
        if self.outer_margin_pt < 0 or self.cell_padding_pt < 0:
            raise ValueError("Margins and padding must be non-negative.")
        if not 0.0 < self.min_image_fill_ratio <= self.image_fill_ratio <= self.max_image_fill_ratio < 1.0:
            raise ValueError("Image fill ratios must satisfy 0 < min <= default <= max < 1.")
