from __future__ import annotations

from pathlib import Path

from app.gui import FormatterGUI


def build_gui_stub() -> FormatterGUI:
    return object.__new__(FormatterGUI)


def test_normalize_clipboard_path_supports_file_uri(tmp_path: Path) -> None:
    gui = build_gui_stub()
    sample = tmp_path / "sample.pdf"
    sample.write_bytes(b"pdf")

    uri = sample.resolve().as_uri()

    normalized = gui._normalize_clipboard_path(uri)

    assert normalized == str(sample.resolve())


def test_extract_paths_from_clipboard_text_recognizes_quoted_and_plain_paths(tmp_path: Path) -> None:
    gui = build_gui_stub()
    first = tmp_path / "first file.pdf"
    second = tmp_path / "second.png"
    first.write_bytes(b"a")
    second.write_bytes(b"b")

    clipboard_text = f'"{first}"\n{second}'

    extracted = gui._extract_paths_from_clipboard_text(clipboard_text)

    assert extracted == [str(first.resolve()), str(second.resolve())]
