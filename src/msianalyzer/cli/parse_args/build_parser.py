import argparse

from .run import _add_run_parser
from .init_config import _add_init_config_parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="msianalyzer",
        description="MSI data analysis pipeline",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    _add_run_parser(subparsers)
    _add_init_config_parser(subparsers)

    return parser
