from pathlib import Path
import re
import tomllib

from app import __version__


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_version_is_consistent_across_release_files() -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    installer = (PROJECT_ROOT / "pdf_exam_formatter_setup.iss").read_text(encoding="utf-8")
    installer_version = re.search(r'#define MyAppVersion "([^"]+)"', installer)

    assert project["project"]["version"] == __version__
    assert installer_version is not None
    assert installer_version.group(1) == __version__


def test_repository_documentation_files_exist() -> None:
    expected = [
        "README.md",
        "LICENSE",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CODE_OF_CONDUCT.md",
        "CHANGELOG.md",
        "docs/TJU_Homework_Making_使用文档.pdf",
    ]

    assert all((PROJECT_ROOT / relative_path).is_file() for relative_path in expected)
