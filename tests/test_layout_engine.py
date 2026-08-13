import numpy as np

from app.config import LayoutConfig
from app.layout_engine import compute_grid_frames, fit_problem_to_cell, paginate_problems, parse_row_pattern_spec
from app.models import CroppedProblem, LayoutTemplateSpec


def build_problem(problem_id: str, height: int = 420, width: int = 320) -> CroppedProblem:
    image = np.full((height, width, 3), 255, dtype=np.uint8)
    return CroppedProblem(
        problem_id=problem_id,
        page_index=0,
        original_bbox=(0, 0, width, height),
        cropped_bbox=(0, 0, width, height),
        image=image,
    )


def test_paginate_problems_preserves_blank_cells_on_last_page() -> None:
    config = LayoutConfig()
    problems = [build_problem(f"t{i}") for i in range(5)]

    pages = paginate_problems(problems, config)

    assert len(pages) == 2
    assert len(pages[0].placements) == 4
    assert len(pages[1].placements) == 4
    assert sum(placement.problem is None for placement in pages[1].placements) == 3


def test_paginate_problems_supports_dynamic_per_page_count() -> None:
    config = LayoutConfig(problems_per_page=6)
    problems = [build_problem(f"t{i}") for i in range(8)]

    pages = paginate_problems(problems, config)

    assert len(pages) == 2
    assert len(pages[0].placements) == 6
    assert len(pages[1].placements) == 6
    assert sum(placement.problem is None for placement in pages[1].placements) == 4


def test_compute_grid_frames_creates_fixed_2x2_layout() -> None:
    frames = compute_grid_frames(LayoutConfig(), page_number=1)

    assert len(frames.cells) == 4

    top_left = frames.cells[0]
    top_right = frames.cells[1]
    bottom_left = frames.cells[2]
    bottom_right = frames.cells[3]

    assert top_left.row == 0 and top_left.column == 0
    assert top_right.row == 0 and top_right.column == 1
    assert bottom_left.row == 1 and bottom_left.column == 0
    assert bottom_right.row == 1 and bottom_right.column == 1

    assert top_left.frame[1] > bottom_left.frame[1]
    assert top_left.frame[0] < top_right.frame[0]
    assert top_left.frame[2] <= top_right.frame[0] + 1e-6
    assert bottom_left.frame[2] <= bottom_right.frame[0] + 1e-6


def test_compute_grid_frames_supports_balanced_dynamic_layout() -> None:
    frames = compute_grid_frames(LayoutConfig(problems_per_page=6), page_number=1)

    assert len(frames.cells) == 6
    assert {cell.row for cell in frames.cells} == {0, 1, 2}
    assert all(cell.metadata["page_capacity"] == 6 for cell in frames.cells)
    assert all(cell.metadata["row_columns"] == 2 for cell in frames.cells)


def test_compute_grid_frames_supports_user_selected_three_problem_pattern() -> None:
    frames = compute_grid_frames(
        LayoutConfig(
            problems_per_page=3,
            layout_pattern_mode="top1_bottom2",
        ),
        page_number=1,
    )

    assert len(frames.cells) == 3
    assert [cell.metadata["row_columns"] for cell in frames.cells] == [1, 2, 2]
    assert [cell.row for cell in frames.cells] == [0, 1, 1]


def test_parse_row_pattern_spec_validates_expected_total() -> None:
    assert parse_row_pattern_spec("1+2", expected_total=3) == [1, 2]


def test_template_can_use_manual_page_capacity_when_lock_is_disabled() -> None:
    template = LayoutTemplateSpec(
        source_path="template.pdf",
        page_size_pt=(595.2756, 841.8898),
        outer_frame=(80.0, 86.0, 510.0, 770.0),
        header_frame=(80.0, 784.0, 510.0, 804.0),
        column_boundaries=(80.0, 295.0, 510.0),
        row_boundaries=(86.0, 428.0, 770.0),
    )
    config = LayoutConfig(problems_per_page=6, template_lock_per_page=False)
    problems = [build_problem(f"t{i}") for i in range(8)]

    pages = paginate_problems(problems, config, template_spec=template)

    assert len(pages) == 2
    assert len(pages[0].placements) == 6
    assert len(pages[1].placements) == 6
    assert sum(placement.problem is None for placement in pages[1].placements) == 4


def test_fit_problem_to_cell_preserves_reference_writing_space() -> None:
    config = LayoutConfig()
    problem = build_problem("standard", height=360, width=320)
    cell = compute_grid_frames(config, page_number=1).cells[0]

    placement = fit_problem_to_cell(problem, cell, config)

    content_height = cell.content_frame[3] - cell.content_frame[1]
    writing_height = placement.writing_frame[3] - placement.writing_frame[1]
    writing_ratio = writing_height / content_height
    image_height_ratio = (placement.image_frame[3] - placement.image_frame[1]) / content_height  # type: ignore[index]

    assert 0.50 <= writing_ratio <= 0.67
    assert image_height_ratio <= 0.42


def test_fit_problem_to_cell_allows_tall_problem_to_use_more_height() -> None:
    config = LayoutConfig()
    problem = build_problem("tall", height=520, width=240)
    cell = compute_grid_frames(config, page_number=1).cells[0]

    placement = fit_problem_to_cell(problem, cell, config)

    content_height = cell.content_frame[3] - cell.content_frame[1]
    image_height_ratio = (placement.image_frame[3] - placement.image_frame[1]) / content_height  # type: ignore[index]

    assert image_height_ratio <= 0.50
    assert image_height_ratio >= 0.40
