from app.config import DetectionConfig
from app.models import DetectedGrid, ProblemRegion
from app.refine_regions import refine_problem_regions, sort_regions_reading_order


def test_sort_regions_reading_order_groups_by_row_then_column() -> None:
    regions = [
        ProblemRegion(page_index=0, bbox=(360, 120, 520, 260)),
        ProblemRegion(page_index=0, bbox=(40, 420, 220, 580)),
        ProblemRegion(page_index=0, bbox=(30, 110, 200, 250)),
        ProblemRegion(page_index=0, bbox=(330, 430, 520, 590)),
    ]

    ordered = sort_regions_reading_order(
        regions,
        page_size=(800, 1200),
        config=DetectionConfig(),
    )

    assert [item.bbox for item in ordered] == [
        (30, 110, 200, 250),
        (360, 120, 520, 260),
        (40, 420, 220, 580),
        (330, 430, 520, 590),
    ]


def test_refine_problem_regions_drops_grid_spanning_container() -> None:
    grid = DetectedGrid(
        page_index=0,
        outer_bbox=(257, 219, 1513, 2311),
        vertical_lines=(257, 885, 1513),
        horizontal_lines=(219, 1265, 2311),
    )
    regions = [
        ProblemRegion(page_index=0, bbox=(250, 220, 1510, 2310), score=0.2),
        ProblemRegion(page_index=0, bbox=(300, 250, 710, 600), score=0.3),
        ProblemRegion(page_index=0, bbox=(920, 260, 1320, 560), score=0.3),
        ProblemRegion(page_index=0, bbox=(290, 1290, 750, 1540), score=0.3),
        ProblemRegion(page_index=0, bbox=(930, 1295, 1470, 1430), score=0.3),
    ]

    refined = refine_problem_regions(
        regions,
        page_size=(1654, 2480),
        config=DetectionConfig(),
        page_grid=grid,
    )

    assert len(refined) == 4
    assert all(region.bbox != (250, 220, 1510, 2310) for region in refined)


def test_refine_problem_regions_keeps_peer_row_regions_separate() -> None:
    regions = [
        ProblemRegion(page_index=0, bbox=(79, 23, 566, 441), score=0.7),
        ProblemRegion(page_index=0, bbox=(587, 74, 1049, 407), score=0.7),
        ProblemRegion(page_index=0, bbox=(1080, 103, 1579, 397), score=0.7),
    ]

    refined = refine_problem_regions(
        regions,
        page_size=(1656, 1339),
        config=DetectionConfig(),
    )

    assert [region.bbox for region in refined] == [
        (79, 23, 566, 441),
        (587, 74, 1049, 407),
        (1080, 103, 1579, 397),
    ]


def test_refine_problem_regions_still_merges_small_attachment() -> None:
    regions = [
        ProblemRegion(page_index=0, bbox=(200, 160, 620, 470), score=0.8),
        ProblemRegion(page_index=0, bbox=(635, 215, 700, 305), score=0.5),
    ]

    refined = refine_problem_regions(
        regions,
        page_size=(1656, 1339),
        config=DetectionConfig(),
    )

    assert len(refined) == 1
    assert refined[0].bbox == (200, 160, 700, 470)
