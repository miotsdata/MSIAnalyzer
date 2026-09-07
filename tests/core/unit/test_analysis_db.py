from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from msianalyzer.core.analysis_db import (
    analysis_db_path,
    attach_raw,
    init_analysis_db,
    is_command_already_run,
    load_features,
    log_command,
    register_sample,
    save_features,
    write_metadata,
)
from msianalyzer.core.spectra.mz_tools import align_mz_across_samples


# ---------------------------------------------------------------------------
# path helper
# ---------------------------------------------------------------------------


def test_analysis_db_path_default_and_override(tmp_path: Path):
    assert analysis_db_path(tmp_path, "run123") == tmp_path / "analysis_run123.db"
    assert (
        analysis_db_path(tmp_path, "run123", override="custom.db")
        == tmp_path / "custom.db"
    )


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------


def test_init_analysis_db_creates_tables(tmp_path: Path):
    db = tmp_path / "analysis.db"
    conn = init_analysis_db(db)
    try:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert {
            "metadata",
            "commands",
            "samples",
            "aggregated_spectra",
            "features",
            "ms2_associations",
            "ms2_window_features",
            "feature_ms2_summary",
            "precursor_purity",
            "annotation_libraries",
            "ms2_annotations",
        } <= tables
        # commands table has the analysis-only sample_id column
        cmd_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(commands)").fetchall()
        }
        assert "sample_id" in cmd_cols
        # precursor_purity carries the purity headline columns
        purity_cols = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(precursor_purity)"
            ).fetchall()
        }
        assert {
            "ms2_scan_id",
            "parent_ms1_scan_id",
            "next_ms1_scan_id",
            "bracket_kind",
            "n_peaks_in_window",
            "runner_up_rel_int",
            "purity",
            "purity_parent",
        } <= purity_cols
    finally:
        conn.close()

    # idempotent
    init_analysis_db(db).close()


# ---------------------------------------------------------------------------
# samples
# ---------------------------------------------------------------------------


def test_register_sample_is_idempotent(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()

    sid1 = register_sample(db, name="s1", raw_db_path=tmp_path / "s1.db", polarity="POSITIVE")
    sid2 = register_sample(db, name="s1", raw_db_path=tmp_path / "s1.db")
    sid_other = register_sample(db, name="s2", raw_db_path=tmp_path / "s2.db")

    assert sid1 == sid2
    assert sid_other != sid1

    with sqlite3.connect(db) as con:
        rows = con.execute("SELECT name, polarity FROM samples ORDER BY sample_id").fetchall()
    assert rows == [("s1", "POSITIVE"), ("s2", None)]


# ---------------------------------------------------------------------------
# commands / provenance
# ---------------------------------------------------------------------------


def test_log_command_and_is_command_already_run(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()

    assert is_command_already_run("detect", "run1", db) is False

    cid = log_command(db, "detect", {"a": 1}, run_id="run1", sample_id=3)
    assert isinstance(cid, int)

    # run-scoped match
    assert is_command_already_run("detect", "run1", db) is True
    # sample-scoped match
    assert is_command_already_run("detect", "run1", db, sample_id=3) is True
    # wrong sample -> no match
    assert is_command_already_run("detect", "run1", db, sample_id=99) is False
    # wrong run -> no match
    assert is_command_already_run("detect", "other", db, sample_id=3) is False


def test_write_metadata_upserts(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()

    write_metadata(db, {"analysis_id": "abc", "n_samples": 2})
    write_metadata(db, {"n_samples": 3})

    with sqlite3.connect(db) as con:
        meta = dict(con.execute("SELECT key, value FROM metadata").fetchall())
    assert meta == {"analysis_id": "abc", "n_samples": "3"}


# ---------------------------------------------------------------------------
# features round-trip
# ---------------------------------------------------------------------------


def test_features_round_trip(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()

    aligned = align_mz_across_samples(
        mz_arrays=[np.array([100.0, 200.0, 500.0]), np.array([100.0005, 300.0])],
        sample_names=["A", "B"],
        align_ppm=10.0,
    )

    save_features(db, aligned, command_id=None)
    restored = load_features(db)

    pd.testing.assert_index_equal(restored.index, aligned.index)
    assert list(restored.columns) == ["A", "B"]
    assert restored["A"].dtype == "Int64"
    # membership preserved (NaN where a sample did not contribute)
    assert restored.loc[300.0, "B"] == 1
    assert pd.isna(restored.loc[300.0, "A"])


def test_save_features_replaces_previous(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()

    df1 = align_mz_across_samples([np.array([100.0])], sample_names=["A"])
    df2 = align_mz_across_samples(
        [np.array([200.0, 300.0])], sample_names=["A"]
    )
    save_features(db, df1)
    save_features(db, df2)

    with sqlite3.connect(db) as con:
        n = con.execute("SELECT COUNT(*) FROM features").fetchone()[0]
    assert n == 2


def test_load_features_empty(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    assert load_features(db).empty


# ---------------------------------------------------------------------------
# attach_raw
# ---------------------------------------------------------------------------


def test_attach_raw_allows_cross_db_read(tmp_path: Path):
    raw = tmp_path / "raw.db"
    with sqlite3.connect(raw) as con:
        con.execute("CREATE TABLE ms1_scans (scan_id INTEGER)")
        con.execute("INSERT INTO ms1_scans VALUES (1), (2)")

    adb = tmp_path / "analysis.db"
    conn = init_analysis_db(adb)
    try:
        attach_raw(conn, raw, alias="raw")
        n = conn.execute("SELECT COUNT(*) FROM raw.ms1_scans").fetchone()[0]
        assert n == 2
    finally:
        conn.close()


def test_attach_raw_rejects_bad_alias(tmp_path: Path):
    adb = tmp_path / "analysis.db"
    conn = init_analysis_db(adb)
    try:
        with pytest.raises(ValueError):
            attach_raw(conn, tmp_path / "raw.db", alias="bad alias;")
    finally:
        conn.close()
