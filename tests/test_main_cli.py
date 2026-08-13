import argparse

from app.main import build_cli_overrides


def test_build_cli_overrides_enables_strict_single_question_mode() -> None:
    args = argparse.Namespace(
        mode="normal",
        debug=None,
        watermark=None,
        dpi=None,
        per_page=4,
        strict_single_question=True,
        layout_template=None,
        template_lock_per_page=None,
        student_id=None,
        class_name=None,
        student_name=None,
    )

    overrides = build_cli_overrides(args)

    assert overrides["layout"]["problems_per_page"] == 1
    assert overrides["detection"]["strict_single_question_mode"] is True
    assert overrides["detection"]["enable_pdf_text_number_detection"] is True

