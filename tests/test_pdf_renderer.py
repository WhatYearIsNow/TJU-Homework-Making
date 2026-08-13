from pathlib import Path

from PIL import Image

from app.config import WatermarkConfig
from app.pdf_renderer import build_watermark_asset, resolve_watermark_source_path


def test_build_watermark_asset_preserves_size_and_applies_opacity(tmp_path: Path) -> None:
    image_path = tmp_path / "mark.png"
    image = Image.new("RGBA", (120, 40), (0, 102, 204, 255))
    image.save(image_path)

    asset = build_watermark_asset(image_path, opacity=0.10)

    assert asset.width_px == 120
    assert asset.height_px == 40


def test_resolve_watermark_source_path_handles_relative_and_absolute_paths(tmp_path: Path) -> None:
    absolute = WatermarkConfig(asset_path=str(tmp_path / "mark.png"))
    assert resolve_watermark_source_path(absolute) == (tmp_path / "mark.png")

    relative = WatermarkConfig(asset_path="assets/branding/tju_logo_source.png")
    resolved = resolve_watermark_source_path(relative)
    assert resolved is not None
    assert resolved.name == "tju_logo_source.png"
