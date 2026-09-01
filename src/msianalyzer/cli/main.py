import argparse
from .parse_args import build_parser
from msianalyzer.core.utils.logging_utils import configure_logging
import logging

configure_logging(level=logging.INFO)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
