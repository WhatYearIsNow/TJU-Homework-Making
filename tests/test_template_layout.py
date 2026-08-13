from pathlib import Path

import cv2
import numpy as np

from app.config import default_config
from app.template_layout import resolve_layout_template


def build_template_image(width: int = 700, height: int = 1000) -> np.ndarray:
    image = np.full((height, width, 3), 255, dtype=np.uint8)

    outer_left, outer_right = 100, 600
    outer_top, outer_bottom = 120, 900
    center_x = 350
    center_y = 510

    cv2.rectangle(image, (outer_left, outer_top), (outer_right, outer_bottom), (0, 0, 0), thickness=6)
    cv2.line(image, (center_x, outer_top), (center_x, outer_bottom), (0, 0, 0), thickness=5)
    cv2.line(image, (outer_left, center_y), (outer_right, center_y), (0, 0, 0), thickness=5)

    cv2.rectangle(image, (220, 62), (480, 86), (0, 0, 0), thickness=-1)
    return image


def test_resolve_layout_template_infers_grid_from_template_image(tmp_path: Path) -> None:
    template_path = tmp_path / "template.png"
    assert cv2.imwrite(str(template_path), build_template_image())

    config = default_config()
    config.layout.template_path = str(template_path)

    template = resolve_layout_template(config)

    assert template is not None
    assert template.rows == 2
    assert template.columns == 2
    assert template.problems_per_page == 4
    assert template.header_frame[1] > template.outer_frame[3]
    assert template.metadata["template_page_name"] == "template.png"
