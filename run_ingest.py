r"""Command-line entry point for target ingestion.

Usage examples:
    venv\Scripts\python.exe run_ingest.py --target molokai_v2
    venv\Scripts\python.exe run_ingest.py --target molokai_v2 --bu MOBILE
    venv\Scripts\python.exe run_ingest.py --target molokai_v2 --excel-path "\\server\share\file.xlsx"

This script delegates the actual DB-backed ingestion workflow to
``src.ingest_logic.ingest_logic``. It intentionally contains no Flask route or
CR Compare code.
"""
from __future__ import annotations

import argparse
import logging
import sys
from typing import Optional

from src.ingest_logic import ingest_logic


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run PDT Buddy ingestion for one target.")
    parser.add_argument(
        "target_positional",
        nargs="?",
        help="Target name/key to ingest. Equivalent to --target.",
    )
    parser.add_argument(
        "--target",
        dest="target_name",
        help="Target name/key to ingest.",
    )
    parser.add_argument(
        "--bu",
        dest="bu_key",
        default=None,
        help="Optional BU override. If omitted, BU is read from dashboard_status.",
    )
    parser.add_argument(
        "--excel-path",
        dest="excel_path",
        default=None,
        help="Optional Excel file/folder override. If omitted, path is read from dashboard_status.",
    )
    parser.add_argument(
        "--triggered-by",
        dest="triggered_by",
        default="cli",
        help="Value stored in ingest_run_log.triggered_by. Default: cli.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    args = _build_parser().parse_args(argv)
    target_name = (args.target_name or args.target_positional or "").strip()

    if not target_name:
        print("ERROR: target is required. Use --target <target_name>.", file=sys.stderr)
        return 2

    ok, message = ingest_logic(
        target_name=target_name,
        bu_key=args.bu_key,
        excel_path=args.excel_path,
        triggered_by=args.triggered_by,
    )

    if ok:
        print(message)
        return 0

    print(f"ERROR: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
