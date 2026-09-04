"""Tests for utils/db.py — DB writes that log the offending values on failure."""

from __future__ import annotations

import logging
import sqlite3

import pytest

from msianalyzer.core.utils.db import safe_execute, safe_executemany

LOGGER = logging.getLogger("test_db_utils")


@pytest.fixture
def con():
    c = sqlite3.connect(":memory:")
    c.execute(
        "CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT NOT NULL UNIQUE)"
    )
    yield c
    c.close()


def test_safe_execute_ok(con):
    safe_execute(
        con, "INSERT INTO t (id, v) VALUES (?, ?)", (1, "a"),
        table="t", logger=LOGGER,
    )
    assert con.execute("SELECT v FROM t WHERE id = 1").fetchone()[0] == "a"


def test_safe_execute_logs_params_and_reraises(con, caplog):
    con.execute("INSERT INTO t (id, v) VALUES (1, 'a')")
    with caplog.at_level(logging.ERROR):
        with pytest.raises(sqlite3.IntegrityError):
            safe_execute(
                con, "INSERT INTO t (id, v) VALUES (?, ?)", (1, "b"),
                table="t", logger=LOGGER, source="/tmp/x.db",
            )
    assert len(caplog.records) == 1
    rec = caplog.records[0]
    assert "DB write failed on t" in rec.message
    assert "(1, 'b')" in rec.message
    assert rec.source_file == "/tmp/x.db"


def test_safe_execute_reports_no_bound_params_clearly(con, caplog):
    con.execute("CREATE TABLE u (id INTEGER PRIMARY KEY)")
    con.execute("INSERT INTO u (id) VALUES (1)")
    with caplog.at_level(logging.ERROR):
        with pytest.raises(sqlite3.IntegrityError):
            safe_execute(
                con,
                "INSERT INTO u (id) SELECT id FROM u",  # collides, no params
                table="u", logger=LOGGER,
            )
    assert "no bound parameters" in caplog.records[0].message.lower()


def test_safe_executemany_ok_inserts_all_and_is_quiet(con, caplog):
    with caplog.at_level(logging.ERROR):
        safe_executemany(
            con, "INSERT INTO t (id, v) VALUES (?, ?)",
            [(1, "a"), (2, "b"), (3, "c")],
            table="t", logger=LOGGER,
        )
    assert con.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 3
    assert caplog.records == []


def test_safe_executemany_pinpoints_offending_row(con, caplog):
    con.execute("INSERT INTO t (id, v) VALUES (10, 'dup')")
    with caplog.at_level(logging.ERROR):
        with pytest.raises(sqlite3.IntegrityError):
            safe_executemany(
                con, "INSERT INTO t (id, v) VALUES (?, ?)",
                [(1, "a"), (2, "dup"), (3, "c")],  # row 1 collides on v UNIQUE
                table="t", logger=LOGGER,
            )
    assert len(caplog.records) == 1
    msg = caplog.records[0].message
    assert "DB batch write failed on t" in msg
    assert "row 1 of 3" in msg
    assert "(2, 'dup')" in msg
    # no partial rows left behind
    assert con.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
