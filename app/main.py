"""CLI entry point for the PDF exam formatter."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from app import __version__
from app.config import AppConfig, ensure_runtime_dirs, load_config
from app.pipeline import run_pipeline

SUPPORTED_INPUT_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".tif",
    ".tiff",
    ".webp",
    ".pdf",
}


def build_parser() -> argparse.ArgumentParser:
    """Build the command line parser."""

    parser = argparse.ArgumentParser(
        prog="pdf_exam_formatter",
        description="Automatically detect, crop, and re-layout exam problem images into a fixed-style PDF.",
    )
    parser.add_argument(
        "--input",
        nargs="+",
        required=True,
        help="One or more image/PDF inputs.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output PDF path.",
    )
    parser.add_argument(
        "--mode",
        choices=("normal", "fast"),
        default="normal",
        help="Processing mode. 'fast' only trims secondary refinements, not page structure.",
    )
    parser.add_argument(
        "--debug",
        type=parse_bool,
        default=None,
        help="Enable debug exports. Accepts true/false/1/0/yes/no.",
    )
    parser.add_argument(
        "--watermark",
        type=parse_bool,
        default=None,
        help="Enable or disable the Tianjin University PDF watermark.",
    )
    parser.add_argument(
        "--dpi",
        type=positive_int,
        default=None,
        help="Rasterization DPI for images and PDF pages.",
    )
    parser.add_argument(
        "--per-page",
        type=positive_int,
        default=None,
        help="Problems per page. Supports dynamic auto-layout when no template is provided.",
    )
    parser.add_argument(
        "--strict-single-question",
        type=parse_bool,
        default=None,
        help="Enable strict single-question mode: one question per page with robust numbered-PDF segmentation.",
    )
    parser.add_argument(
        "--layout-template",
        default=None,
        help="Optional PDF/image template used to infer page grid geometry from an example page.",
    )
    parser.add_argument(
        "--template-lock-per-page",
        type=parse_bool,
        default=None,
        help="When true, a layout template also locks the page capacity to the template grid.",
    )
    parser.add_argument(
        "--header-config",
        default=None,
        help="Optional YAML/JSON file for header template overrides.",
    )
    parser.add_argument(
        "--student-id",
        default=None,
        help="Student ID shown in the reference-style header.",
    )
    parser.add_argument(
        "--class-name",
        default=None,
        help="Class name shown in the reference-style header.",
    )
    parser.add_argument(
        "--student-name",
        default=None,
        help="Student name shown in the reference-style header.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Optional YAML/JSON file for global config overrides.",
    )
    parser.add_argument(
        "--print-config",
        action="store_true",
        help="Print the resolved configuration and exit.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def parse_bool(value: str | bool) -> bool:
    """Parse common CLI bool representations."""

    if isinstance(value, bool):
        return value

    normalized = value.strip().lower()
    truthy = {"1", "true", "yes", "y", "on"}
    falsy = {"0", "false", "no", "n", "off"}
    if normalized in truthy:
        return True
    if normalized in falsy:
        return False
    raise argparse.ArgumentTypeError(f"Invalid boolean value: {value!r}")


def positive_int(value: str) -> int:
    """Validate a positive integer CLI argument."""

    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("Value must be a positive integer.")
    return parsed


def build_cli_overrides(args: argparse.Namespace) -> dict[str, Any]:
    """Translate CLI options into nested config overrides."""

    overrides: dict[str, Any] = {
        "performance": {"mode": args.mode},
    }
    if args.debug is not None:
        overrides.setdefault("debug", {})["enabled"] = args.debug
    if args.watermark is not None:
        overrides.setdefault("watermark", {})["enabled"] = args.watermark
    if args.dpi is not None:
        overrides.setdefault("io", {})["image_dpi"] = args.dpi
        overrides.setdefault("io", {})["pdf_dpi"] = args.dpi
    if args.per_page is not None:
        overrides.setdefault("layout", {})["problems_per_page"] = args.per_page
    if args.strict_single_question is True:
        overrides.setdefault("layout", {})["problems_per_page"] = 1
        overrides.setdefault("detection", {}).update(
            {
                "strict_single_question_mode": True,
                "enable_pdf_text_number_detection": True,
                "pdf_text_min_questions": 2,
                "pdf_text_anchor_max_step": 2,
                "pdf_text_bottom_trim_threshold": 242,
            }
        )
    elif args.strict_single_question is False:
        overrides.setdefault("detection", {})["strict_single_question_mode"] = False
    if args.layout_template is not None:
        overrides.setdefault("layout", {})["template_path"] = args.layout_template
    if args.template_lock_per_page is not None:
        overrides.setdefault("layout", {})["template_lock_per_page"] = args.template_lock_per_page
    identity_overrides: dict[str, Any] = {}
    if args.student_id is not None:
        identity_overrides["student_id"] = args.student_id
    if args.class_name is not None:
        identity_overrides["class_name"] = args.class_name
    if args.student_name is not None:
        identity_overrides["student_name"] = args.student_name
    if identity_overrides:
        overrides.setdefault("header", {})["identity"] = identity_overrides
    return overrides


def validate_inputs(input_paths: Sequence[str]) -> list[Path]:
    """Validate user supplied inputs and return normalized paths."""

    normalized: list[Path] = []
    for raw_path in input_paths:
        path = Path(raw_path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"Input not found: {path}")
        if path.suffix.lower() not in SUPPORTED_INPUT_SUFFIXES:
            raise ValueError(
                f"Unsupported input type: {path.suffix or '<none>'}. "
                f"Supported types: {', '.join(sorted(SUPPORTED_INPUT_SUFFIXES))}"
            )
        normalized.append(path)
    return normalized


def resolve_output_path(output_arg: str) -> Path:
    """Normalize and validate the output PDF path."""

    output_path = Path(output_arg).expanduser().resolve()
    if output_path.suffix.lower() != ".pdf":
        raise ValueError("Output path must end with .pdf")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return output_path


def print_resolved_config(config: AppConfig) -> None:
    """Print config as formatted JSON for inspection."""

    print(json.dumps(config.as_dict(), indent=2, ensure_ascii=False))


def run_cli_pipeline(
    *,
    input_paths: Sequence[Path],
    output_path: Path,
    config: AppConfig,
) -> int:
    """Run the full CLI pipeline and print a concise summary."""

    ensure_runtime_dirs(config)
    result = run_pipeline(
        input_paths=input_paths,
        output_path=output_path,
        config=config,
    )

    print(f"Output PDF: {result.output_path}")
    print(f"Input files: {len(result.input_paths)}")
    print(f"Source pages: {len(result.page_results)}")
    print(f"Detected problems: {len(result.cropped_problems)}")
    print(f"Rendered pages: {len(result.page_layouts)}")
    print(f"Mode: {config.performance.mode}")
    if result.template_spec is not None:
        print(f"Layout template: {result.template_spec.source_path}")
        print(f"Template capacity: {result.template_spec.problems_per_page}")
    if result.debug_dir is not None:
        print(f"Debug artifacts: {result.debug_dir}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Program entry point."""

    argv = list(argv) if argv is not None else sys.argv[1:]
    if "--gui" in argv:
        from app.gui import run_gui

        return run_gui()

    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        input_paths = validate_inputs(args.input)
        output_path = resolve_output_path(args.output)
        config = load_config(
            args.config,
            header_config_path=args.header_config,
            overrides=build_cli_overrides(args),
        )
        if args.print_config:
            print_resolved_config(config)
            return 0
        return run_cli_pipeline(
            input_paths=input_paths,
            output_path=output_path,
            config=config,
        )
    except (FileNotFoundError, ValueError, RuntimeError, argparse.ArgumentTypeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
