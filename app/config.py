"""Application configuration management for the PDF exam formatter."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from app.models import ProcessingMode, RenderConfig
from app.runtime_paths import app_home, resource_root

ConfigPath = str | Path
HeaderStyle = Literal["reference_identity", "legacy_fields"]


@dataclass(slots=True)
class IOConfig:
    """Input and output related settings."""

    image_dpi: int = 220
    pdf_dpi: int = 220
    output_dir: Path = field(default_factory=lambda: app_home() / "outputs")
    debug_dir: Path = field(default_factory=lambda: app_home() / "debug")
    cache_dir: Path = field(default_factory=lambda: app_home() / ".cache")
    supported_image_suffixes: tuple[str, ...] = (
        ".png",
        ".jpg",
        ".jpeg",
        ".bmp",
        ".tif",
        ".tiff",
        ".webp",
    )
    supported_pdf_suffixes: tuple[str, ...] = (".pdf",)


@dataclass(slots=True)
class DetectionConfig:
    """Traditional vision based detection thresholds."""

    strict_single_question_mode: bool = False
    strict_projection_fallback_enabled: bool = True
    strict_projection_row_density_threshold: float = 0.004
    strict_projection_gap_ratio: float = 0.014
    strict_projection_min_band_height_ratio: float = 0.020
    strict_projection_side_padding_ratio: float = 0.030
    strict_projection_vertical_padding_ratio: float = 0.010
    strict_projection_min_bands: int = 2
    grayscale_blur_kernel: int = 3
    threshold_method: Literal["otsu", "adaptive"] = "otsu"
    adaptive_block_size: int = 35
    adaptive_c: int = 15
    invert_binary: bool = True
    close_kernel_width: int = 25
    close_kernel_height: int = 17
    close_iterations: int = 1
    min_region_area_ratio: float = 0.0020
    min_region_width_ratio: float = 0.08
    min_region_height_ratio: float = 0.035
    max_noise_aspect_ratio: float = 12.0
    merge_x_gap_ratio: float = 0.040
    merge_y_gap_ratio: float = 0.028
    merge_overlap_ratio: float = 0.12
    merge_small_attachment_area_ratio: float = 0.35
    merge_peer_region_area_ratio: float = 0.45
    merge_peer_min_region_area_ratio: float = 0.015
    row_group_tolerance_ratio: float = 0.040
    allow_sparse_problem_expansion: bool = True
    enable_grid_detection: bool = True
    grid_line_kernel_ratio: float = 0.35
    grid_projection_threshold_ratio: float = 0.45
    grid_line_merge_gap_px: int = 10
    grid_center_tolerance_ratio: float = 0.18
    grid_outer_margin_ratio: float = 0.25
    grid_support_span_ratio: float = 0.55
    enable_pdf_text_number_detection: bool = True
    pdf_text_min_questions: int = 2
    pdf_text_side_margin_ratio: float = 0.035
    pdf_text_top_padding_ratio: float = 0.005
    pdf_text_bottom_padding_ratio: float = 0.012
    pdf_text_anchor_max_step: int = 3
    pdf_text_bottom_trim_threshold: int = 245


@dataclass(slots=True)
class CropConfig:
    """Safety margins and whitespace trimming policy."""

    expand_left_ratio: float = 0.040
    expand_right_ratio: float = 0.050
    expand_top_ratio: float = 0.050
    expand_bottom_ratio: float = 0.095
    min_padding_px: int = 12
    max_padding_ratio: float = 0.120
    whitespace_threshold: int = 245
    trim_scan_step: int = 2
    sparse_extra_margin_ratio: float = 0.030
    preserve_round_shapes: bool = True
    tall_problem_height_bias: float = 1.08
    wide_problem_width_bias: float = 1.06


@dataclass(slots=True)
class LayoutConfig:
    """Fixed page layout and cell fitting rules."""

    page_size: str = "A4"
    problems_per_page: int = 4
    layout_pattern_mode: str = "auto"
    custom_row_pattern: str = ""
    template_path: str = ""
    template_dpi: int = 220
    template_lock_per_page: bool = True
    rows: int = 2
    columns: int = 2
    outer_margin_pt: float = 24.0
    header_height_pt: float = 96.0
    cell_padding_pt: float = 12.0
    grid_line_width_pt: float = 1.0
    page_margin_left_pt: float = 84.6
    page_margin_right_pt: float = 100.7
    page_margin_top_pt: float = 72.0
    page_margin_bottom_pt: float = 86.1
    center_divider_x_pt: float = 289.6
    center_divider_y_pt: float = 432.9
    header_text_gap_pt: float = 14.0
    blank_space_ratio_min: float = 0.50
    blank_space_ratio_target: float = 0.60
    blank_space_ratio_max: float = 0.67
    standard_problem_height_ratio: float = 0.38
    wide_problem_height_ratio: float = 0.36
    round_problem_height_ratio: float = 0.39
    tall_problem_height_ratio: float = 0.46
    blank_write_ratio: float = 0.19
    min_image_fill_ratio: float = 0.78
    target_image_fill_ratio: float = 0.81
    max_image_fill_ratio: float = 0.84
    tall_problem_fill_ratio: float = 0.84
    wide_problem_fill_ratio: float = 0.80


@dataclass(slots=True)
class FontConfig:
    """Font lookup and fallback strategy for Chinese and Latin text."""

    preferred_font_name: str = "SimSun"
    title_font_name: str = "SimHei"
    fallback_font_names: tuple[str, ...] = ("Microsoft YaHei", "STSong-Light", "Helvetica")
    user_font_paths: list[Path] = field(default_factory=list)
    search_dirs: list[Path] = field(
        default_factory=lambda: [
            resource_root() / "assets" / "fonts",
            Path("C:/Windows/Fonts"),
        ]
    )


@dataclass(slots=True)
class WatermarkConfig:
    """Background watermark settings used by the PDF renderer."""

    enabled: bool = True
    asset_path: str = "assets/branding/tju_logo_source.png"
    tint_hex: str = "#9fb4cf"
    opacity: float = 0.24
    width_ratio: float = 0.62
    max_height_ratio: float = 0.20
    center_y_ratio: float = 0.52


@dataclass(slots=True)
class HeaderFieldConfig:
    """Single legacy header field label such as '姓名' or '班级'."""

    label: str
    width_ratio: float = 0.22


@dataclass(slots=True)
class HeaderIdentityConfig:
    """Identity text used by the reference PDF style."""

    student_id: str = ""
    class_name: str = ""
    student_name: str = ""
    font_size: float = 10.5


@dataclass(slots=True)
class HeaderConfig:
    """Parameterized header template configuration."""

    style: HeaderStyle = "reference_identity"
    identity: HeaderIdentityConfig = field(default_factory=HeaderIdentityConfig)
    title: str = "题目练习"
    subtitle: str | None = None
    title_font_size: float = 17.0
    body_font_size: float = 10.5
    top_padding_pt: float = 16.0
    bottom_padding_pt: float = 12.0
    title_y_offset_pt: float = 0.0
    field_gap_pt: float = 16.0
    underline_offset_pt: float = 3.0
    line_width_pt: float = 1.0
    fields: list[HeaderFieldConfig] = field(
        default_factory=lambda: [
            HeaderFieldConfig(label="姓名"),
            HeaderFieldConfig(label="班级"),
            HeaderFieldConfig(label="日期"),
            HeaderFieldConfig(label="得分"),
        ]
    )


@dataclass(slots=True)
class DebugConfig:
    """Debug export controls."""

    enabled: bool = False
    export_detection_boxes: bool = True
    export_merged_boxes: bool = True
    export_crop_contact_sheet: bool = True
    export_render_preview: bool = True
    max_preview_pages: int = 3


@dataclass(slots=True)
class PerformanceConfig:
    """Mode-dependent runtime behavior."""

    mode: ProcessingMode = "normal"
    enable_cache: bool = True
    export_debug_by_default: bool = False
    skip_minor_refinements_in_fast: bool = True
    local_fix_for_outliers_only: bool = True


@dataclass(slots=True)
class AppConfig:
    """Top-level application configuration."""

    io: IOConfig = field(default_factory=IOConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    crop: CropConfig = field(default_factory=CropConfig)
    layout: LayoutConfig = field(default_factory=LayoutConfig)
    fonts: FontConfig = field(default_factory=FontConfig)
    watermark: WatermarkConfig = field(default_factory=WatermarkConfig)
    header: HeaderConfig = field(default_factory=HeaderConfig)
    debug: DebugConfig = field(default_factory=DebugConfig)
    performance: PerformanceConfig = field(default_factory=PerformanceConfig)

    def to_render_config(self) -> RenderConfig:
        """Convert the current layout and font settings into renderer settings."""

        base_font_size = (
            self.header.identity.font_size
            if self.header.style == "reference_identity"
            else self.header.body_font_size
        )
        return RenderConfig(
            grid_line_width_pt=self.layout.grid_line_width_pt,
            default_font_name=self.fonts.preferred_font_name,
            default_font_size=base_font_size,
            title_font_size=self.header.title_font_size,
            mode=self.performance.mode,
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON/YAML serializable representation of the config."""

        return _dataclass_to_dict(self)


def default_config() -> AppConfig:
    """Return the default application configuration."""

    return AppConfig()


def load_config(
    config_path: ConfigPath | None = None,
    *,
    header_config_path: ConfigPath | None = None,
    overrides: dict[str, Any] | None = None,
) -> AppConfig:
    """Load config from defaults plus optional YAML/JSON overrides."""

    merged = default_config().as_dict()

    if config_path is not None:
        config_mapping = _load_mapping_file(config_path)
        merged = deep_merge(merged, config_mapping)

    if header_config_path is not None:
        header_mapping = _load_mapping_file(header_config_path)
        merged = deep_merge(merged, {"header": header_mapping})

    if overrides:
        merged = deep_merge(merged, overrides)

    return _app_config_from_dict(merged)


def deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge a config mapping without mutating the inputs."""

    merged = dict(base)
    for key, value in updates.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def ensure_runtime_dirs(config: AppConfig) -> None:
    """Create output, debug, and cache directories if they do not already exist."""

    for path in (config.io.output_dir, config.io.debug_dir, config.io.cache_dir):
        Path(path).mkdir(parents=True, exist_ok=True)


def _load_mapping_file(path_like: ConfigPath) -> dict[str, Any]:
    """Load a JSON or YAML config mapping from disk."""

    path = Path(path_like)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    suffix = path.suffix.lower()
    raw_text = path.read_text(encoding="utf-8")
    if suffix == ".json":
        data = json.loads(raw_text)
    elif suffix in {".yaml", ".yml"}:
        data = yaml.safe_load(raw_text)
    else:
        raise ValueError(f"Unsupported config format: {path.suffix}. Use JSON or YAML.")

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Config file must contain a top-level object: {path}")
    return data


def _dataclass_to_dict(value: Any) -> Any:
    """Convert nested dataclasses to plain dicts while preserving Path values as strings."""

    if is_dataclass(value):
        return {key: _dataclass_to_dict(val) for key, val in asdict(value).items()}
    if isinstance(value, dict):
        return {key: _dataclass_to_dict(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_dataclass_to_dict(item) for item in value]
    if isinstance(value, tuple):
        return [_dataclass_to_dict(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _app_config_from_dict(data: dict[str, Any]) -> AppConfig:
    """Instantiate AppConfig from a nested mapping."""

    io = IOConfig(
        image_dpi=int(data["io"]["image_dpi"]),
        pdf_dpi=int(data["io"]["pdf_dpi"]),
        output_dir=Path(data["io"]["output_dir"]),
        debug_dir=Path(data["io"]["debug_dir"]),
        cache_dir=Path(data["io"]["cache_dir"]),
        supported_image_suffixes=tuple(data["io"]["supported_image_suffixes"]),
        supported_pdf_suffixes=tuple(data["io"]["supported_pdf_suffixes"]),
    )
    detection = DetectionConfig(**data["detection"])
    crop = CropConfig(**data["crop"])
    layout = LayoutConfig(**data["layout"])
    fonts = FontConfig(
        preferred_font_name=data["fonts"]["preferred_font_name"],
        title_font_name=data["fonts"]["title_font_name"],
        fallback_font_names=tuple(data["fonts"]["fallback_font_names"]),
        user_font_paths=[Path(path) for path in data["fonts"]["user_font_paths"]],
        search_dirs=[Path(path) for path in data["fonts"]["search_dirs"]],
    )
    watermark = WatermarkConfig(**data.get("watermark", {}))
    identity_data = data["header"].get("identity", {})
    header = HeaderConfig(
        style=data["header"].get("style", "reference_identity"),
        identity=HeaderIdentityConfig(
            student_id=str(identity_data.get("student_id", "")),
            class_name=str(identity_data.get("class_name", "")),
            student_name=str(identity_data.get("student_name", "")),
            font_size=float(identity_data.get("font_size", data["header"].get("body_font_size", 10.5))),
        ),
        title=data["header"].get("title", "题目练习"),
        subtitle=data["header"].get("subtitle"),
        title_font_size=float(data["header"].get("title_font_size", 17.0)),
        body_font_size=float(data["header"].get("body_font_size", 10.5)),
        top_padding_pt=float(data["header"].get("top_padding_pt", 16.0)),
        bottom_padding_pt=float(data["header"].get("bottom_padding_pt", 12.0)),
        title_y_offset_pt=float(data["header"].get("title_y_offset_pt", 0.0)),
        field_gap_pt=float(data["header"].get("field_gap_pt", 16.0)),
        underline_offset_pt=float(data["header"].get("underline_offset_pt", 3.0)),
        line_width_pt=float(data["header"].get("line_width_pt", 1.0)),
        fields=[
            HeaderFieldConfig(
                label=item["label"],
                width_ratio=float(item.get("width_ratio", 0.22)),
            )
            for item in data["header"].get("fields", [])
        ]
        or [
            HeaderFieldConfig(label="姓名"),
            HeaderFieldConfig(label="班级"),
            HeaderFieldConfig(label="日期"),
            HeaderFieldConfig(label="得分"),
        ],
    )
    debug = DebugConfig(**data["debug"])
    performance = PerformanceConfig(**data["performance"])
    return AppConfig(
        io=io,
        detection=detection,
        crop=crop,
        layout=layout,
        fonts=fonts,
        watermark=watermark,
        header=header,
        debug=debug,
        performance=performance,
    )
