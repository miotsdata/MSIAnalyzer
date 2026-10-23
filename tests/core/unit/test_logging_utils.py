"""Tests for the logging primitives: resolve_log_level, log_call,
configure_logging, worker_logging."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from msianalyzer.core.utils.logging_utils import (
    configure_logging,
    log_call,
    resolve_log_level,
    run_log,
    worker_logging,
)


# ---------------------------------------------------------------------------
# resolve_log_level
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        ("debug", logging.DEBUG),
        ("INFO", logging.INFO),
        ("Warning", logging.WARNING),
        ("error", logging.ERROR),
        ("critical", logging.CRITICAL),
        (logging.INFO, logging.INFO),
        (5, 5),
    ],
)
def test_resolve_log_level(value, expected):
    assert resolve_log_level(value) == expected


def test_resolve_log_level_unknown_raises():
    with pytest.raises(ValueError, match="unknown log level"):
        resolve_log_level("loud")


# ---------------------------------------------------------------------------
# log_call
# ---------------------------------------------------------------------------


def test_log_call_emits_start_and_end(caplog):
    @log_call
    def add(a, b):
        """add two numbers"""
        return a + b

    with caplog.at_level(logging.DEBUG, logger=__name__):
        assert add(2, 3) == 5

    msgs = [r.message for r in caplog.records]
    assert any(m.startswith("start ") and "add" in m for m in msgs)
    assert any(m.startswith("end ") and "add" in m and "ms" in m for m in msgs)
    # functools.wraps preserved
    assert add.__name__ == "add"
    assert add.__doc__ == "add two numbers"


def test_log_call_logs_and_reraises_on_exception(caplog):
    @log_call
    def boom():
        raise RuntimeError("kaboom")

    with caplog.at_level(logging.DEBUG, logger=__name__):
        with pytest.raises(RuntimeError, match="kaboom"):
            boom()

    fail = [r for r in caplog.records if r.message.startswith("fail ")]
    assert len(fail) == 1
    assert fail[0].levelno == logging.ERROR
    assert fail[0].exc_info is not None  # traceback attached


def test_log_call_source_sets_source_file(caplog):
    @log_call(source="db_path")
    def touch(db_path):
        return db_path

    with caplog.at_level(logging.DEBUG, logger=__name__):
        touch(db_path="/tmp/x.db")

    starts = [r for r in caplog.records if r.message.startswith("start ")]
    assert starts and getattr(starts[0], "source_file", None) == "/tmp/x.db"


# ---------------------------------------------------------------------------
# configure_logging
# ---------------------------------------------------------------------------


def test_configure_logging_level_and_files(tmp_path):
    log_file = tmp_path / "user.log"
    debug_dir = tmp_path / "logs"
    try:
        configure_logging(
            level="warning",
            log_file=log_file,
            debug_log_dir=debug_dir,
            run_id="run42",
        )
        root = logging.getLogger()
        # console + debug + user file
        stream_h = [
            h for h in root.handlers if type(h).__name__ == "StreamHandler"
        ]
        file_h = [h for h in root.handlers if type(h).__name__ == "FileHandler"]
        assert stream_h and stream_h[0].level == logging.WARNING
        assert (debug_dir / "debug_run42.log").exists()
        assert any(
            Path(h.baseFilename) == log_file and h.level == logging.WARNING
            for h in file_h
        )

        logging.getLogger("x").warning("hello-warn")
        logging.getLogger("x").debug("hello-dbg")
        for h in root.handlers:
            h.flush()
        user_text = log_file.read_text()
        assert "hello-warn" in user_text and "hello-dbg" not in user_text
        assert "hello-dbg" in (debug_dir / "debug_run42.log").read_text()
    finally:
        for h in list(logging.getLogger().handlers):
            h.close()
        logging.basicConfig(level=logging.WARNING, force=True)


# ---------------------------------------------------------------------------
# worker_logging
# ---------------------------------------------------------------------------


def test_worker_logging_yields_queue_and_initializer():
    with worker_logging() as (queue, initializer):
        assert queue is not None
        assert callable(initializer)
        # initializer wires a QueueHandler onto the (child) root logger
        initializer(queue)
        from logging.handlers import QueueHandler

        assert any(
            isinstance(h, QueueHandler) for h in logging.getLogger().handlers
        )
    logging.basicConfig(level=logging.WARNING, force=True)


# ---------------------------------------------------------------------------
# run_log (ADR 56)
# ---------------------------------------------------------------------------


def test_run_log_writes_core_records_at_level_to_a_timestamped_file(tmp_path):
    core = logging.getLogger("msianalyzer.core.some_stage")
    gui = logging.getLogger("msianalyzer.gui.heatmap")
    n_handlers = len(logging.getLogger().handlers)

    with run_log(tmp_path / "out", "info", run_id="abc123") as path:
        core.info("stage done")
        core.debug("too verbose")
        gui.warning("gui noise")

    assert path.parent == tmp_path / "out"
    assert path.name.startswith("run_") and path.suffix == ".log"
    text = path.read_text()
    assert "abc123" in text.splitlines()[0]
    assert "stage done" in text
    assert "too verbose" not in text
    assert "gui noise" not in text
    assert len(logging.getLogger().handlers) == n_handlers


def test_run_log_off_writes_nothing(tmp_path):
    with run_log(tmp_path, "off", run_id="abc") as path:
        logging.getLogger("msianalyzer.core.x").error("boom")
    assert path is None
    assert list(tmp_path.iterdir()) == []


def test_run_log_debug_works_even_if_core_loggers_filter_it(tmp_path):
    core_root = logging.getLogger("msianalyzer.core")
    before = core_root.level
    core_root.setLevel(logging.WARNING)
    try:
        with run_log(tmp_path, "debug", run_id="abc") as path:
            logging.getLogger("msianalyzer.core.x").debug("fine detail")
        assert "fine detail" in path.read_text()
        assert core_root.level == logging.WARNING  # restored
    finally:
        core_root.setLevel(before)


def test_run_log_keeps_the_file_when_the_run_fails(tmp_path):
    with pytest.raises(RuntimeError):
        with run_log(tmp_path, "info", run_id="abc") as path:
            logging.getLogger("msianalyzer.core.x").error("failed here")
            raise RuntimeError
    assert "failed here" in path.read_text()
