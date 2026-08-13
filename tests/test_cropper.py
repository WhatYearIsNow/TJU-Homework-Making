import cv2
import numpy as np

from app.config import CropConfig
from app.cropper import safe_crop
from app.models import PageImage, ProblemRegion


def test_safe_crop_keeps_extra_bottom_margin_and_shrinks_white_border() -> None:
    image = np.full((900, 700, 3), 255, dtype=np.uint8)
    cv2.putText(image, "1", (105, 185), cv2.FONT_HERSHEY_SIMPLEX, 1.8, (0, 0, 0), 3)
    cv2.rectangle(image, (180, 160), (470, 430), (0, 0, 0), 3)
    cv2.arrowedLine(image, (230, 520), (430, 520), (0, 0, 0), 4, tipLength=0.05)

    page = PageImage(
        source_path="synthetic.png",
        page_index=0,
        width=700,
        height=900,
        dpi=220,
        image=image,
    )
    region = ProblemRegion(
        page_index=0,
        bbox=(120, 150, 500, 470),
        metadata={"fill_ratio": 0.08},
    )

    cropped = safe_crop(page, region, CropConfig(), problem_id="t1")

    assert cropped.cropped_bbox[0] <= region.bbox[0]
    assert cropped.cropped_bbox[1] < region.bbox[1]
    assert cropped.cropped_bbox[2] >= region.bbox[2]
    assert cropped.cropped_bbox[3] > region.bbox[3]
    top_extension = region.bbox[1] - cropped.cropped_bbox[1]
    bottom_extension = cropped.cropped_bbox[3] - region.bbox[3]
    assert bottom_extension >= top_extension
    assert cropped.image is not None
    assert cropped.image.shape[0] < 500
