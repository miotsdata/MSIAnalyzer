import argparse
import sys
from pathlib import Path
import pytest

from msianalyzer.cli import build_parser, main

# Adjust the import path to match where _add_run_parser and run_command are defined
from msianalyzer.cli.parse_args.run import _add_run_parser, run_command

MODULE_PATH = "msianalyzer.cli.parse_args.run"


# ==============================================================================
# 1. TESTS FOR _add_run_parser (CLI Argument Parsing)
# ==============================================================================


def test_add_run_parser_defaults():
    """Verifies that parsing 'run' with no flags sets correct default values."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="subcommand")
    _add_run_parser(subparsers)

    args = parser.parse_args(["run"])

    assert args.subcommand == "run"
    assert args.config is None
    assert args.out_dir is None
    assert args.func == run_command


def test_add_run_parser_custom_args(tmp_path):
    """Verifies parsing '-c/--config' and '--out-dir' flags into Path objects."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="subcommand")
    _add_run_parser(subparsers)

    config_path = tmp_path / "config.yaml"
    out_dir_path = tmp_path / "output"

    args = parser.parse_args([
        "run",
        "-c", str(config_path),
        "--out-dir", str(out_dir_path),
    ])

    assert args.config == config_path
    assert args.out_dir == out_dir_path
    assert isinstance(args.config, Path)
    assert isinstance(args.out_dir, Path)


def test_add_run_parser_short_flags(tmp_path):
    """`-c` and `-o` are accepted as short aliases for --config / --out-dir."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="subcommand")
    _add_run_parser(subparsers)

    args = parser.parse_args([
        "run",
        "-c", str(tmp_path / "c.toml"),
        "-o", str(tmp_path / "out"),
    ])

    assert args.config == tmp_path / "c.toml"
    assert args.out_dir == tmp_path / "out"
    assert args.func == run_command


def test_build_parser_registers_run_command(tmp_path):
    """The top-level parser exposes `run` and routes it to run_command."""
    args = build_parser().parse_args(["run", "-c", str(tmp_path / "c.yaml")])

    assert args.command == "run"
    assert args.config == tmp_path / "c.yaml"
    assert args.out_dir is None
    assert args.func == run_command


# ==============================================================================
# 2. TESTS FOR run_command EXECUTION
# ==============================================================================


def test_run_command_without_out_dir_override(mocker, tmp_path):
    """Verifies run_command when out_dir is None (uses config default)."""
    # 1. Mock Config.load
    mock_config = mocker.MagicMock()
    mock_config.io.out_dir = tmp_path / "default_out"
    mock_config_load = mocker.patch(f"{MODULE_PATH}.Config.load", return_value=mock_config)

    # 2. Mock Run class
    mock_run_instance = mocker.MagicMock()
    mock_run_cls = mocker.patch(f"{MODULE_PATH}.Run", return_value=mock_run_instance)

    # 3. Execute with args.out_dir = None
    args = argparse.Namespace(config=tmp_path / "my_config.yaml", out_dir=None)
    run_command(args)

    # 4. Assertions
    mock_config_load.assert_called_once_with(tmp_path / "my_config.yaml")
    assert mock_config.io.out_dir == tmp_path / "default_out"  # Unchanged
    mock_run_cls.assert_called_once()
    mock_run_instance.start.assert_called_once_with(config=mock_config)


def test_run_command_with_out_dir_override(mocker, tmp_path):
    """Verifies run_command overrides config.io.out_dir when --out-dir is passed."""
    # 1. Mock Config.load
    mock_config = mocker.MagicMock()
    mock_config.io.out_dir = tmp_path / "default_out"
    mock_config_load = mocker.patch(f"{MODULE_PATH}.Config.load", return_value=mock_config)

    # 2. Mock Run class
    mock_run_instance = mocker.MagicMock()
    mock_run_cls = mocker.patch(f"{MODULE_PATH}.Run", return_value=mock_run_instance)

    # 3. Execute with args.out_dir provided
    override_dir = tmp_path / "custom_override_out"
    args = argparse.Namespace(config=None, out_dir=override_dir)
    run_command(args)

    # 4. Assertions
    mock_config_load.assert_called_once_with(None)
    assert mock_config.io.out_dir == override_dir  # Successfully overridden
    mock_run_cls.assert_called_once()
    mock_run_instance.start.assert_called_once_with(config=mock_config)


# ==============================================================================
# 3. Integration: full CLI via main()
# ==============================================================================


def test_full_cli_run_command_execution(mocker, tmp_path, monkeypatch):
    """Simulates 'msianalyzer run -c ... -o ...' through main()."""
    mock_config = mocker.MagicMock()
    mock_config.io.out_dir = tmp_path / "config_out"
    mock_config_load = mocker.patch(
        f"{MODULE_PATH}.Config.load", return_value=mock_config
    )
    mock_run_instance = mocker.MagicMock()
    mock_run_cls = mocker.patch(f"{MODULE_PATH}.Run", return_value=mock_run_instance)

    config_path = tmp_path / "pipeline.yaml"
    override_dir = tmp_path / "cli_out"
    monkeypatch.setattr(
        sys,
        "argv",
        ["msianalyzer", "run", "-c", str(config_path), "-o", str(override_dir)],
    )

    main()

    mock_config_load.assert_called_once_with(config_path)
    assert mock_config.io.out_dir == override_dir
    mock_run_cls.assert_called_once()
    mock_run_instance.start.assert_called_once_with(config=mock_config)