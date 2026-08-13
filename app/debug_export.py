"""Debug artifact exporters for intermediate pipeline outputs."""

from __future__ import annotations

from math import ceil
from pathlib import Path

import cv2
import numpy as np

from app.config import DebugConfig
from app.detect_regions import DetectionResult
from app.io_utils import import_pymupdf, write_image
from app.models import CroppedProblem, PageImage, ProblemRegion


def prepare_debug_dir(base_dir: str | Path, output_path: str | Path) -> Path:
    """Create a stable debug directory for one output PDF."""

    root = Path(base_dir).expanduser().resolve()
    run_dir = root / Path(output_path).stem
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def export_detection_boxes(
    detection_results: list[DetectionResult],
    output_dir: str | Path,
) -> list[Path]:
    """Export per-page images with raw candidate boxes overlaid."""

    directory = Path(output_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)

    exported: list[Path] = []
    for result in detection_results:
        image = draw_problem_regions(
            page=result.page,
            regions=result.candidate_regions,
            color=(0, 140, 255),
            title="candidate",
        )
        path = directory / f"page_{result.page.page_index + 1:03d}_candidates.png"
        exported.append(write_image(path, image))
    return exported


def export_merged_boxes(
    detection_results: list[DetectionResult],
    output_dir: str | Path,
) -> list[Path]:
    """Export per-page images with refined/merged boxes overlaid."""

    directory = Path(output_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)

    exported: list[Path] = []
    for result in detection_results:
        image = draw_problem_regions(
            page=result.page,
            regions=result.refined_regions,
            color=(40, 170, 40),
            title="refined",
        )
        path = directory / f"page_{result.page.page_index + 1:03d}_refined.png"
        exported.append(write_image(path, image))
    return exported


def export_crop_contact_sheet(
    cropped_problems: list[CroppedProblem],
    output_path: str | Path,
    *,
    columns: int = 2,
    tile_width: int = 420,
    tile_height: int = 320,
) -> Path | None:
    """Export all cropped problems into one contact sheet image."""

    valid = [problem for problem in cropped_problems if problem.image is not None]
    if not valid:
        return None

    columns = max(1, int(columns))
    rows = ceil(len(valid) / columns)
    header_height = 28
    gutter = 14

    canvas_width = columns * tile_width + (columns + 1) * gutter
    canvas_height = rows * tile_height + (rows + 1) * gutter
    sheet = np.full((canvas_height, canvas_width, 3), 255, dtype=np.uint8)

    for index, problem in enumerate(valid):
        row = index // columns
        column = index % columns
        tile_x1 = gutter + column * (tile_width + gutter)
        tile_y1 = gutter + row * (tile_height + gutter)
        tile_x2 = tile_x1 + tile_width
        tile_y2 = tile_y1 + tile_height

        cv2.rectangle(sheet, (tile_x1, tile_y1), (tile_x2, tile_y2), (0, 0, 0), 1)
        cv2.putText(
            sheet,
            problem.problem_id,
            (tile_x1 + 8, tile_y1 + 18),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (40, 40, 40),
            1,
            cv2.LINE_AA,
        )

        image_area = (tile_x1 + 8, tile_y1 + header_height, tile_x2 - 8, tile_y2 - 8)
        fitted = fit_image_to_box(problem.image, image_area[2] - image_area[0], image_area[3] - image_area[1])
        offset_x = image_area[0] + ((image_area[2] - image_area[0] - fitted.shape[1]) // 2)
        offset_y = image_area[1] + ((image_area[3] - image_area[1] - fitted.shape[0]) // 2)
        sheet[offset_y : offset_y + fitted.shape[0], offset_x : offset_x + fitted.shape[1]] = fitted

    return write_image(output_path, sheet)


def export_render_preview(
    pdf_path: str | Path,
    output_dir: str | Path,
    *,
    max_pages: int = 3,
    scale: float = 1.5,
) -> list[Path]:
    """Render one or more PDF preview pages to PNG."""

    fitz = import_pymupdf()
    directory = Path(output_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    pdf_file = Path(pdf_path).expanduser().resolve()

    exported: list[Path] = []
    with fitz.open(str(pdf_file)) as document:
        preview_count = min(max_pages, document.page_count)
        matrix = fitz.Matrix(scale, scale)
        for page_index in range(preview_count):
            pixmap = document.load_page(page_index).get_pixmap(matrix=matrix, alpha=False)
            output_path = directory / f"preview_page_{page_index + 1:03d}.png"
            pixmap.save(str(output_path))
            exported.append(output_path)
    return exported


def export_debug_artifacts(
    *,
    detection_results: list[DetectionResult],
    cropped_problems: list[CroppedProblem],
    pdf_path: str | Path,
    output_dir: str | Path,
    debug_config: DebugConfig,
) -> dict[str, list[Path]]:
    """Export all enabled debug artifacts and return their file paths."""

    run_dir = Path(output_dir).expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    exported: dict[str, list[Path]] = {}

    if debug_config.export_detection_boxes:
        exported["candidate_boxes"] = export_detection_boxes(detection_results, run_dir / "detection_candidates")

    if debug_config.export_merged_boxes:
        exported["refined_boxes"] = export_merged_boxes(detection_results, run_dir / "detection_refined")

    if debug_config.export_crop_contact_sheet:
        contact_sheet = export_crop_contact_sheet(cropped_problems, run_dir / "crop_contact_sheet.png")
        exported["crop_contact_sheet"] = [contact_sheet] if contact_sheet is not None else []

    if debug_config.export_render_preview:
        exported["render_preview"] = export_render_preview(
            pdf_path,
            run_dir / "render_preview",
            max_pages=debug_config.max_preview_pages,
        )

    return exported


def draw_problem_regions(
    *,
    page: PageImage,
    regions: list[ProblemRegion],
    color: tuple[int, int, int],
    title: str,
) -> np.ndarray:
    """Overlay numbered regions on top of a page image."""

    if page.image is None:
        raise ValueError("PageImage.image is required for debug overlays.")

    overlay = page.image.copy()
    for index, region in enumerate(regions, start=1):
        x1, y1, x2, y2 = region.bbox
        cv2.rectangle(overlay, (x1, y1), (x2, y2), color, 2)
        label = f"{title}:{index}"
        label_y = max(20, y1 - 8)
        cv2.putText(
            overlay,
            label,
            (x1 + 4, label_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            color,
            2,
            cv2.LINE_AA,
        )
    return overlay


def fit_image_to_box(image: np.ndarray, max_width: int, max_height: int) -> np.ndarray:
    """Resize an image to fit inside a target box while preserving aspect ratio."""

    height, width = image.shape[:2]
    if width <= 0 or height <= 0:
        raise ValueError("Cannot fit an empty image into a debug contact sheet.")

    scale = min(max_width / width, max_height / height)
    scale = max(scale, 1e-6)
    resized = cv2.resize(
        image,
        (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
        interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC,
    )
    if resized.ndim == 2:
        resized = cv2.cvtColor(resized, cv2.COLOR_GRAY2BGR)
    return resized
