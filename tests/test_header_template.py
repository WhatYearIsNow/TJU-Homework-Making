import json
from pathlib import Path

from app.config import load_config
from app.header_template import build_header_template


def test_reference_identity_header_renders_single_centered_line() -> None:
    config = load_config(
        overrides={
            "header": {
                "style": "reference_identity",
                "identity": {
                    "student_id": "3025999999",
                    "class_name": "机制三班",
                    "student_name": "张三",
                    "font_size": 10.5,
                }
            }
        }
    )

    template = build_header_template(
        config.header,
        (84.6, 783.9, 494.6, 807.9),
        body_font_name="Helvetica",
        title_font_name="Helvetica-Bold",
        page_number=1,
    )

    assert template.style == "reference_identity"
    assert template.identity_line is not None
    assert template.identity_line.text == "3025999999 机制三班 张三"
    assert template.title is None
    assert template.fields == []
    assert template.decorations == []
    assert template.lines == []


def test_cli_style_overrides_identity_defaults_from_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "header": {
                    "style": "reference_identity",
                    "identity": {
                        "student_id": "default-id",
                        "class_name": "默认班级",
                        "student_name": "默认姓名",
                    },
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    config = load_config(
        config_path,
        overrides={
            "header": {
                "identity": {
                    "student_id": "override-id",
                    "student_name": "新的姓名",
                }
            }
        },
    )

    assert config.header.identity.student_id == "override-id"
    assert config.header.identity.class_name == "默认班级"
    assert config.header.identity.student_name == "新的姓名"
