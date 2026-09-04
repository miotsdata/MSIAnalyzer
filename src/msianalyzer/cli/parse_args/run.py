from __future__ import annotations

import argparse
import logging
from pathlib import Path

from msianalyzer.core.config import Config
from msianalyzer.core.run.run import Run
from msianalyzer.core.utils.logging_utils import configure_logging

logger = logging.getLogger(__name__)

_LEVELS = ["debug", "info", "warning", "error", "critical"]


def _add_run_parser(subparsers: argparse._SubParsersAction) -> None:
    run = subparsers.add_parser(
        "run",
        help="Run the full workflow",
        description=(
            "Run the full MSI workflow. Load settings from a config file "
            "with -c/--config, and/or override individual settings via "
            "the flags below. CLI flags always take precedence over the "
            "config file."
        ),
    )

    run.add_argument(
        "-c",
        "--config",
        type=Path,
        default=None,
        help="Path to a .yaml/.yml or .toml config file.",
    )

    run.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory; overrides io.out_dir from the config file.",
    )

    run.add_argument(
        "-l",
        "--log-file",
        type=Path,
        default=None,
        help="Write this run's log to PATH (level set by -v). Default: no file.",
    )

    run.add_argument(
        "-v",
        "--verbosity",
        type=str.lower,
        choices=_LEVELS,
        default="info",
        help="Log level for the console and the -l file. Default: info.",
    )

    run.set_defaults(func=run_command)


def run_command(args: argparse.Namespace) -> None:
    config = Config.load(args.config)

    if args.out_dir is not None:
        config.io.out_dir = Path(args.out_dir)

    run = Run()  # run.id exists after __init__

    configure_logging(
        level=args.verbosity,
        log_file=args.log_file,
        debug_log_dir=Path(config.io.project_folder) / "logs",
        run_id=run.id,
    )
    logger.info(
        "run %s starting (verbosity=%s, log_file=%s)",
        run.id,
        args.verbosity,
        args.log_file,
    )

    run.start(config=config)
