from ast import arg

from msianalyzer.core.parser import MzmlParser

import argparse
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


def _add_parse_mzml_parser(subparsers: argparse._SubParsersAction) -> None:
    parse_mzml_parser = subparsers.add_parser(
        "parse",
        help="Parse mzml file into db file, ready for analyses with MSIAnalyzer.",
    )

    parse_mzml_parser.add_argument(
        "--mzml-paths",
        nargs="+",
        action="extend",
        type=Path,
        default=[],
        required=True,
        metavar="PATH",
        help="Paths of mzml files to parse.",
    )
    parse_mzml_parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("."),
        help="Path of the directory where to store the newly created .db files. Default to '.'",
    )

    parse_mzml_parser.set_defaults(func=parse_mzml_files)


def callback():
    pass


def parse_mzml_files(args: argparse.Namespace) -> None:
    files = [Path(f) for f in args.mzml_paths]
    out_dir = Path(args.out_dir)

    parser = MzmlParser()

    for f in files:
        f_out = out_dir / f"{f.stem}.db"
        parser.parse(f, f_out)
