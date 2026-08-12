from __future__ import annotations

import argparse
from dataclasses import fields
from pathlib import Path
import logging


from msianalyzer.core.config import Config
from msianalyzer.core.run.run import Run

logger = logging.getLogger(__name__)


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
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory.",
    )

    run.set_defaults(func=run_command)


def run_command(args: argparse.Namespace) -> None:
    config = Config.load(args.config)

    if args.out_dir is not None:
        config.io.out_dir = Path(args.out_dir)

    # Set run
    run = Run()

    run.start(config=config)
