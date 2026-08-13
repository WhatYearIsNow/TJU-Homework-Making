"""ReportLab based PDF rendering."""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from app.config import FontConfig, HeaderConfig, WatermarkConfig
from app.header_template import HeaderLine, HeaderTemplate, HeaderText, build_header_template
from app.layout_engine import GridFrames, PageLayout, ProblemPlacement
from app.models import RenderConfig
from app.runtime_paths import resource_root


class PDFRenderError(RuntimeError):
    """Raised when PDF generation or image placement fails."""


@dataclass(slots=True)
class ResolvedFonts:
    """Resolved font names ready for ReportLab drawing."""

    body_font_name: str
    title_font_name: str
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class WatermarkAsset:
    """Prepared watermark image plus its original pixel size."""

    reader: ImageReader
    width_px: int
    height_px: int


def render_pdf(
    output_path: str | Path,
    pages: list[PageLayout],
    render_config: RenderConfig,
    header_config: HeaderConfig,
    font_config: FontConfig,
    watermark_config: WatermarkConfig,
) -> Path:
    """Render a fixed-layout PDF with header, grid, and problem images."""

    if not pages:
        raise ValueError("No page layouts were provided for PDF rendering.")

    destination = Path(output_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)

    fonts = resolve_fonts(font_config)
    base_page_size = pages[0].frames.page_size_pt
    image_cache: dict[str, ImageReader] = {}
    watermark_cache: dict[str, WatermarkAsset] = {}

    try:
        pdf = canvas.Canvas(str(destination), pagesize=base_page_size, pageCompression=1)
        pdf.setTitle(resolve_document_title(header_config))

        for page in pages:
            ensure_page_size_matches(page.frames.page_size_pt, render_config)
            header_template = build_header_template(
                header_config,
                page.frames.header_frame,
                body_font_name=fonts.body_font_name,
                title_font_name=fonts.title_font_name,
                page_number=page.page_number,
            )
            draw_background_watermark(
                pdf,
                page.frames,
                watermark_config,
                watermark_cache=watermark_cache,
            )
            draw_grid(pdf, page.frames, render_config, header_config=header_config)
            draw_header(pdf, header_template)
            for placement in page.placements:
                place_problem_image(pdf, placement, image_cache=image_cache)
            pdf.showPage()

        pdf.save()
    except Exception as exc:  # pragma: no cover - explicit wrapping for user-facing clarity
        raise PDFRenderError(f"Failed to render PDF to {destination}: {exc}") from exc

    return destination


def draw_header(pdf: canvas.Canvas, header: HeaderTemplate) -> None:
    """Draw the resolved header template onto the current PDF page."""

    for line in header.lines:
        draw_line(pdf, line)

    if header.identity_line is not None:
        draw_text(pdf, header.identity_line)
    if header.title is not None:
        draw_text(pdf, header.title)
    if header.subtitle is not None:
        draw_text(pdf, header.subtitle)

    for field in header.fields:
        draw_text(pdf, field.label_text)
        draw_line(pdf, field.underline)

    for text in header.decorations:
        draw_text(pdf, text)


def draw_grid(
    pdf: canvas.Canvas,
    frames: GridFrames,
    render_config: RenderConfig,
    *,
    header_config: HeaderConfig,
) -> None:
    """Draw the page outer frame, header divider, and 2x2 cell borders."""

    pdf.setStrokeColorRGB(0.0, 0.0, 0.0)
    pdf.setLineWidth(render_config.grid_line_width_pt)

    draw_rect(pdf, frames.outer_frame)
    if header_config.style == "legacy_fields":
        draw_line(
            pdf,
            HeaderLine(
                x1=frames.header_frame[0],
                y1=frames.header_frame[1],
                x2=frames.header_frame[2],
                y2=frames.header_frame[1],
                line_width=render_config.grid_line_width_pt,
            ),
        )

    for cell in frames.cells:
        draw_rect(pdf, cell.frame)


def draw_background_watermark(
    pdf: canvas.Canvas,
    frames: GridFrames,
    watermark_config: WatermarkConfig,
    *,
    watermark_cache: dict[str, WatermarkAsset] | None = None,
) -> None:
    """Draw a faint Tianjin University watermark behind the page content."""

    if not watermark_config.enabled:
        return

    source_path = resolve_watermark_source_path(watermark_config)
    if source_path is None or not source_path.exists():
        return

    cache = watermark_cache if watermark_cache is not None else {}
    cache_key = f"{source_path}:{watermark_config.tint_hex}:{watermark_config.opacity:.4f}"
    asset = cache.get(cache_key)
    if asset is None:
        asset = build_watermark_asset(
            source_path,
            opacity=watermark_config.opacity,
            tint_hex=watermark_config.tint_hex,
        )
        cache[cache_key] = asset

    body_x1, body_y1, body_x2, body_y2 = frames.outer_frame
    body_width = body_x2 - body_x1
    body_height = body_y2 - body_y1
    max_width = max(40.0, body_width * watermark_config.width_ratio)
    max_height = max(28.0, body_height * watermark_config.max_height_ratio)
    scale = min(
        max_width / max(asset.width_px, 1),
        max_height / max(asset.height_px, 1),
    )
    draw_width = asset.width_px * scale
    draw_height = asset.height_px * scale
    center_y = body_y1 + body_height * watermark_config.center_y_ratio
    draw_x = body_x1 + (body_width - draw_width) / 2.0
    draw_y = center_y - draw_height / 2.0

    pdf.drawImage(
        asset.reader,
        draw_x,
        draw_y,
        width=draw_width,
        height=draw_height,
        preserveAspectRatio=False,
        mask="auto",
    )


def place_problem_image(
    pdf: canvas.Canvas,
    placement: ProblemPlacement,
    *,
    image_cache: dict[str, ImageReader] | None = None,
) -> None:
    """Place one cropped problem image inside its resolved frame."""

    if placement.problem is None or placement.image_frame is None:
        return
    if placement.problem.image is None:
        raise PDFRenderError(f"Problem {placement.problem.problem_id} is missing image data.")

    cache = image_cache if image_cache is not None else {}
    image_id = placement.problem.problem_id
    reader = cache.get(image_id)
    if reader is None:
        reader = convert_image_to_reader(placement.problem.image)
        cache[image_id] = reader

    x1, y1, x2, y2 = placement.image_frame
    pdf.drawImage(
        reader,
        x1,
        y1,
        width=x2 - x1,
        height=y2 - y1,
        preserveAspectRatio=False,
        mask="auto",
    )


def draw_text(pdf: canvas.Canvas, text: HeaderText) -> None:
    """Draw one resolved text element."""

    pdf.setFont(text.font_name, text.font_size)
    if text.align == "center":
        pdf.drawCentredString(text.x, text.y, text.text)
    elif text.align == "right":
        pdf.drawRightString(text.x, text.y, text.text)
    else:
        pdf.drawString(text.x, text.y, text.text)


def draw_line(pdf: canvas.Canvas, line: HeaderLine) -> None:
    """Draw one resolved line element."""

    pdf.setLineWidth(line.line_width)
    pdf.line(line.x1, line.y1, line.x2, line.y2)


def draw_rect(pdf: canvas.Canvas, frame: tuple[float, float, float, float]) -> None:
    """Draw a rectangle from an absolute frame bbox."""

    x1, y1, x2, y2 = frame
    pdf.rect(x1, y1, x2 - x1, y2 - y1, stroke=1, fill=0)


def convert_image_to_reader(image: np.ndarray) -> ImageReader:
    """Convert a numpy image into a ReportLab ImageReader."""

    if image.ndim == 2:
        pil_image = Image.fromarray(image)
    else:
        rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        pil_image = Image.fromarray(rgb_image)

    buffer = BytesIO()
    pil_image.save(buffer, format="PNG")
    buffer.seek(0)
    return ImageReader(buffer)


def build_watermark_asset(source_path: Path, opacity: float, tint_hex: str = "#9fb4cf") -> WatermarkAsset:
    """Load an RGBA watermark image and soften it for page-background use."""

    with Image.open(source_path) as image:
        rgba = image.convert("RGBA")
        alpha = rgba.getchannel("A")
        scaled_alpha = alpha.point(lambda value: max(0, min(255, int(value * opacity))))
        red, green, blue = parse_hex_color(tint_hex)
        tinted = Image.new("RGBA", rgba.size, (red, green, blue, 0))
        tinted.putalpha(scaled_alpha)

        buffer = BytesIO()
        tinted.save(buffer, format="PNG")
        buffer.seek(0)
        return WatermarkAsset(
            reader=ImageReader(buffer),
            width_px=tinted.width,
            height_px=tinted.height,
        )


def resolve_watermark_source_path(watermark_config: WatermarkConfig) -> Path | None:
    """Resolve the watermark asset path from config and bundled resources."""

    raw_path = watermark_config.asset_path.strip()
    if not raw_path:
        return None
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    return (resource_root() / candidate).resolve()


def parse_hex_color(value: str) -> tuple[int, int, int]:
    """Parse a hex RGB color string into integer components."""

    normalized = value.strip().lstrip("#")
    if len(normalized) != 6:
        return (159, 180, 207)
    try:
        return (
            int(normalized[0:2], 16),
            int(normalized[2:4], 16),
            int(normalized[4:6], 16),
        )
    except ValueError:
        return (159, 180, 207)


def resolve_document_title(header_config: HeaderConfig) -> str:
    """Return a stable PDF title for the current header style."""

    if header_config.style == "reference_identity":
        parts = [
            header_config.identity.student_id.strip(),
            header_config.identity.class_name.strip(),
            header_config.identity.student_name.strip(),
        ]
        identity_text = " ".join(part for part in parts if part)
        return identity_text or "题目图片自动排版"
    return header_config.title


def resolve_fonts(font_config: FontConfig) -> ResolvedFonts:
    """Resolve and register robust font fallbacks for body and title text."""

    body_font_name, body_notes = resolve_single_font(
        [font_config.preferred_font_name, *font_config.fallback_font_names],
        font_config,
    )
    title_font_name, title_notes = resolve_single_font(
        [font_config.title_font_name, font_config.preferred_font_name, *font_config.fallback_font_names],
        font_config,
    )
    return ResolvedFonts(
        body_font_name=body_font_name,
        title_font_name=title_font_name,
        notes=[*body_notes, *title_notes],
    )


def resolve_single_font(candidates: list[str], font_config: FontConfig) -> tuple[str, list[str]]:
    """Resolve one usable font from candidate names and configured directories."""

    notes: list[str] = []
    for candidate in candidates:
        resolved = resolve_builtin_font(candidate)
        if resolved is not None:
            return resolved, notes

        font_path = find_font_file(candidate, font_config)
        if font_path is None:
            notes.append(f"Font not found on disk: {candidate}")
            continue

        alias = register_truetype_font(candidate, font_path)
        if alias is not None:
            return alias, notes
        notes.append(f"Font file could not be registered: {font_path}")

    fallback = ensure_cid_font("STSong-Light")
    return fallback, [*notes, "Fell back to STSong-Light."]


def resolve_builtin_font(name: str) -> str | None:
    """Return a built-in or already registered font if available."""

    if not name:
        return None
    if name in pdfmetrics.standardFonts:
        return name
    try:
        pdfmetrics.getFont(name)
        return name
    except KeyError:
        pass
    if name == "STSong-Light":
        return ensure_cid_font(name)
    return None


def ensure_cid_font(name: str) -> str:
    """Register a CID font once and return its name."""

    try:
        pdfmetrics.getFont(name)
        return name
    except KeyError:
        pdfmetrics.registerFont(UnicodeCIDFont(name))
        return name


def register_truetype_font(font_name: str, font_path: Path) -> str | None:
    """Register a TTF/OTF/TTC font file and return the alias used by ReportLab."""

    alias = f"font_{normalize_font_token(font_name)}_{abs(hash(str(font_path))) % 100000}"
    try:
        pdfmetrics.getFont(alias)
        return alias
    except KeyError:
        pass

    try:
        pdfmetrics.registerFont(TTFont(alias, str(font_path)))
        return alias
    except Exception:
        return None


def find_font_file(font_name: str, font_config: FontConfig) -> Path | None:
    """Search configured font paths and directories for a candidate font file."""

    normalized_name = normalize_font_token(font_name)
    explicit_paths = [Path(path) for path in font_config.user_font_paths]
    for path in explicit_paths:
        if path.exists() and path.is_file():
            if normalized_name in normalize_font_token(path.stem) or normalize_font_token(path.stem) in normalized_name:
                return path

    supported_suffixes = {".ttf", ".ttc", ".otf"}
    for directory in font_config.search_dirs:
        directory_path = Path(directory)
        if not directory_path.exists() or not directory_path.is_dir():
            continue
        for path in directory_path.iterdir():
            if not path.is_file() or path.suffix.lower() not in supported_suffixes:
                continue
            stem = normalize_font_token(path.stem)
            if normalized_name in stem or stem in normalized_name:
                return path
    return None


def normalize_font_token(value: str) -> str:
    """Normalize a font name for loose matching."""

    return "".join(ch for ch in value.lower() if ch.isalnum())


def ensure_page_size_matches(page_size: tuple[float, float], render_config: RenderConfig) -> None:
    """Verify that the layout page size matches renderer expectations."""

    expected = (render_config.page_width_pt, render_config.page_height_pt)
    if not nearly_equal(page_size[0], expected[0]) or not nearly_equal(page_size[1], expected[1]):
        raise PDFRenderError(
            f"Page size mismatch. Layout uses {page_size}, renderer expects {expected}."
        )


def nearly_equal(first: float, second: float, *, tolerance: float = 0.5) -> bool:
    """Return True when two floats are close within a small point tolerance."""

    return abs(first - second) <= tolerance
