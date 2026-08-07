import argparse
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

# Adjust imports to match your package structure
from msianalyzer.cli.parse_args.parse import (
    _add_parse_mzml_parser,
    parse_mzml_files,
)

from msianalyzer.cli.main import main

# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def cli_parser() -> argparse.ArgumentParser:
    """Fixture that builds an ArgumentParser configured with the 'parse' subcommand."""
    parser = argparse.ArgumentParser(prog="msianalyzer")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_parse_mzml_parser(subparsers)
    return parser


# ===========================================================================
# Unit Tests: Argument Parsing
# ===========================================================================


def test_parse_args_valid_multiple_files(cli_parser: argparse.ArgumentParser):
    """Verify parsing multiple input paths and a custom output directory."""
    args = cli_parser.parse_args(
        [
            "parse",
            "--mzml-paths",
            "file1.mzml",
            "file2.mzml",
            "--out-dir",
            "/custom/output",
        ]
    )

    assert args.command == "parse"
    assert args.mzml_paths == [Path("file1.mzml"), Path("file2.mzml")]
    assert args.out_dir == Path("/custom/output")
    assert args.func == parse_mzml_files


def test_parse_args_default_out_dir(cli_parser: argparse.ArgumentParser):
    """Verify that --out-dir defaults to Path('.') when omitted."""
    args = cli_parser.parse_args(["parse", "--mzml-paths", "sample.mzml"])

    assert args.mzml_paths == [Path("sample.mzml")]
    assert args.out_dir == Path(".")


def test_parse_args_missing_required_paths(
    cli_parser: argparse.ArgumentParser, capsys: pytest.CaptureFixture
):
    """Verify argparse exits with code 2 when required --mzml-paths argument is missing."""
    with pytest.raises(SystemExit) as exc_info:
        cli_parser.parse_args(["parse"])

    assert exc_info.value.code == 2

    captured = capsys.readouterr()
    assert (
        "the following arguments are required: --mzml-paths" in captured.err
        or "--mzml-paths" in captured.err
    )


# ===========================================================================
# Unit Tests: Handler Execution (`parse_mzml_files`)
# ===========================================================================


@patch(
    "msianalyzer.cli.parse_args.parse.MzmlParser"
)  # Patch MzmlParser inside the module using it
def test_parse_mzml_files_handler_calls_parser(
    mock_mzml_parser_cls: MagicMock, tmp_path: Path
):
    """Verify parse_mzml_files instantiates MzmlParser and calls parse() with expected .db paths."""
    mock_instance = MagicMock()
    mock_mzml_parser_cls.return_value = mock_instance

    f1 = tmp_path / "file1.mzml"
    f2 = tmp_path / "file2.mzml"
    out_dir = tmp_path / "output_db"

    args = argparse.Namespace(mzml_paths=[f1, f2], out_dir=out_dir)

    # Call handler directly
    parse_mzml_files(args)

    # Assert MzmlParser was instantiated
    mock_mzml_parser_cls.assert_called_once()

    # Assert parse() was called for each file with target .db path stem
    assert mock_instance.parse.call_count == 2
    mock_instance.parse.assert_any_call(f1, out_dir / "file1.db")
    mock_instance.parse.assert_any_call(f2, out_dir / "file2.db")


# ===========================================================================
# Integration Test: Full CLI Execution
# ===========================================================================


def test_full_cli_parse_command_execution(
    mocker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Simulates typing 'msianalyzer parse --mzml-paths ...' into terminal via main()."""
    # 1. Intercept MzmlParser using the mocker fixture
    mock_mzml_parser_cls = mocker.patch("msianalyzer.cli.parse_args.parse.MzmlParser")

    # 2. Grab the auto-created mock instance
    mock_instance = mock_mzml_parser_cls.return_value

    input_file = tmp_path / "run_alpha.mzml"
    out_dir = tmp_path / "db_store"

    # Simulate sys.argv
    cli_tokens = [
        "msianalyzer",
        "parse",
        "--mzml-paths",
        str(input_file),
        "--out-dir",
        str(out_dir),
    ]
    monkeypatch.setattr(sys, "argv", cli_tokens)

    # Run main CLI entrance
    main()

    # Verify MzmlParser.parse was called with correctly mapped output path
    mock_instance.parse.assert_called_once_with(
        Path(input_file), out_dir / "run_alpha.db"
    )
