from __future__ import annotations

import argparse
import logging
import sqlite3
from pathlib import Path

from msianalyzer.core.report.summary import build_summary_report
from msianalyzer.core.utils.logging_utils import configure_logging

logger = logging.getLogger(__name__)

_LEVELS = ["debug", "info", "warning", "error", "critical"]


def _add_report_parser(subparsers: argparse._SubParsersAction) -> None:
    report = subparsers.add_parser(
        "report",
        help="Build the summary report for a finished analysis database.",
        description=(
            "Regenerate summary_report.html + summary.json from an existing "
            "analysis_<run-id>.db without re-running the pipeline."
        ),
    )
    report.add_argument(
        "analysis_db",
        type=Path,
        help="Path to a finished analysis database (analysis_<run-id>.db).",
    )
    report.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=None,
        help=(
            "Directory for summary_report.html / summary.json. "
            "Default: the analysis database's directory."
        ),
    )
    report.add_argument(
        "--raw-db",
        type=Path,
        nargs="+",
        action="extend",
        default=[],
        metavar="PATH",
        help=(
            "Raw per-sample databases, in `samples` table order. Default: the "
            "raw_db_path values recorded in the analysis database."
        ),
    )
    report.add_argument(
        "-v",
        "--verbosity",
        type=str.lower,
        choices=_LEVELS,
        default="info",
        help="Console log level. Default: info.",
    )
    report.set_defaults(func=report_command)


def report_command(args: argparse.Namespace) -> None:
    configure_logging(level=args.verbosity)

    raw_map: dict[int, str] | None = None
    if args.raw_db:
        with sqlite3.connect(args.analysis_db) as con:
            sample_ids = [
                int(r[0])
                for r in con.execute(
                    "SELECT sample_id FROM samples ORDER BY sample_id"
                ).fetchall()
            ]
        if len(sample_ids) != len(args.raw_db):
            raise SystemExit(
                f"--raw-db expects {len(sample_ids)} path(s) "
                f"(one per sample), got {len(args.raw_db)}"
            )
        raw_map = {
            sid: str(Path(p)) for sid, p in zip(sample_ids, args.raw_db)
        }

    html_path = build_summary_report(
        args.analysis_db, raw_db_paths=raw_map, out_dir=args.out_dir
    )
    logger.info("summary report written to %s", html_path)
    print(html_path)
