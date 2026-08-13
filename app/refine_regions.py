"""Region merge, deduplication, and reading-order refinement helpers."""

from __future__ import annotations

from statistics import median

from app.config import DetectionConfig
from app.models import BBox, DetectedGrid, ProblemRegion, bbox_area, merge_bboxes


def refine_problem_regions(
    regions: list[ProblemRegion],
    *,
    page_size: tuple[int, int],
    config: DetectionConfig,
    page_grid: DetectedGrid | None = None,
) -> list[ProblemRegion]:
    """Merge fragmented detections and sort them in reading order."""

    if not regions:
        return []

    working = filter_problem_regions(regions, page_size=page_size, config=config)
    if page_grid is None:
        working = deduplicate_regions(working)
        working = merge_adjacent_regions(working, page_size=page_size, config=config)
        working = deduplicate_regions(working)
        working = filter_problem_regions(working, page_size=page_size, config=config)
        return sort_regions_reading_order(working, page_size=page_size, config=config)

    working = discard_grid_spanning_regions(working, page_grid)
    bucketed = bucket_regions_by_grid_cell(working, page_grid)

    refined: list[ProblemRegion] = []
    for cell_index in range(4):
        cell_regions = bucketed.get(cell_index, [])
        if not cell_regions:
            continue
        cell_working = deduplicate_regions(cell_regions)
        cell_working = merge_adjacent_regions(
            cell_working,
            page_size=page_size,
            config=config,
            page_grid=page_grid,
        )
        cell_working = deduplicate_regions(cell_working)
        cell_working = filter_problem_regions(cell_working, page_size=page_size, config=config)
        cell_working = sort_regions_reading_order(cell_working, page_size=page_size, config=config)
        refined.extend(cell_working)

    if not refined:
        working = deduplicate_regions(working)
        working = merge_adjacent_regions(working, page_size=page_size, config=config)
        working = deduplicate_regions(working)
        working = filter_problem_regions(working, page_size=page_size, config=config)
        return sort_regions_reading_order(working, page_size=page_size, config=config)

    return sort_regions_by_grid_order(refined, page_grid)


def filter_problem_regions(
    regions: list[ProblemRegion],
    *,
    page_size: tuple[int, int],
    config: DetectionConfig,
) -> list[ProblemRegion]:
    """Apply a final noise filter using page-relative thresholds."""

    page_width, page_height = page_size
    page_area = page_width * page_height
    min_area = max(1, int(page_area * config.min_region_area_ratio))
    min_width = max(1, int(page_width * config.min_region_width_ratio))
    min_height = max(1, int(page_height * config.min_region_height_ratio))

    filtered: list[ProblemRegion] = []
    for region in regions:
        width = region.width
        height = region.height
        area = width * height
        aspect_ratio = max(width / max(height, 1), height / max(width, 1))

        if area < min_area and (width < min_width or height < min_height):
            continue
        if aspect_ratio > config.max_noise_aspect_ratio and area < min_area * 4:
            continue
        filtered.append(region)
    return filtered


def merge_adjacent_regions(
    regions: list[ProblemRegion],
    *,
    page_size: tuple[int, int],
    config: DetectionConfig,
    page_grid: DetectedGrid | None = None,
) -> list[ProblemRegion]:
    """Iteratively merge adjacent fragments that likely belong to one problem."""

    if len(regions) < 2:
        return list(regions)

    merged_regions = sorted(regions, key=lambda region: (region.bbox[1], region.bbox[0]))
    changed = True

    while changed:
        changed = False
        consumed = [False] * len(merged_regions)
        next_round: list[ProblemRegion] = []

        for index, region in enumerate(merged_regions):
            if consumed[index]:
                continue

            current = region
            consumed[index] = True
            merged_this_round = True

            while merged_this_round:
                merged_this_round = False
                for other_index, other in enumerate(merged_regions):
                    if consumed[other_index]:
                        continue
                    if should_merge_regions(current, other, page_size=page_size, config=config, page_grid=page_grid):
                        current = merge_problem_regions(current, other)
                        consumed[other_index] = True
                        merged_this_round = True
                        changed = True

            next_round.append(current)

        merged_regions = sorted(next_round, key=lambda region: (region.bbox[1], region.bbox[0]))

    return merged_regions


def should_merge_regions(
    first: ProblemRegion,
    second: ProblemRegion,
    *,
    page_size: tuple[int, int],
    config: DetectionConfig,
    page_grid: DetectedGrid | None = None,
) -> bool:
    """Decide whether two candidate regions should be merged."""

    first_box = first.bbox
    second_box = second.bbox
    combined_box = merge_bboxes(first_box, second_box)

    if page_grid is not None and crosses_grid_dividers(combined_box, page_grid):
        return False

    if overlap_ratio(first_box, second_box) >= config.merge_overlap_ratio:
        return True

    if is_contained(first_box, second_box) or is_contained(second_box, first_box):
        return True

    page_width, page_height = page_size
    horizontal_limit = max(8, int(page_width * config.merge_x_gap_ratio))
    vertical_limit = max(8, int(page_height * config.merge_y_gap_ratio))

    x_gap = horizontal_gap(first_box, second_box)
    y_gap = vertical_gap(first_box, second_box)
    horizontal_overlap = axis_overlap_ratio(first_box, second_box, axis="x")
    vertical_overlap = axis_overlap_ratio(first_box, second_box, axis="y")

    area_ratio = min(bbox_area(first_box), bbox_area(second_box)) / max(
        bbox_area(first_box),
        bbox_area(second_box),
    )
    small_attachment = area_ratio <= config.merge_small_attachment_area_ratio
    peer_regions = are_peer_regions(first_box, second_box, page_size=page_size, config=config)

    # Two neighboring full-sized peer regions are much more likely to be
    # separate problems than fragmented parts of the same one.
    if peer_regions:
        return False

    same_row_fragment = x_gap <= horizontal_limit and (
        vertical_overlap >= 0.18 or (small_attachment and vertical_overlap >= 0.05)
    )
    if same_row_fragment:
        return True

    same_column_fragment = y_gap <= vertical_limit and (
        horizontal_overlap >= 0.18 or (small_attachment and horizontal_overlap >= 0.08)
    )
    if same_column_fragment:
        return True

    diagonal_attachment = (
        x_gap <= int(horizontal_limit * 0.65)
        and y_gap <= int(vertical_limit * 0.65)
        and (horizontal_overlap > 0.0 or vertical_overlap > 0.0 or small_attachment)
    )
    if diagonal_attachment:
        return True

    if config.allow_sparse_problem_expansion and small_attachment:
        return sparse_attachment(first_box, second_box, horizontal_limit, vertical_limit)

    return False


def are_peer_regions(
    first: BBox,
    second: BBox,
    *,
    page_size: tuple[int, int],
    config: DetectionConfig,
) -> bool:
    """Return True when two boxes look like separate full-sized problems."""

    first_area = bbox_area(first)
    second_area = bbox_area(second)
    smaller_area = min(first_area, second_area)
    larger_area = max(first_area, second_area)
    if larger_area <= 0:
        return False

    area_ratio = smaller_area / larger_area
    if area_ratio < config.merge_peer_region_area_ratio:
        return False

    page_width, page_height = page_size
    page_area = max(page_width * page_height, 1)
    min_peer_area = page_area * config.merge_peer_min_region_area_ratio
    if first_area < min_peer_area or second_area < min_peer_area:
        return False

    first_width = first[2] - first[0]
    second_width = second[2] - second[0]
    first_height = first[3] - first[1]
    second_height = second[3] - second[1]

    wide_pair = first_width >= page_width * 0.14 and second_width >= page_width * 0.14
    tall_pair = first_height >= page_height * 0.12 and second_height >= page_height * 0.12
    return wide_pair or tall_pair


def merge_problem_regions(first: ProblemRegion, second: ProblemRegion) -> ProblemRegion:
    """Combine two regions into a single enclosing region."""

    merged_sources = list(flatten_merged_sources(first)) + list(flatten_merged_sources(second))
    merged_bbox = merge_bboxes(first.bbox, second.bbox)
    score = max(first.score, second.score)
    tags = tuple(sorted(set(first.tags).union(second.tags)))

    metadata = dict(first.metadata)
    metadata.update(second.metadata)
    metadata["merged_sources"] = merged_sources
    metadata["source_count"] = len(merged_sources)

    return ProblemRegion(
        page_index=min(first.page_index, second.page_index),
        bbox=merged_bbox,
        score=score,
        source="merged",
        problem_no=first.problem_no or second.problem_no,
        tags=tags,
        metadata=metadata,
    )


def deduplicate_regions(regions: list[ProblemRegion]) -> list[ProblemRegion]:
    """Remove near-duplicate detections while keeping the stronger region."""

    if len(regions) < 2:
        return list(regions)

    sorted_regions = sorted(
        regions,
        key=lambda region: (bbox_area(region.bbox), region.score),
        reverse=True,
    )
    kept: list[ProblemRegion] = []

    for region in sorted_regions:
        if any(overlap_ratio(region.bbox, other.bbox) >= 0.92 for other in kept):
            continue
        kept.append(region)

    return kept


def sort_regions_reading_order(
    regions: list[ProblemRegion],
    *,
    page_size: tuple[int, int],
    config: DetectionConfig,
) -> list[ProblemRegion]:
    """Sort regions from top to bottom, then left to right within each row band."""

    if not regions:
        return []

    page_height = page_size[1]
    heights = [region.height for region in regions]
    median_height = median(heights)
    row_tolerance = max(
        12,
        int(page_height * config.row_group_tolerance_ratio),
        int(median_height * 0.3),
    )

    sorted_by_y = sorted(regions, key=lambda region: (region.bbox[1], region.bbox[0]))
    rows: list[dict[str, object]] = []

    for region in sorted_by_y:
        center_y = (region.bbox[1] + region.bbox[3]) / 2.0
        target_row: dict[str, object] | None = None
        for row in rows:
            if abs(center_y - float(row["anchor_y"])) <= row_tolerance:
                target_row = row
                break

        if target_row is None:
            rows.append({"anchor_y": center_y, "regions": [region]})
            continue

        row_regions = target_row["regions"]
        assert isinstance(row_regions, list)
        row_regions.append(region)
        target_row["anchor_y"] = sum(
            (item.bbox[1] + item.bbox[3]) / 2.0 for item in row_regions
        ) / len(row_regions)

    ordered: list[ProblemRegion] = []
    rows.sort(key=lambda row: min(region.bbox[1] for region in row["regions"]))  # type: ignore[index]
    for row in rows:
        row_regions = row["regions"]
        assert isinstance(row_regions, list)
        row_regions.sort(key=lambda region: (region.bbox[0], region.bbox[1]))
        ordered.extend(row_regions)

    return ordered


def discard_grid_spanning_regions(
    regions: list[ProblemRegion],
    page_grid: DetectedGrid,
) -> list[ProblemRegion]:
    """Drop large spanning containers when stable per-cell candidates already exist."""

    kept: list[ProblemRegion] = []
    for region in regions:
        if not crosses_grid_dividers(region.bbox, page_grid):
            kept.append(region)
            continue

        supported_cells = set()
        for other in regions:
            if other is region:
                continue
            if bbox_area(other.bbox) >= bbox_area(region.bbox):
                continue
            cell_index = region_cell_index(other, page_grid)
            if cell_index is None:
                continue
            if overlap_ratio(other.bbox, region.bbox) >= 0.98:
                supported_cells.add(cell_index)

        if len(supported_cells) >= 2:
            continue
        kept.append(region)
    return kept


def bucket_regions_by_grid_cell(
    regions: list[ProblemRegion],
    page_grid: DetectedGrid,
) -> dict[int, list[ProblemRegion]]:
    """Assign regions to one of the 4 grid cells using their bbox center."""

    buckets: dict[int, list[ProblemRegion]] = {0: [], 1: [], 2: [], 3: []}
    for region in regions:
        cell_index = region_cell_index(region, page_grid)
        if cell_index is None:
            continue
        buckets[cell_index].append(region)
    return buckets


def sort_regions_by_grid_order(
    regions: list[ProblemRegion],
    page_grid: DetectedGrid,
) -> list[ProblemRegion]:
    """Sort regions by grid cell order, then by local reading order."""

    return sorted(
        regions,
        key=lambda region: (
            region_cell_index(region, page_grid) if region_cell_index(region, page_grid) is not None else 99,
            region.bbox[1],
            region.bbox[0],
        ),
    )


def region_cell_index(region: ProblemRegion, page_grid: DetectedGrid) -> int | None:
    """Return the row-major grid cell index for a region center."""

    x1, y1, x2, y2 = region.bbox
    center_x = (x1 + x2) / 2.0
    center_y = (y1 + y2) / 2.0
    return page_grid.cell_index_for_point(center_x, center_y)


def crosses_grid_dividers(bbox: BBox, page_grid: DetectedGrid) -> bool:
    """Return True when a bbox spans the grid center lines."""

    crosses_vertical = bbox[0] < page_grid.center_x < bbox[2]
    crosses_horizontal = bbox[1] < page_grid.center_y < bbox[3]
    return crosses_vertical or crosses_horizontal


def flatten_merged_sources(region: ProblemRegion) -> list[BBox]:
    """Return the original bboxes that contributed to a region."""

    merged_sources = region.metadata.get("merged_sources")
    if not isinstance(merged_sources, list):
        return [region.bbox]

    normalized: list[BBox] = []
    for item in merged_sources:
        if isinstance(item, (list, tuple)) and len(item) == 4:
            normalized.append(tuple(int(value) for value in item))
    return normalized or [region.bbox]


def overlap_ratio(first: BBox, second: BBox) -> float:
    """Compute intersection area divided by the smaller bbox area."""

    intersection = intersection_area(first, second)
    if intersection <= 0:
        return 0.0
    return intersection / max(1.0, min(bbox_area(first), bbox_area(second)))


def intersection_area(first: BBox, second: BBox) -> float:
    """Return the intersection area between two bboxes."""

    x1 = max(first[0], second[0])
    y1 = max(first[1], second[1])
    x2 = min(first[2], second[2])
    y2 = min(first[3], second[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    return float((x2 - x1) * (y2 - y1))


def horizontal_gap(first: BBox, second: BBox) -> int:
    """Return the horizontal separation between two bboxes."""

    if first[2] < second[0]:
        return second[0] - first[2]
    if second[2] < first[0]:
        return first[0] - second[2]
    return 0


def vertical_gap(first: BBox, second: BBox) -> int:
    """Return the vertical separation between two bboxes."""

    if first[3] < second[1]:
        return second[1] - first[3]
    if second[3] < first[1]:
        return first[1] - second[3]
    return 0


def axis_overlap_ratio(first: BBox, second: BBox, *, axis: str) -> float:
    """Compute overlap ratio on one axis, normalized by the smaller span."""

    if axis == "x":
        start = max(first[0], second[0])
        end = min(first[2], second[2])
        span = min(first[2] - first[0], second[2] - second[0])
    elif axis == "y":
        start = max(first[1], second[1])
        end = min(first[3], second[3])
        span = min(first[3] - first[1], second[3] - second[1])
    else:
        raise ValueError("axis must be 'x' or 'y'")

    if end <= start or span <= 0:
        return 0.0
    return (end - start) / span


def is_contained(inner: BBox, outer: BBox, *, threshold: float = 0.94) -> bool:
    """Return True when one bbox is almost fully contained in another."""

    if bbox_area(inner) > bbox_area(outer):
        return False
    return overlap_ratio(inner, outer) >= threshold


def sparse_attachment(first: BBox, second: BBox, horizontal_limit: int, vertical_limit: int) -> bool:
    """Allow a small sparse fragment to attach to a nearby main problem body."""

    x_gap = horizontal_gap(first, second)
    y_gap = vertical_gap(first, second)
    first_center_x = (first[0] + first[2]) / 2.0
    first_center_y = (first[1] + first[3]) / 2.0
    second_center_x = (second[0] + second[2]) / 2.0
    second_center_y = (second[1] + second[3]) / 2.0

    center_dx = abs(first_center_x - second_center_x)
    center_dy = abs(first_center_y - second_center_y)
    max_width = max(first[2] - first[0], second[2] - second[0])
    max_height = max(first[3] - first[1], second[3] - second[1])

    if x_gap <= int(horizontal_limit * 1.4) and center_dy <= max_height * 0.5:
        return True
    if y_gap <= int(vertical_limit * 1.6) and center_dx <= max_width * 0.55:
        return True
    return x_gap == 0 and y_gap == 0
