from app.detect_regions import group_active_rows, infer_numbered_question_spans


def test_infer_numbered_question_spans_groups_lines_until_next_anchor() -> None:
    lines = [
        {"text": "一、单选题", "bbox": (60.0, 40.0, 190.0, 56.0)},
        {"text": "1. 已知向量...", "bbox": (60.0, 80.0, 340.0, 100.0)},
        {"text": "A. 1", "bbox": (90.0, 108.0, 150.0, 126.0)},
        {"text": "B. 2", "bbox": (230.0, 108.0, 290.0, 126.0)},
        {"text": "2. 在△ABC中...", "bbox": (60.0, 160.0, 360.0, 182.0)},
        {"text": "A. 3", "bbox": (90.0, 190.0, 150.0, 208.0)},
    ]

    spans = infer_numbered_question_spans(lines, min_questions=2)

    assert len(spans) == 2
    assert spans[0]["number"] == 1
    assert spans[0]["line_count"] == 3
    assert spans[1]["number"] == 2
    assert spans[1]["line_count"] == 2


def test_infer_numbered_question_spans_returns_empty_when_anchors_too_few() -> None:
    lines = [
        {"text": "课程名称", "bbox": (60.0, 40.0, 190.0, 56.0)},
        {"text": "1. 已知向量...", "bbox": (60.0, 80.0, 340.0, 100.0)},
        {"text": "A. 1", "bbox": (90.0, 108.0, 150.0, 126.0)},
    ]

    spans = infer_numbered_question_spans(lines, min_questions=2)

    assert spans == []


def test_infer_numbered_question_spans_skips_abnormal_anchor_jump() -> None:
    lines = [
        {"text": "1. 第一题", "bbox": (60.0, 80.0, 220.0, 100.0)},
        {"text": "A. 选项", "bbox": (90.0, 108.0, 190.0, 126.0)},
        {"text": "99. 干扰锚点", "bbox": (60.0, 150.0, 240.0, 170.0)},
        {"text": "2. 第二题", "bbox": (60.0, 220.0, 220.0, 240.0)},
        {"text": "A. 选项", "bbox": (90.0, 248.0, 190.0, 266.0)},
    ]

    spans = infer_numbered_question_spans(lines, min_questions=2, max_anchor_step=3)

    assert len(spans) == 2
    assert spans[0]["number"] == 1
    assert spans[1]["number"] == 2


def test_group_active_rows_merges_with_gap_tolerance() -> None:
    rows = [10, 11, 12, 20, 21, 40]

    spans = group_active_rows(rows, max_gap=2)

    assert spans == [(10, 13), (20, 22), (40, 41)]
