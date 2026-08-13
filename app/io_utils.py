"""I/O utilities for loading images and rasterizing PDF pages."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import cv2
import numpy as np
from PIL import Image
from numpy.typing import NDArray

from app.config import AppConfig, IOConfig
from app.models import PageImage


class InputLoadError(RuntimeError):
    """Raised when an input image or PDF page cannot be decoded."""


def load_input_pages(input_paths: Iterable[str | Path], config: AppConfig) -> list[PageImage]:
    """Load one or more input paths into rasterized page images.

    Images produce a single :class:`PageImage`. PDFs are rasterized into one page image
    per document page.
    """

    pages: list[PageImage] = []
    for input_order, input_path in enumerate(input_paths):
        path = Path(input_path).expanduser().resolve()
        ensure_supported_input(path, config.io)
        if is_pdf_path(path, config.io):
            loaded_pages = load_pdf_pages(path, config.io.pdf_dpi)
        else:
            loaded_pages = [load_image_page(path, config.io.image_dpi)]

        for local_index, page in enumerate(loaded_pages):
            page.page_index = len(pages)
            page.metadata["input_order"] = input_order
            page.metadata["input_page_index"] = local_index
            pages.append(page)
    return pages


def ensure_supported_input(path: Path, io_config: IOConfig) -> None:
    """Validate that a path exists and matches a supported input suffix."""

    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    suffix = path.suffix.lower()
    supported = set(io_config.supported_image_suffixes) | set(io_config.supported_pdf_suffixes)
    if suffix not in supported:
        raise ValueError(f"Unsupported input type: {suffix or '<none>'}. Supported: {sorted(supported)}")


def is_pdf_path(path: Path, io_config: IOConfig) -> bool:
    """Return True when the input path is a supported PDF file."""

    return path.suffix.lower() in set(io_config.supported_pdf_suffixes)


def load_image_page(path: str | Path, dpi: int) -> PageImage:
    """Load an image file into a :class:`PageImage`.

    The returned `image` uses OpenCV's BGR channel order for downstream vision code.
    """

    image_path = Path(path).expanduser().resolve()
    image = read_image(image_path)
    height, width = image.shape[:2]
    return PageImage(
        source_path=image_path,
        page_index=0,
        width=width,
        height=height,
        dpi=dpi,
        image=image,
        metadata={
            "source_type": "image",
            "color_space": "bgr",
            "source_name": image_path.name,
        },
    )


def load_pdf_pages(path: str | Path, dpi: int) -> list[PageImage]:
    """Rasterize a PDF into page images using PyMuPDF."""

    fitz = import_pymupdf()
    pdf_path = Path(path).expanduser().resolve()
    scale = dpi / 72.0
    matrix = fitz.Matrix(scale, scale)
    pages: list[PageImage] = []

    try:
        with fitz.open(str(pdf_path)) as document:
            page_count = document.page_count
            for page_index in range(page_count):
                page = document.load_page(page_index)
                pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                raster = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                    pixmap.height,
                    pixmap.width,
                    pixmap.n,
                )
                if pixmap.n == 1:
                    image = cv2.cvtColor(raster, cv2.COLOR_GRAY2BGR)
                else:
                    image = cv2.cvtColor(raster, cv2.COLOR_RGB2BGR)
                pages.append(
                    PageImage(
                        source_path=pdf_path,
                        page_index=page_index,
                        width=pixmap.width,
                        height=pixmap.height,
                        dpi=dpi,
                        image=image,
                        metadata={
                            "source_type": "pdf",
                            "color_space": "bgr",
                            "pdf_page_count": page_count,
                            "document_page_number": page_index + 1,
                            "source_name": pdf_path.name,
                        },
                    )
                )
    except (RuntimeError, ValueError) as exc:
        raise InputLoadError(f"Failed to rasterize PDF: {pdf_path}") from exc

    return pages


def import_pymupdf():
    """Import PyMuPDF lazily so image-only workflows do not require it upfront."""

    try:
        import fitz  # type: ignore

        return fitz
    except ImportError:
        try:
            import pymupdf as fitz  # type: ignore

            return fitz
        except ImportError as exc:
            raise InputLoadError(
                "PDF input requires PyMuPDF. Install it with 'pip install PyMuPDF'."
            ) from exc


def read_image(path: str | Path) -> NDArray[np.uint8]:
    """Read an image from disk with Windows-safe path handling."""

    image_path = Path(path).expanduser().resolve()
    buffer = np.fromfile(str(image_path), dtype=np.uint8)
    if buffer.size == 0:
        raise InputLoadError(f"Input image is empty: {image_path}")

    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is not None:
        return image

    try:
        with Image.open(image_path) as pil_image:
            rgb_image = np.array(pil_image.convert("RGB"))
    except (OSError, ValueError) as exc:
        raise InputLoadError(f"Failed to decode image: {image_path}") from exc
    return cv2.cvtColor(rgb_image, cv2.COLOR_RGB2BGR)


def write_image(path: str | Path, image: NDArray[np.uint8]) -> Path:
    """Write an image to disk with Windows-safe Unicode path support."""

    output_path = Path(path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    extension = output_path.suffix.lower() or ".png"
    success, encoded = cv2.imencode(extension, image)
    if not success:
        raise InputLoadError(f"Failed to encode image for output: {output_path}")

    encoded.tofile(str(output_path))
    return output_path
