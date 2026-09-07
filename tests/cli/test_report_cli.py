import argparse
from pathlib import Path

import pytest

from msianalyzer.cli.parse_args.report import _add_report_parser, report_command


@pytest.fixture
def cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="msianalyzer")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_report_parser(subparsers)
    return parser


def test_report_args_defaults(cli_parser):
    args = cli_parser.parse_args(["report", "analysis_x.db"])
    assert args.analysis_db == Path("analysis_x.db")
    assert args.out_dir is None
    assert args.raw_db == []
    assert args.func is report_command


def test_report_args_full(cli_parser):
    args = cli_parser.parse_args(
        ["report", "a.db", "-o", "out", "--raw-db", "s1.db", "s2.db", "-v", "debug"]
    )
    assert args.out_dir == Path("out")
    assert args.raw_db == [Path("s1.db"), Path("s2.db")]
    assert args.verbosity == "debug"


def test_report_command_invokes_builder(mocker, tmp_path):
    build = mocker.patch(
        "msianalyzer.cli.parse_args.report.build_summary_report",
        return_value=tmp_path / "summary_report.html",
    )
    mocker.patch("msianalyzer.cli.parse_args.report.configure_logging")

    ns = argparse.Namespace(
        analysis_db=tmp_path / "analysis.db",
        out_dir=tmp_path / "out",
        raw_db=[],
        verbosity="info",
    )
    report_command(ns)

    build.assert_called_once()
    assert build.call_args.kwargs["raw_db_paths"] is None
    assert build.call_args.kwargs["out_dir"] == tmp_path / "out"


def test_report_command_maps_raw_db_to_sample_ids(mocker, tmp_path):
    from msianalyzer.core.analysis_db import init_analysis_db, register_sample

    adb = tmp_path / "analysis.db"
    init_analysis_db(adb).close()
    register_sample(adb, name="s1", raw_db_path=tmp_path / "orig1.db")
    register_sample(adb, name="s2", raw_db_path=tmp_path / "orig2.db")

    build = mocker.patch(
        "msianalyzer.cli.parse_args.report.build_summary_report",
        return_value=adb.parent / "summary_report.html",
    )
    mocker.patch("msianalyzer.cli.parse_args.report.configure_logging")

    ns = argparse.Namespace(
        analysis_db=adb,
        out_dir=None,
        raw_db=[tmp_path / "new1.db", tmp_path / "new2.db"],
        verbosity="info",
    )
    report_command(ns)

    raw_map = build.call_args.kwargs["raw_db_paths"]
    assert raw_map == {1: str(tmp_path / "new1.db"), 2: str(tmp_path / "new2.db")}


def test_report_command_rejects_wrong_raw_db_count(mocker, tmp_path):
    from msianalyzer.core.analysis_db import init_analysis_db, register_sample

    adb = tmp_path / "analysis.db"
    init_analysis_db(adb).close()
    register_sample(adb, name="s1", raw_db_path=tmp_path / "orig1.db")

    mocker.patch("msianalyzer.cli.parse_args.report.build_summary_report")
    mocker.patch("msianalyzer.cli.parse_args.report.configure_logging")

    ns = argparse.Namespace(
        analysis_db=adb,
        out_dir=None,
        raw_db=[tmp_path / "a.db", tmp_path / "b.db"],
        verbosity="info",
    )
    with pytest.raises(SystemExit):
        report_command(ns)
