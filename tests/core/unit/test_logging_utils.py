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
