"""Parameterized header template definitions for stable PDF rendering."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import HeaderConfig
from app.models import FloatBBox


@dataclass(slots=True)
class HeaderText:
    """Resolved header text element with absolute PDF coordinates."""

    text: str
    x: float
    y: float
    font_name: str
    font_size: float
    align: str = "left"


@dataclass(slots=True)
class HeaderLine:
    """Resolved header line element with absolute PDF coordinates."""

    x1: float
    y1: float
    x2: float
    y2: float
    line_width: float


@dataclass(slots=True)
class HeaderFieldTemplate:
    """Resolved field label and underline inside the legacy header area."""

    label: str
    label_text: HeaderText
    underline: HeaderLine
    field_frame: FloatBBox


@dataclass(slots=True)
class HeaderTemplate:
    """Fully resolved page header template."""

    style: str
    frame: FloatBBox
    identity_line: HeaderText | None = None
    title: HeaderText | None = None
    subtitle: HeaderText | None = None
    fields: list[HeaderFieldTemplate] = field(default_factory=list)
    lines: list[HeaderLine] = field(default_factory=list)
    decorations: list[HeaderText] = field(default_factory=list)


def build_header_template(
    config: HeaderConfig,
    header_frame: FloatBBox,
    *,
    body_font_name: str,
    title_font_name: str,
    page_number: int | None = None,
) -> HeaderTemplate:
    """Resolve a parameterized header config into absolute text and line elements."""

    if config.style == "reference_identity":
        return build_reference_identity_template(
            config,
            header_frame,
            body_font_name=body_font_name,
        )

    return build_legacy_header_template(
        config,
        header_frame,
        body_font_name=body_font_name,
        title_font_name=title_font_name,
        page_number=page_number,
    )


def build_reference_identity_template(
    config: HeaderConfig,
    header_frame: FloatBBox,
    *,
    body_font_name: str,
) -> HeaderTemplate:
    """Build the one-line identity header used by the reference PDF style."""

    x1, y1, x2, _ = header_frame
    parts = [
        config.identity.student_id.strip(),
        config.identity.class_name.strip(),
        config.identity.student_name.strip(),
    ]
    identity_text = " ".join(part for part in parts if part)
    identity_line = None
    if identity_text:
        identity_line = HeaderText(
            text=identity_text,
            x=(x1 + x2) / 2.0,
            y=y1,
            font_name=body_font_name,
            font_size=config.identity.font_size,
            align="center",
        )

    return HeaderTemplate(
        style=config.style,
        frame=header_frame,
        identity_line=identity_line,
    )


def build_legacy_header_template(
    config: HeaderConfig,
    header_frame: FloatBBox,
    *,
    body_font_name: str,
    title_font_name: str,
    page_number: int | None = None,
) -> HeaderTemplate:
    """Build the original title-plus-fields header layout."""

    x1, y1, x2, y2 = header_frame
    title_y = y2 - config.top_padding_pt - config.title_font_size + config.title_y_offset_pt
    title = HeaderText(
        text=config.title,
        x=(x1 + x2) / 2.0,
        y=title_y,
        font_name=title_font_name,
        font_size=config.title_font_size,
        align="center",
    )

    subtitle = None
    if config.subtitle:
        subtitle = HeaderText(
            text=config.subtitle,
            x=(x1 + x2) / 2.0,
            y=title_y - max(16.0, config.body_font_size * 1.6),
            font_name=body_font_name,
            font_size=config.body_font_size,
            align="center",
        )

    field_band_bottom = y1 + config.bottom_padding_pt
    underline_y = field_band_bottom + config.underline_offset_pt
    label_y = underline_y + max(3.0, config.body_font_size * 0.12)
    field_top = max(y1, label_y - 2.0)

    field_frames = compute_field_frames(
        header_frame=header_frame,
        field_count=len(config.fields),
        width_ratios=[item.width_ratio for item in config.fields],
        gap_pt=config.field_gap_pt,
        top_y=field_top,
        bottom_y=field_band_bottom + config.body_font_size + 6.0,
    )

    fields: list[HeaderFieldTemplate] = []
    for item, field_frame in zip(config.fields, field_frames, strict=False):
        fx1, _, fx2, _ = field_frame
        label_text = HeaderText(
            text=item.label,
            x=fx1,
            y=label_y,
            font_name=body_font_name,
            font_size=config.body_font_size,
            align="left",
        )
        label_block_width = estimate_label_block_width(item.label, config.body_font_size)
        underline_x1 = min(fx2 - 18.0, fx1 + label_block_width)
        underline = HeaderLine(
            x1=underline_x1,
            y1=underline_y,
            x2=fx2,
            y2=underline_y,
            line_width=config.line_width_pt,
        )
        fields.append(
            HeaderFieldTemplate(
                label=item.label,
                label_text=label_text,
                underline=underline,
                field_frame=field_frame,
            )
        )

    structural_lines = [
        HeaderLine(
            x1=x1,
            y1=y1,
            x2=x2,
            y2=y1,
            line_width=config.line_width_pt,
        )
    ]

    decorations: list[HeaderText] = []
    if page_number is not None:
        decorations.append(
            HeaderText(
                text=f"Page {page_number}",
                x=x2,
                y=y2 - config.top_padding_pt + 1.0,
                font_name=body_font_name,
                font_size=max(9.0, config.body_font_size - 0.5),
                align="right",
            )
        )

    return HeaderTemplate(
        style=config.style,
        frame=header_frame,
        title=title,
        subtitle=subtitle,
        fields=fields,
        lines=structural_lines,
        decorations=decorations,
    )


def compute_field_frames(
    *,
    header_frame: FloatBBox,
    field_count: int,
    width_ratios: list[float],
    gap_pt: float,
    top_y: float,
    bottom_y: float,
) -> list[FloatBBox]:
    """Compute resolved field frames across the header width."""

    if field_count <= 0:
        return []

    x1, _, x2, _ = header_frame
    usable_width = max(0.0, (x2 - x1) - (max(0, field_count - 1) * gap_pt))
    normalized_ratios = normalize_width_ratios(width_ratios, field_count)

    frames: list[FloatBBox] = []
    cursor_x = x1
    for index in range(field_count):
        field_width = usable_width * normalized_ratios[index]
        frames.append((cursor_x, top_y, cursor_x + field_width, bottom_y))
        cursor_x += field_width + gap_pt
    return frames


def normalize_width_ratios(width_ratios: list[float], field_count: int) -> list[float]:
    """Normalize configured field width ratios or fall back to equal widths."""

    if field_count <= 0:
        return []

    if len(width_ratios) != field_count:
        return [1.0 / field_count] * field_count

    total = sum(max(0.0, ratio) for ratio in width_ratios)
    if total <= 0.0:
        return [1.0 / field_count] * field_count

    return [max(0.0, ratio) / total for ratio in width_ratios]


def estimate_label_block_width(label: str, font_size: float) -> float:
    """Estimate label width plus a small gap before the underline."""

    effective_chars = max(2.0, len(label) * 1.15)
    return max(22.0, effective_chars * font_size) + 5.0
