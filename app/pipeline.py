"""End-to-end processing pipeline orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from app.config import AppConfig, ensure_runtime_dirs
from app.cropper import crop_problem_regions
from app.debug_export import export_debug_artifacts, prepare_debug_dir
from app.detect_regions import DetectionResult, detect_problem_regions
from app.io_utils import load_input_pages
from app.layout_engine import PageLayout, paginate_problems
from app.models import CroppedProblem, LayoutTemplateSpec, PageImage, ProblemRegion
from app.pdf_renderer import render_pdf
from app.template_layout import resolve_layout_template


@dataclass(slots=True)
class PageProcessingResult:
    """All intermediate results associated with a single source page."""

    page: PageImage
    detection: DetectionResult
    selected_regions: list[ProblemRegion]
    cropped_problems: list[CroppedProblem]


@dataclass(slots=True)
class PipelineResult:
    """Resolved outputs from one pipeline execution."""

    input_paths: list[Path]
    output_path: Path
    page_results: list[PageProcessingResult]
    cropped_problems: list[CroppedProblem]
    page_layouts: list[PageLayout]
    template_spec: LayoutTemplateSpec | None = None
    debug_dir: Path | None = None
    debug_artifacts: dict[str, list[Path]] = field(default_factory=dict)


@dataclass(slots=True)
class PipelineProgress:
    """Structured progress event emitted during pipeline execution."""

    stage: str
    message: str
    fraction: float
    completed: int = 0
    total: int = 0


ProgressCallback = Callable[[PipelineProgress], None]


def run_pipeline(
    *,
    input_paths: Sequence[str | Path],
    output_path: str | Path,
    config: AppConfig,
    progress_callback: ProgressCallback | None = None,
) -> PipelineResult:
    """Run the full image/PDF to formatted PDF pipeline."""

    ensure_runtime_dirs(config)
    resolved_inputs = [Path(path).expanduser().resolve() for path in input_paths]
    resolved_output = Path(output_path).expanduser().resolve()
    resolved_output.parent.mkdir(parents=True, exist_ok=True)

    emit_progress(progress_callback, "input", "正在读取输入文件...", 0.05)
    pages = load_input_pages(resolved_inputs, config)
    if not pages:
        raise ValueError("没有读取到可用的输入页面。")

    template_spec: LayoutTemplateSpec | None = None
    if config.layout.template_path.strip():
        emit_progress(progress_callback, "template", "正在解析版式模板...", 0.10)
        template_spec = resolve_layout_template(config)

    page_results: list[PageProcessingResult] = []
    cropped_problems: list[CroppedProblem] = []
    page_count = len(pages)

    emit_progress(
        progress_callback,
        "input",
        f"已读取 {page_count} 个源页面。",
        0.12,
        completed=page_count,
        total=page_count,
    )

    for page_number, page in enumerate(pages, start=1):
        page_base = 0.12 + ((page_number - 1) / max(page_count, 1)) * 0.54
        page_detect_fraction = min(0.66, page_base + 0.16)
        page_crop_fraction = min(0.74, page_base + 0.30)

        emit_progress(
            progress_callback,
            "detect",
            f"正在检测第 {page_number}/{page_count} 页中的小题区域...",
            page_detect_fraction,
            completed=page_number - 1,
            total=page_count,
        )
        detection = detect_problem_regions(page, config.detection)
        selected_regions = select_regions_for_page(page, detection)

        emit_progress(
            progress_callback,
            "crop",
            f"正在裁剪第 {page_number}/{page_count} 页的小题...",
            page_crop_fraction,
            completed=page_number - 1,
            total=page_count,
        )
        crops = crop_problem_regions(page, selected_regions, config.crop)
        page_results.append(
            PageProcessingResult(
                page=page,
                detection=detection,
                selected_regions=selected_regions,
                cropped_problems=crops,
            )
        )
        cropped_problems.extend(crops)

    if not cropped_problems:
        raise ValueError("没有从输入内容中裁剪出可用的小题图片。")

    emit_progress(
        progress_callback,
        "layout",
        f"正在排版 {len(cropped_problems)} 道小题...",
        0.82,
        completed=len(cropped_problems),
        total=len(cropped_problems),
    )
    page_layouts = paginate_problems(cropped_problems, config.layout, template_spec=template_spec)
    if not page_layouts:
        raise ValueError("没有生成可用的页面排版结果。")

    emit_progress(
        progress_callback,
        "render",
        f"正在生成 PDF，共 {len(page_layouts)} 页...",
        0.92,
        completed=len(page_layouts),
        total=len(page_layouts),
    )
    render_pdf(
        resolved_output,
        page_layouts,
        config.to_render_config(),
        config.header,
        config.fonts,
        config.watermark,
    )

    debug_dir: Path | None = None
    debug_artifacts: dict[str, list[Path]] = {}
    if config.debug.enabled:
        emit_progress(progress_callback, "debug", "正在导出调试结果...", 0.97)
        debug_dir = prepare_debug_dir(config.io.debug_dir, resolved_output)
        debug_artifacts = export_debug_artifacts(
            detection_results=[item.detection for item in page_results],
            cropped_problems=cropped_problems,
            pdf_path=resolved_output,
            output_dir=debug_dir,
            debug_config=config.debug,
        )

    emit_progress(progress_callback, "done", "处理完成。", 1.0)

    return PipelineResult(
        input_paths=resolved_inputs,
        output_path=resolved_output,
        page_results=page_results,
        cropped_problems=cropped_problems,
        page_layouts=page_layouts,
        template_spec=template_spec,
        debug_dir=debug_dir,
        debug_artifacts=debug_artifacts,
    )


def select_regions_for_page(page: PageImage, detection: DetectionResult) -> list[ProblemRegion]:
    """Choose the best available set of regions for one page, with a safe fallback."""

    if detection.refined_regions:
        return detection.refined_regions
    if detection.candidate_regions:
        return detection.candidate_regions

    fallback_region = ProblemRegion(
        page_index=page.page_index,
        bbox=(0, 0, page.width, page.height),
        score=0.01,
        source="fallback_full_page",
        tags=("fallback",),
        metadata={"fallback_reason": "no_regions_detected"},
    )
    return [fallback_region]


def emit_progress(
    callback: ProgressCallback | None,
    stage: str,
    message: str,
    fraction: float,
    *,
    completed: int = 0,
    total: int = 0,
) -> None:
    """Safely emit a progress event if a callback is registered."""

    if callback is None:
        return
    callback(
        PipelineProgress(
            stage=stage,
            message=message,
            fraction=max(0.0, min(1.0, float(fraction))),
            completed=completed,
            total=total,
        )
    )
