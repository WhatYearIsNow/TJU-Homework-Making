"""Image preprocessing helpers for problem region detection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray

from app.config import DetectionConfig
from app.models import PageImage


@dataclass(slots=True)
class PreprocessArtifacts:
    """Intermediate images produced during traditional vision preprocessing."""

    page_index: int
    gray: NDArray[np.uint8]
    blurred: NDArray[np.uint8]
    binary: NDArray[np.uint8]
    foreground: NDArray[np.uint8]
    closed: NDArray[np.uint8]
    metadata: dict[str, Any] = field(default_factory=dict)


def preprocess_page(page: PageImage, config: DetectionConfig) -> PreprocessArtifacts:
    """Run grayscale, threshold, inversion, and closing for one page."""

    if page.image is None:
        raise ValueError("PageImage.image is required for preprocessing.")

    gray = to_grayscale(page.image)
    blurred = apply_gaussian_blur(gray, config.grayscale_blur_kernel)
    binary = binarize_image(blurred, config)
    foreground = invert_binary_mask(binary) if config.invert_binary else binary.copy()
    closed = apply_close_operation(
        foreground,
        kernel_width=config.close_kernel_width,
        kernel_height=config.close_kernel_height,
        iterations=config.close_iterations,
    )

    return PreprocessArtifacts(
        page_index=page.page_index,
        gray=gray,
        blurred=blurred,
        binary=binary,
        foreground=foreground,
        closed=closed,
        metadata={
            "threshold_method": config.threshold_method,
            "blur_kernel": _ensure_odd(config.grayscale_blur_kernel),
            "close_kernel": (
                max(1, int(config.close_kernel_width)),
                max(1, int(config.close_kernel_height)),
            ),
            "close_iterations": max(1, int(config.close_iterations)),
        },
    )


def to_grayscale(image: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """Convert an image to single-channel grayscale."""

    if image.ndim == 2:
        return image.copy()
    if image.ndim == 3 and image.shape[2] == 1:
        return image[:, :, 0].copy()
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def apply_gaussian_blur(image: NDArray[np.uint8], kernel_size: int) -> NDArray[np.uint8]:
    """Apply a light Gaussian blur before thresholding."""

    normalized = _ensure_odd(kernel_size)
    if normalized <= 1:
        return image.copy()
    return cv2.GaussianBlur(image, (normalized, normalized), sigmaX=0)


def binarize_image(image: NDArray[np.uint8], config: DetectionConfig) -> NDArray[np.uint8]:
    """Binarize a grayscale image using Otsu or adaptive thresholding."""

    if config.threshold_method == "adaptive":
        block_size = max(3, _ensure_odd(config.adaptive_block_size))
        binary = cv2.adaptiveThreshold(
            image,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            block_size,
            config.adaptive_c,
        )
    else:
        _, binary = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)

    return ensure_binary_mask(binary)


def invert_binary_mask(mask: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """Invert a binary mask so lines and text become foreground."""

    return cv2.bitwise_not(ensure_binary_mask(mask))


def apply_close_operation(
    mask: NDArray[np.uint8],
    *,
    kernel_width: int,
    kernel_height: int,
    iterations: int,
) -> NDArray[np.uint8]:
    """Connect nearby text and diagram fragments using morphological closing."""

    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (max(1, int(kernel_width)), max(1, int(kernel_height))),
    )
    return cv2.morphologyEx(
        ensure_binary_mask(mask),
        cv2.MORPH_CLOSE,
        kernel,
        iterations=max(1, int(iterations)),
    )


def ensure_binary_mask(mask: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """Normalize a mask to uint8 values of 0 or 255."""

    if mask.dtype != np.uint8:
        mask = mask.astype(np.uint8)
    normalized = np.where(mask > 0, 255, 0).astype(np.uint8)
    return normalized


def _ensure_odd(value: int) -> int:
    """Normalize a positive kernel size to an odd integer."""

    normalized = max(1, int(value))
    if normalized % 2 == 0:
        normalized += 1
    return normalized
