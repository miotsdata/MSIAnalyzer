from msianalyzer.core.project import create_project_folder


import argparse
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


def _add_project_parser(subparsers: argparse._SubParsersAction) -> None:
    # 1. Add the main "project" command
    project_parser = subparsers.add_parser("project", help="Handle project operations")

    # 2. Add subparsers to the "project" command
    project_subparsers = project_parser.add_subparsers(
        dest="project_command", required=True
    )

    # 3. Create the "create" subcommand under "project"
    create_parser = project_subparsers.add_parser("create", help="Create a new project")

    # Add any arguments specific to `create`
    create_parser.add_argument(
        "-n", "--name", type=str, required=True, help="Project name"
    )
    create_parser.add_argument(
        "-d", "--directory", type=Path, required=True, help="Project directory"
    )

    # 4. Attach the handler function to execute when `project create` is invoked
    create_parser.set_defaults(
        func=lambda args: create_project_folder(args.directory, args.name)
    )
