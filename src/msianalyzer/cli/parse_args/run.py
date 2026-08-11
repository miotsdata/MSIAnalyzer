from __future__ import annotations

import argparse
from dataclasses import fields
from pathlib import Path
import logging


from msianalyzer.core.config import Project, Config, GROUPS
from msianalyzer.core.config.config import IOConfig
from msianalyzer.core.run import run_core

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
        "-n",
        "--name",
        type=str,
        default=None,
        required=True,
        help="Name of the project.",
    )

    # --- required inputs (optional here, since they may come from -c) ---
    io_group = run.add_argument_group("input/output")
    io_group.add_argument(
        "--mzml-paths",
        nargs="+",
        action="extend",
        type=Path,
        default=None,
        metavar="PATH",
        help="One or more mzML files. Repeatable and/or space-separated.",
    )
    io_group.add_argument(
        "--xml-paths",
        nargs="+",
        action="extend",
        type=Path,
        default=None,
        metavar="PATH",
        help="One or more XML files. Repeatable and/or space-separated.",
    )
    io_group.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory.",
    )

    # --- Average MS1 ---
    ms1_group = run.add_argument_group("average MS1")
    ms1_group.add_argument("--ms1-chunk-size", type=int, default=None)
    ms1_group.add_argument("--ms1-bin-width", type=float, default=None)
    ms1_group.add_argument("--ms1-min-mz", type=float, default=None)
    ms1_group.add_argument("--ms1-max-mz", type=float, default=None)

    # --- Detect centroids ---
    centroid_group = run.add_argument_group("detect centroids")
    centroid_group.add_argument("--prominence-factor", type=float, default=None)
    centroid_group.add_argument("--baseline-factor", type=float, default=None)
    centroid_group.add_argument(
        "--baseline-method", type=str, default=None, choices=["local", "global"]
    )
    centroid_group.add_argument("--baseline-percentile", type=float, default=None)
    centroid_group.add_argument("--local-window", type=int, default=None)
    centroid_group.add_argument("--smooth-sigma", type=float, default=None)

    # --- Peak threshold ---
    peak_group = run.add_argument_group("peak threshold")
    peak_group.add_argument("--peak-height-threshold", type=float, default=None)
    peak_group.add_argument("--filter-mad", action="store_true", default=None)
    peak_group.add_argument("--filter-mad-log", action="store_true", default=None)
    peak_group.add_argument("--filter-mad-nmads", type=float, default=None)

    # --- Find all mzs ---
    align_group = run.add_argument_group("align all mzs")
    align_group.add_argument("--align-ppm", type=float, default=None)
    align_group.add_argument("--mz-decimals", type=int, default=None)
    align_group.add_argument(
        "--sample-names",
        nargs="+",
        action="extend",
        type=str,
        default=None,
        metavar="NAME",
    )

    # --- Create h5ad ---
    h5ad_group = run.add_argument_group("create h5ad")
    h5ad_group.add_argument("--integration-ppm", type=float, default=None)
    h5ad_group.add_argument("--integration-batch-size", type=int, default=None)
    h5ad_group.add_argument(
        "--integration-scan-handling",
        type=str,
        default=None,
        choices=["sum", "mean", "max"],
    )
    h5ad_group.add_argument("--n-workers", type=int, default=None)

    run.set_defaults(func=run_command)


def resolve_config(args: argparse.Namespace) -> Config:
    """Load configuration and apply CLI overrides.

    Priority:
        CLI arguments > config file > dataclass defaults

    Path handling:
        - config paths are resolved relative to the config file
        - CLI paths are resolved relative to the current working directory
    """

    if args.config is not None:
        config = Config.load(args.config)
    else:
        config = Config(IOConfig())

    cli_args = vars(args)

    field_to_group = {
        f.name: group_name
        for group_name, group_type in GROUPS.items()
        for f in fields(group_type)
    }

    cwd = Path.cwd()

    for key, value in cli_args.items():
        if key in {
            "func",
            "config",
            "command",
            "subparser_name",
            "name",
        }:
            continue

        if value is None:
            continue

        group_name = field_to_group.get(key)

        if group_name is None:
            continue

        group = getattr(config, group_name)

        # CLI paths are relative to cwd
        if group_name == "io" and key in {
            "mzml_paths",
            "xml_paths",
            "out_dir",
        }:
            if isinstance(value, list):
                value = [(cwd / Path(p)).resolve() for p in value]
            else:
                value = (cwd / Path(value)).resolve()

        setattr(group, key, value)

    # Validate required IO fields
    missing = []

    if not config.io.mzml_paths:
        missing.append("mzml_paths")

    if not config.io.xml_paths:
        missing.append("xml_paths")

    if config.io.out_dir is None:
        missing.append("out_dir")

    if missing:
        raise SystemExit(
            f"Missing required argument(s): {', '.join(missing)}. "
            "Provide via -c/--config or CLI."
        )

    return config


def run_command(args: argparse.Namespace) -> None:
    config = resolve_config(args)

    project = Project(args.name)
    project.set_config(config)

    project.export()

    run_core(project, config)
