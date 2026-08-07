import pytest
import sys
from pathlib import Path
from msianalyzer.cli import build_parser, main


def test_cli_parser_project_create_args():
    """Unit test parser directly without executing main()."""
    parser = build_parser()
    args = parser.parse_args(
        ["project", "create", "--name", "my_proj", "--directory", "/tmp/demo"]
    )

    assert args.command == "project"
    assert args.project_command == "create"
    assert args.name == "my_proj"
    assert args.directory == Path("/tmp/demo")


def test_cli_main_create_project_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Test full CLI execution using sys.argv simulation."""
    target_directory = tmp_path / "cli_project"

    # Simulate: msianalyzer project create --name cli_project --directory <tmp_path/cli_project>
    test_args = [
        "msianalyzer",
        "project",
        "create",
        "--name",
        "cli_project",
        "--directory",
        str(target_directory),
    ]
    monkeypatch.setattr(sys, "argv", test_args)

    # Run main CLI handler
    main()

    # Assert side effects
    assert target_directory.exists()
    assert (target_directory / ".msianalyzer.yml").is_file()


def test_cli_missing_required_args(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    """Test that argparse automatically exits (exit code 2) when required flags are missing."""
    # Omitting required --directory flag
    test_args = ["msianalyzer", "project", "create", "--name", "my_proj"]
    monkeypatch.setattr(sys, "argv", test_args)

    with pytest.raises(SystemExit) as exc_info:
        main()

    # argparse exits with code 2 on invalid arguments
    assert exc_info.value.code == 2

    # Check error message printed to stderr
    captured = capsys.readouterr()
    assert (
        "the following arguments are required: -p/--directory" in captured.err
        or "--directory" in captured.err
    )
