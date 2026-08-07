import argparse
from pathlib import Path

from msianalyzer.core.config.config import create_config_file


def _add_config_parser(subparsers: argparse._SubParsersAction) -> None:
    # 1. Add the main "config" command
    config_parser = subparsers.add_parser("config", help="Handle config operations")

    # 2. Add subparsers to the "config" command
    config_subparsers = config_parser.add_subparsers(
        dest="config_command", required=True
    )

    # 3. Create the "create" subcommand under "config"
    create_parser = config_subparsers.add_parser("create", help="Create a new config")

    # Add any arguments specific to `create`
    create_parser.add_argument(
        "-f",
        "--file",
        type=Path,
        required=True,
        help="Path of the file that will be created with configs.",
    )

    create_parser.add_argument(
        "--force",
        action="store_true",
        help="(Optional) Whether to override existing config file if already present.",
    )

    create_parser.add_argument(
        "-p",
        "--project",
        type=Path,
        help="(Optional) Path to the project folder this config belongs to. If not provided, the command must be run inside the project folder.",
    )

    create_parser.add_argument(
        "--mzml-files",
        type=Path,
        nargs="*",
        default=[],
        help="(Optional) List of .mzml file paths.",
    )

    create_parser.add_argument(
        "--xml-files",
        type=Path,
        nargs="*",
        default=[],
        help="(Optional) List of .xml file paths. The order of xml files must match the one of the mzml files.",
    )

    create_parser.add_argument(
        "-o",
        "--output-directory",
        type=Path,
        help="(Optional) Path of the directory where to store results files. Default to '.' (current working directory).",
    )

    # 4. Attach the handler function to execute when `config create` is invoked
    create_parser.set_defaults(
        func=lambda args: create_config_file(
            file=args.file,
            project_folder=args.project,
            mzml_files=args.mzml_files,
            xml_files=args.xml_files,
            out_dir=args.output_directory,
            force=args.force,
        )
    )
