"""Helpers for resolving runtime and resource directories."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def app_home() -> Path:
    """Return the writable application home directory.

    When running from source, this is the project root.
    When running as a frozen executable, this is the directory containing the executable.
    """

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    source_root = Path(__file__).resolve().parents[1]
    if (source_root / "pyproject.toml").is_file():
        return source_root

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data).resolve() / "TJUHomeworkMaking"
    return Path.home().resolve() / ".tju-homework-making"


def resource_root() -> Path:
    """Return the directory that contains bundled resources such as assets."""

    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS).resolve()

    source_root = Path(__file__).resolve().parents[1]
    if (source_root / "assets").is_dir():
        return source_root

    installed_root = Path(sys.prefix).resolve()
    if (installed_root / "assets").is_dir():
        return installed_root
    return source_root
