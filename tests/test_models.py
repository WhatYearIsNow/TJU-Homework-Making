from app.models import merge_bboxes


def test_merge_bboxes_returns_enclosing_box() -> None:
    merged = merge_bboxes((10, 20, 60, 90), (0, 15, 40, 30), (18, 12, 100, 120))
    assert merged == (0, 12, 100, 120)
