import argparse

"""
from .run import _add_run_parser
from .init_config import _add_init_config_parser
from .resume import _add_resume_parser
"""
from .project import _add_project_parser
from .parse import _add_parse_mzml_parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="msianalyzer",
        description="MSI data analysis pipeline",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    _add_project_parser(subparsers)
    _add_parse_mzml_parser(subparsers)
    # _add_run_parser(subparsers)
    # _add_init_config_parser(subparsers)
    # _add_resume_parser(subparsers)

    return parser
