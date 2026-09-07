"""Tests for :mod:`msianalyzer.core.annotation.consensus` (Stage A'')."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from msianalyzer.core.analysis_db import init_analysis_db, register_sample
from msianalyzer.core.annotation.consensus import (
    ScanStat,
    consensus_score,
    peak_term,
    pick_feature,
    purity_term,
    run_consensus,
)
from msianalyzer.core.config.config import ConsensusConfig


# ---------------------------------------------------------------------------
# pure scoring
# ---------------------------------------------------------------------------


def test_peak_term_saturates_at_target():
    assert peak_term(0, 10) == 0.0
    assert peak_term(5, 10) == pytest.approx(0.5)
    assert peak_term(10, 10) == 1.0
    assert peak_term(50, 10) == 1.0
    assert peak_term(3, 0) == 1.0  # target <= 0 disables the term


def test_purity_term_neutral_for_none_and_clamped():
    assert purity_term(None, 0.5) == 0.5
    assert purity_term(0.9, 0.5) == pytest.approx(0.9)
    assert purity_term(1.7, 0.5) == 1.0
    assert purity_term(-0.2, 0.5) == 0.0


def test_consensus_score_without_annotation_uses_purity_and_peaks():
    s = ScanStat(1, 10, purity=0.8, n_peaks=5, best_score=None)
    # 1.0 * 0.8 * 0.5
    assert consensus_score(s, target_peaks=10, neutral_purity=0.5) == pytest.approx(0.4)


def test_consensus_score_with_annotation_multiplies_in_score():
    s = ScanStat(1, 10, purity=1.0, n_peaks=20, best_score=0.6)
    assert consensus_score(s, target_peaks=10, neutral_purity=0.5) == pytest.approx(0.6)


def test_pick_feature_prefers_cleaner_richer_scan():
    stats = [
        ScanStat(1, 1, purity=0.3, n_peaks=3, best_score=None),
        ScanStat(1, 2, purity=0.95, n_peaks=15, best_score=None),
        ScanStat(2, 3, purity=0.6, n_peaks=8, best_score=None),
    ]
    row = pick_feature(
        7, 500.1234, stats, target_peaks=10, neutral_purity=0.5, min_purity=None
    )
    assert row.best_scan_id == 2
    assert row.n_ms2 == 3
    assert row.n_ms2_considered == 3
    assert row.n_ms2_scored == 0


def test_pick_feature_min_purity_excludes_from_pick_not_from_count():
    stats = [
        ScanStat(1, 1, purity=0.2, n_peaks=30, best_score=0.9),  # richest but dirty
        ScanStat(1, 2, purity=0.85, n_peaks=6, best_score=0.4),
    ]
    row = pick_feature(
        1, 100.0, stats, target_peaks=10, neutral_purity=0.5, min_purity=0.5
    )
    assert row.best_scan_id == 2
    assert row.n_ms2 == 2
    assert row.n_ms2_considered == 1


def test_pick_feature_returns_none_when_all_excluded():
    stats = [ScanStat(1, 1, purity=0.1, n_peaks=5, best_score=None)]
    assert (
        pick_feature(1, 1.0, stats, target_peaks=10, neutral_purity=0.5, min_purity=0.5)
        is None
    )


def test_pick_feature_none_for_no_scans():
    assert (
        pick_feature(1, 1.0, [], target_peaks=10, neutral_purity=0.5, min_purity=None)
        is None
    )


# ---------------------------------------------------------------------------
# orchestrator
# ---------------------------------------------------------------------------


def _analysis_db(tmp_path: Path, *, with_purity=True, with_annotations=True) -> Path:
    adb = tmp_path / "analysis.db"
    init_analysis_db(adb).close()
    register_sample(adb, name="s1", raw_db_path=tmp_path / "s1.db")
    con = sqlite3.connect(adb)
    con.execute("PRAGMA foreign_keys = ON")
    try:
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) VALUES "
            "(1, 500.0, '{}'), (2, 700.0, '{}')"
        )
        # feature 1: two scans; feature 2: one scan
        con.executemany(
            "INSERT INTO ms2_associations "
            "(sample_id, scan_id, feature_id, match_key, n_features_in_window, "
            " n_peaks, precursor_only) VALUES (?,?,?,?,?,?,0)",
            [
                (1, 10, 1, "precursor_mz", 1, 4),
                (1, 11, 1, "precursor_mz", 2, 25),
                (1, 20, 2, "precursor_mz", 1, 12),
            ],
        )
        if with_purity:
            con.executemany(
                "INSERT INTO precursor_purity "
                "(sample_id, ms2_scan_id, bracket_kind, precursor_found, "
                " n_peaks_in_window, purity) VALUES (?,?,'parent_only',1,?,?)",
                [(1, 10, 1, 0.98), (1, 11, 3, 0.30), (1, 20, 1, 0.95)],
            )
        if with_annotations:
            con.execute(
                "INSERT INTO annotation_libraries (id, path, name) "
                "VALUES (1, 'lib.db', 'lib')"
            )
            con.executemany(
                "INSERT INTO ms2_annotations "
                "(sample_id, scan_id, feature_id, library_id, library_spectrum_id, "
                " score, dot_product_score, lib_coverage, emp_coverage, "
                " coverage_score, n_matched_peaks, n_lib_peaks, n_emp_peaks_raw, "
                " n_emp_peaks_filtered, rank, compound_name, inchikey) "
                "VALUES (?,?,?,1,?,?,?,1,1,1,3,3,5,4,1,?,?)",
                [
                    (1, 10, 1, 100, 0.7, 0.7, "CleanHit", "KEY-CLEAN"),
                    (1, 11, 1, 101, 0.9, 0.9, "DirtyHit", "KEY-DIRTY"),
                    (1, 20, 2, 102, 0.5, 0.5, "F2Hit", "KEY-F2"),
                ],
            )
        con.commit()
    finally:
        con.close()
    return adb


def test_run_consensus_picks_clean_scan_and_persists(tmp_path):
    adb = _analysis_db(tmp_path)
    result = run_consensus(adb, ConsensusConfig(min_purity=0.5))

    assert result.n_features == 2
    assert result.n_features_scored == 2

    with sqlite3.connect(adb) as con:
        rows = {
            r[0]: r
            for r in con.execute(
                "SELECT feature_id, best_scan_id, n_ms2, n_ms2_considered, "
                "best_compound_name FROM feature_ms2_consensus"
            ).fetchall()
        }
    # scan 11 is richer + higher library score but purity 0.30 < min_purity
    assert rows[1][1] == 10
    assert rows[1][2] == 2
    assert rows[1][3] == 1
    assert rows[1][4] == "CleanHit"
    assert rows[2][1] == 20


def test_run_consensus_works_without_annotations(tmp_path):
    adb = _analysis_db(tmp_path, with_annotations=False)
    result = run_consensus(adb, ConsensusConfig(min_purity=None))

    assert result.n_features == 2
    assert result.n_features_scored == 0
    with sqlite3.connect(adb) as con:
        best = dict(
            con.execute(
                "SELECT feature_id, best_scan_id FROM feature_ms2_consensus"
            ).fetchall()
        )
    # no library: purity (0.98 vs 0.30) outweighs scan 11's extra peaks
    assert best[1] == 10


def test_run_consensus_peak_richness_breaks_purity_tie(tmp_path):
    adb = _analysis_db(tmp_path, with_annotations=False, with_purity=False)
    run_consensus(adb, ConsensusConfig(min_purity=None))
    with sqlite3.connect(adb) as con:
        best = dict(
            con.execute(
                "SELECT feature_id, best_scan_id FROM feature_ms2_consensus"
            ).fetchall()
        )
    # equal (neutral) purity -> scan 11 wins feature 1 on peak richness
    assert best[1] == 11


def test_run_consensus_is_idempotent(tmp_path):
    adb = _analysis_db(tmp_path)
    run_consensus(adb, ConsensusConfig())
    run_consensus(adb, ConsensusConfig())
    with sqlite3.connect(adb) as con:
        (n,) = con.execute(
            "SELECT COUNT(*) FROM feature_ms2_consensus"
        ).fetchone()
    assert n == 2
