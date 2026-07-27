from __future__ import annotations

import argparse
from pathlib import Path

from msianalyzer.core.config import Config


def _add_init_config_parser(subparsers: argparse._SubParsersAction) -> None:
    init_config = subparsers.add_parser(
        "init-config",
        help="Write a default config file to disk",
        description=(
            "Write a Config with default values to a file, as a starting "
            "point for editing. Format (.yaml/.yml or .toml) is inferred "
            "from the output file's extension."
        ),
    )

    init_config.add_argument(
        "out_path",
        type=Path,
        help="Where to write the config file, e.g. config.yaml or config.toml",
    )
    init_config.add_argument(
        "--mzml-paths",
        nargs="+",
        action="extend",
        type=Path,
        default=[],
        metavar="PATH",
        help="Placeholder mzML path(s) to seed the file with (optional).",
    )
    init_config.add_argument(
        "--xml-paths",
        nargs="+",
        action="extend",
        type=Path,
        default=[],
        metavar="PATH",
        help="Placeholder XML path(s) to seed the file with (optional).",
    )
    init_config.add_argument(
        "--out-dir",
        type=Path,
        default=Path("."),
        help="Placeholder output directory to seed the file with.",
    )
    init_config.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Overwrite out_path if it already exists.",
    )

    init_config.set_defaults(func=init_config_command)


def init_config_command(args: argparse.Namespace) -> None:
    if args.out_path.exists() and not args.force:
        raise SystemExit(
            f"{args.out_path} already exists. Use -f/--force to overwrite it."
        )

    config = Config(
        mzml_paths=args.mzml_paths,
        xml_paths=args.xml_paths,
        out_dir=args.out_dir,
    )
    config.export(args.out_path)
    print(f"Wrote default config to {args.out_path}")
