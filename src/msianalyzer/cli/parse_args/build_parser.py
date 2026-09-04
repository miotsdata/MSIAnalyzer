import argparse

from .config import _add_config_parser
from .parse import _add_parse_mzml_parser
from .project import _add_project_parser
from .run import _add_run_parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="msianalyzer",
        description="MSI data analysis pipeline",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    _add_project_parser(subparsers)
    _add_parse_mzml_parser(subparsers)
    _add_config_parser(subparsers)
    _add_run_parser(subparsers)

    return parser
