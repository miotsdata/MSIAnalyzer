import argparse
from pathlib import Path
import pytest

from msianalyzer.cli.parse_args.config import _add_config_parser


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def cli_parser() -> argparse.ArgumentParser:
    """Fixture that constructs an ArgumentParser with the config subcommand attached."""
    parser = argparse.ArgumentParser(prog="msianalyzer")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_config_parser(subparsers)
    return parser


# ===========================================================================
# Unit Tests: Argument Parsing
# ===========================================================================

def test_config_create_minimal_args(cli_parser: argparse.ArgumentParser):
    """Verify parsing with only the required -f/--file flag."""
    args = cli_parser.parse_args(["config", "create", "-f", "config.yaml"])

    assert args.command == "config"
    assert args.config_command == "create"
    assert args.file == Path("config.yaml")
    assert args.force is False
    assert args.project is None
    assert args.mzml_files == []
    assert args.xml_files == []
    assert args.output_directory is None
    assert callable(args.func)


def test_config_create_full_args(cli_parser: argparse.ArgumentParser):
    """Verify parsing when all optional flags and multiple file paths are provided."""
    args = cli_parser.parse_args([
        "config", "create",
        "-f", "/tmp/config.yaml",
        "--force",
        "-p", "/home/user/my_project",
        "--mzml-files", "sample1.mzml", "sample2.mzml",
        "--xml-files", "sample1.xml", "sample2.xml",
        "-o", "results_dir",
    ])

    assert args.file == Path("/tmp/config.yaml")
    assert args.force is True
    assert args.project == Path("/home/user/my_project")
    assert args.mzml_files == [Path("sample1.mzml"), Path("sample2.mzml")]
    assert args.xml_files == [Path("sample1.xml"), Path("sample2.xml")]
    assert args.output_directory == Path("results_dir")


def test_config_create_missing_required_file_flag(
    cli_parser: argparse.ArgumentParser, capsys: pytest.CaptureFixture
):
    """Verify argparse exits with error code 2 when required -f/--file is omitted."""
    with pytest.raises(SystemExit) as exc_info:
        cli_parser.parse_args(["config", "create"])

    assert exc_info.value.code == 2

    captured = capsys.readouterr()
    assert "the following arguments are required: -f/--file" in captured.err


def test_config_missing_subcommand(
    cli_parser: argparse.ArgumentParser, capsys: pytest.CaptureFixture
):
    """Verify argparse fails when calling 'config' without a subcommand like 'create'."""
    with pytest.raises(SystemExit) as exc_info:
        cli_parser.parse_args(["config"])

    assert exc_info.value.code == 2


# ===========================================================================
# Unit Tests: Handler Dispatch (Execution)
# ===========================================================================

def test_config_create_func_executes_create_config_file(
    cli_parser: argparse.ArgumentParser, mocker
):
    """Verify executing args.func() calls create_config_file with mapped CLI arguments."""
    # Patch create_config_file inside the module where the CLI lambda resides
    # Adjust path if _add_config_parser is in a different file (e.g. 'msianalyzer.cli.parse_args.config.create_config_file')
    mock_create_fn = mocker.patch("msianalyzer.cli.parse_args.config.create_config_file")

    args = cli_parser.parse_args([
        "config", "create",
        "-f", "out_config.yaml",
        "--force",
        "-p", "/proj/path",
        "--mzml-files", "f1.mzml",
        "--xml-files", "f1.xml",
        "-o", "output_dir",
    ])

    # Execute lambda stored in set_defaults(func=...)
    args.func(args)

    # Verify mock was called once with exact expected parameters
    mock_create_fn.assert_called_once_with(
        file=Path("out_config.yaml"),
        project_folder=Path("/proj/path"),
        mzml_files=[Path("f1.mzml")],
        xml_files=[Path("f1.xml")],
        out_dir=Path("output_dir"),
        force=True,
    )