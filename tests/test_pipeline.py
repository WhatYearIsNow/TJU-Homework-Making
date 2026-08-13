from pathlib import Path
from types import SimpleNamespace

import numpy as np

from app.config import default_config
from app.models import CroppedProblem, LayoutTemplateSpec, PageImage, ProblemRegion
from app.pipeline import PipelineProgress, run_pipeline


def test_run_pipeline_emits_progress_events(monkeypatch, tmp_path: Path) -> None:
    page = PageImage(
        source_path=tmp_path / "input.png",
        page_index=0,
        width=100,
        height=120,
        dpi=220,
        image=np.full((120, 100, 3), 255, dtype=np.uint8),
    )
    region = ProblemRegion(page_index=0, bbox=(0, 0, 100, 120))
    crop = CroppedProblem(
        problem_id="t1",
        page_index=0,
        original_bbox=(0, 0, 100, 120),
        cropped_bbox=(0, 0, 100, 120),
        image=np.full((120, 100, 3), 255, dtype=np.uint8),
    )

    monkeypatch.setattr("app.pipeline.load_input_pages", lambda input_paths, config: [page])
    monkeypatch.setattr(
        "app.pipeline.detect_problem_regions",
        lambda page, config: SimpleNamespace(page=page, candidate_regions=[region], refined_regions=[region]),
    )
    monkeypatch.setattr("app.pipeline.crop_problem_regions", lambda page, regions, config: [crop])
    monkeypatch.setattr(
        "app.pipeline.paginate_problems",
        lambda crops, config, template_spec=None: [SimpleNamespace(page_number=1)],
    )
    monkeypatch.setattr(
        "app.pipeline.render_pdf",
        lambda output_path, pages, render_config, header_config, font_config, watermark_config: Path(output_path),
    )

    events: list[PipelineProgress] = []
    output_path = tmp_path / "result.pdf"
    config = default_config()

    result = run_pipeline(
        input_paths=[tmp_path / "input.png"],
        output_path=output_path,
        config=config,
        progress_callback=events.append,
    )

    assert result.output_path == output_path.resolve()
    assert events
    assert events[0].stage == "input"
    assert events[-1].stage == "done"
    assert events[-1].fraction == 1.0
    assert any(event.stage == "detect" for event in events)
    assert any(event.stage == "crop" for event in events)
    assert any(event.stage == "layout" for event in events)
    assert any(event.stage == "render" for event in events)


def test_run_pipeline_resolves_optional_layout_template(monkeypatch, tmp_path: Path) -> None:
    page = PageImage(
        source_path=tmp_path / "input.png",
        page_index=0,
        width=100,
        height=120,
        dpi=220,
        image=np.full((120, 100, 3), 255, dtype=np.uint8),
    )
    region = ProblemRegion(page_index=0, bbox=(0, 0, 100, 120))
    crop = CroppedProblem(
        problem_id="t1",
        page_index=0,
        original_bbox=(0, 0, 100, 120),
        cropped_bbox=(0, 0, 100, 120),
        image=np.full((120, 100, 3), 255, dtype=np.uint8),
    )
    template_spec = LayoutTemplateSpec(
        source_path=tmp_path / "template.pdf",
        page_size_pt=(595.2756, 841.8898),
        outer_frame=(80.0, 86.0, 510.0, 770.0),
        header_frame=(80.0, 784.0, 510.0, 804.0),
        column_boundaries=(80.0, 295.0, 510.0),
        row_boundaries=(86.0, 428.0, 770.0),
    )

    monkeypatch.setattr("app.pipeline.load_input_pages", lambda input_paths, config: [page])
    monkeypatch.setattr(
        "app.pipeline.detect_problem_regions",
        lambda page, config: SimpleNamespace(page=page, candidate_regions=[region], refined_regions=[region]),
    )
    monkeypatch.setattr("app.pipeline.crop_problem_regions", lambda page, regions, config: [crop])
    monkeypatch.setattr("app.pipeline.resolve_layout_template", lambda config: template_spec)

    captured_template_specs: list[LayoutTemplateSpec | None] = []

    def fake_paginate(crops, config, template_spec=None):
        captured_template_specs.append(template_spec)
        return [SimpleNamespace(page_number=1)]

    monkeypatch.setattr("app.pipeline.paginate_problems", fake_paginate)
    monkeypatch.setattr(
        "app.pipeline.render_pdf",
        lambda output_path, pages, render_config, header_config, font_config, watermark_config: Path(output_path),
    )

    events: list[PipelineProgress] = []
    output_path = tmp_path / "result.pdf"
    config = default_config()
    config.layout.template_path = str(tmp_path / "template.pdf")

    result = run_pipeline(
        input_paths=[tmp_path / "input.png"],
        output_path=output_path,
        config=config,
        progress_callback=events.append,
    )

    assert result.template_spec == template_spec
    assert captured_template_specs == [template_spec]
    assert any(event.stage == "template" for event in events)
