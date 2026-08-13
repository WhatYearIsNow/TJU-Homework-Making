from pathlib import Path

import pytest

from app.config import default_config
from app.detect_regions import detect_problem_regions
from app.io_utils import load_input_pages


def test_reference_pdf_detects_four_regions_per_page_if_available() -> None:
    matches = sorted((Path.home() / "Desktop").glob("*20260316200712.pdf"))
    if not matches:
        pytest.skip("reference PDF not available on this machine")

    config = default_config()
    pages = load_input_pages([matches[0]], config)

    refined_counts = [len(detect_problem_regions(page, config.detection).refined_regions) for page in pages]

    assert refined_counts
    assert refined_counts[0] == 4
