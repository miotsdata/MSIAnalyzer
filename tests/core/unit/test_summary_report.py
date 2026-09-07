"""Tests for :mod:`msianalyzer.core.report.summary`."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytest

from msianalyzer.core.analysis_db import init_analysis_db, register_sample, save_features
from msianalyzer.core.report.summary import (
    build_summary_report,
    collect_stats,
    feature_membership,
    figure_nfw,
    figure_overlap_upset,
    figure_per_sample,
    figure_purity,
    ms2_summary,
    overlap_combos,
    per_sample_counts,
    purity_vs_nfw,
)


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------


def _raw_db(path: Path, *, n_ms1: int, n_ms2: int, n_pixels: int) -> Path:
    from msianalyzer.core.parser.mzml_parser import array_to_blob, init_raw_db

    blob = array_to_blob(np.array([1.0, 2.0, 3.0]))
    con = init_raw_db(path)
    try:
        con.executemany(
            "INSERT INTO ms1_scans (scan_id, rt, polarity, mz_array, intensity_array) "
            "VALUES (?,?,?,?,?)",
            [(i, float(i), "+", blob, blob) for i in range(1, n_ms1 + 1)],
        )
        con.executemany(
            "INSERT INTO ms2_scans (scan_id, rt, filter_string, mz_array, "
            "intensity_array) VALUES (?,?,?,?,?)",
            [(1000 + i, float(i), "ms2", blob, blob) for i in range(1, n_ms2 + 1)],
        )
        if n_pixels:
            con.execute(
                "CREATE TABLE spatial_pixels ("
                "pixel_id INTEGER PRIMARY KEY AUTOINCREMENT, x INTEGER NOT NULL, "
                "y INTEGER NOT NULL, t_start REAL NOT NULL, t_end REAL NOT NULL)"
            )
            con.executemany(
                "INSERT INTO spatial_pixels (x, y, t_start, t_end) VALUES (?,?,?,?)",
                [(i, 0, float(i), float(i) + 1) for i in range(n_pixels)],
            )
        con.commit()
    finally:
        con.close()
    return path


def _analysis_db(tmp_path: Path) -> tuple[Path, dict[int, str]]:
    """Two samples, a 3-feature overlap, and a few MS2 rows with purity."""
    raw1 = _raw_db(tmp_path / "s1.db", n_ms1=40, n_ms2=6, n_pixels=5)
    raw2 = _raw_db(tmp_path / "s2.db", n_ms1=30, n_ms2=4, n_pixels=4)

    adb = tmp_path / "analysis.db"
    init_analysis_db(adb).close()
    sid1 = register_sample(adb, name="s1", raw_db_path=raw1)
    sid2 = register_sample(adb, name="s2", raw_db_path=raw2)

    feats = pd.DataFrame(
        {"s1": pd.array([0, 1, pd.NA], dtype="Int64"),
         "s2": pd.array([0, pd.NA, 2], dtype="Int64")},
        index=pd.Index([500.0, 600.0, 700.0], name="mz"),
    )
    save_features(adb, feats)

    with sqlite3.connect(adb) as con:
        con.execute("PRAGMA foreign_keys = ON")
        con.executemany(
            "INSERT INTO ms2_associations "
            "(sample_id, scan_id, feature_id, match_key, n_features_in_window, "
            " n_peaks, precursor_only) VALUES (?,?,?,?,?,?,?)",
            [
                (sid1, 1001, 1, "precursor_mz", 1, 10, 0),
                (sid1, 1002, 1, "precursor_mz", 3, 20, 0),
                (sid1, 1003, None, "none", 0, 2, 1),
                (sid2, 1001, 2, "precursor_mz", 2, 8, 0),
            ],
        )
        con.executemany(
            "INSERT INTO precursor_purity "
            "(sample_id, ms2_scan_id, bracket_kind, precursor_found, "
            " n_peaks_in_window, purity) VALUES (?,?,'parent_only',?,?,?)",
            [
                (sid1, 1001, 1, 1, 0.97),
                (sid1, 1002, 1, 3, 0.35),
                (sid2, 1001, 1, 2, 0.60),
            ],
        )
        con.commit()

    return adb, {sid1: str(raw1), sid2: str(raw2)}


# ---------------------------------------------------------------------------
# pure stats
# ---------------------------------------------------------------------------


def test_per_sample_counts(tmp_path):
    adb, raw_map = _analysis_db(tmp_path)
    counts = per_sample_counts(adb, raw_map)
    by_name = {c.name: c for c in counts}
    assert by_name["s1"].n_ms1 == 40
    assert by_name["s1"].n_ms2 == 6
    assert by_name["s1"].n_pixels == 5
    assert by_name["s1"].n_features == 2  # contributed to 500 and 600
    assert by_name["s2"].n_features == 2  # contributed to 500 and 700


def test_feature_membership_and_overlap_combos():
    feats = pd.DataFrame(
        {"a": pd.array([0, 1, pd.NA], dtype="Int64"),
         "b": pd.array([0, pd.NA, 0], dtype="Int64")},
        index=[10.0, 20.0, 30.0],
    )
    membership = feature_membership(feats)
    combos = overlap_combos(membership, top_n=10)
    as_dict = {tuple(s): n for s, n in combos}
    assert as_dict[("a", "b")] == 1
    assert as_dict[("a",)] == 1
    assert as_dict[("b",)] == 1


def test_overlap_combos_empty():
    assert overlap_combos(pd.DataFrame()) == []


def test_ms2_summary(tmp_path):
    adb, _ = _analysis_db(tmp_path)
    s = ms2_summary(adb, purity_cutoff=0.8)
    assert s.n_total == 4
    assert s.n_associated == 3
    assert s.n_unassociated == 1
    assert s.n_precursor_only == 1
    assert s.n_unique_window == 1  # only scan 1001/s1
    assert s.n_chimeric_window == 2
    assert s.n_empty_window == 1
    assert s.nfw_distribution == {0: 1, 1: 1, 2: 1, 3: 1}
    assert s.n_purity_scored == 3
    assert s.n_low_purity == 2  # 0.35 and 0.60


def test_purity_vs_nfw(tmp_path):
    adb, _ = _analysis_db(tmp_path)
    purity, nfw = purity_vs_nfw(adb)
    assert purity.shape == nfw.shape == (3,)
    assert set(nfw.tolist()) == {1, 3, 2}


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------


def test_figures_return_figures_and_tolerate_empty():
    assert isinstance(figure_per_sample([]), go.Figure)
    assert isinstance(figure_overlap_upset([], []), go.Figure)
    assert isinstance(figure_nfw(ms2_summary_empty()), go.Figure)
    assert isinstance(figure_purity(ms2_summary_empty()), go.Figure)


def ms2_summary_empty():
    from msianalyzer.core.report.summary import Ms2Summary

    return Ms2Summary(0, 0, 0, 0, 0, 0, 0, {}, 0, 0, 0.8, [])


# ---------------------------------------------------------------------------
# end to end
# ---------------------------------------------------------------------------


def test_collect_stats(tmp_path):
    adb, raw_map = _analysis_db(tmp_path)
    stats = collect_stats(adb, raw_map)
    assert stats.n_features == 3
    assert len(stats.samples) == 2
    assert stats.ms2.n_total == 4
    d = stats.to_dict()
    assert d["ms2"]["n_purity_values"] == 3
    assert "purity_values" not in d["ms2"]


def test_build_summary_report_writes_files(tmp_path):
    adb, raw_map = _analysis_db(tmp_path)
    out = tmp_path / "report_out"
    html_path = build_summary_report(adb, raw_db_paths=raw_map, out_dir=out)

    assert html_path == out / "summary_report.html"
    assert html_path.exists()
    text = html_path.read_text()
    assert "MSIAnalyzer summary report" in text
    assert "Plotly" in text or "plotly" in text

    payload = json.loads((out / "summary.json").read_text())
    assert payload["n_features"] == 3
    assert payload["ms2"]["n_total"] == 4
    assert len(payload["samples"]) == 2


def test_build_summary_report_defaults_out_dir_to_db_parent(tmp_path):
    adb, raw_map = _analysis_db(tmp_path)
    html_path = build_summary_report(adb, raw_db_paths=raw_map)
    assert html_path.parent == adb.parent
