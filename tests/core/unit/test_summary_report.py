"""Tests for :mod:`msianalyzer.core.report.summary`."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytest

from msianalyzer.core.analysis_db import (
    init_analysis_db,
    log_command,
    register_sample,
    save_features,
)
from msianalyzer.core.report.summary import (
    AssocPuritySample,
    AssociatedPuritySummary,
    BasePeakIntensitySample,
    BasePeakIntensitySummary,
    Ms2Summary,
    RecheckSummary,
    annotation_summary,
    associated_purity,
    base_peak_intensity,
    build_summary_report,
    collect_stats,
    feature_membership,
    figure_base_peak_intensity,
    figure_base_peak_intensity_per_sample,
    figure_ms2_association_per_sample,
    figure_overlap_upset,
    figure_per_sample,
    figure_purity,
    figure_purity_per_sample,
    figure_unassociated_recheck,
    mad_filter_summary,
    ms2_summary,
    overlap_combos,
    per_sample_counts,
    per_sample_ms2,
    unassociated_recheck,
)
from msianalyzer.core.spectra.average_spectra import mad_threshold


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------


def _raw_db(
    path: Path,
    *,
    n_ms1: int,
    n_ms2: int,
    n_pixels: int,
    pixel_ms1_ids: Sequence[int] | None = None,
    ms2_intensity_arrays: Sequence[Sequence[float]] | None = None,
) -> Path:
    """``ms2_intensity_arrays``, when given, must have length ``n_ms2`` —
    one raw ``intensity_array`` per scan (an empty sequence produces an
    empty, still non-NULL blob — the "no peaks" case). Omitted, every MS2
    scan shares one hardcoded ``[1.0, 2.0, 3.0]`` blob, same as before."""
    from msianalyzer.core.parser.mzml_parser import array_to_blob, init_raw_db

    blob = array_to_blob(np.array([1.0, 2.0, 3.0]))
    con = init_raw_db(path)
    try:
        con.executemany(
            "INSERT INTO ms1_scans (scan_id, rt, polarity, mz_array, intensity_array) "
            "VALUES (?,?,?,?,?)",
            [(i, float(i), "+", blob, blob) for i in range(1, n_ms1 + 1)],
        )
        if ms2_intensity_arrays is not None:
            assert len(ms2_intensity_arrays) == n_ms2
            ms2_rows = []
            for i, vals in enumerate(ms2_intensity_arrays, start=1):
                ivals = np.asarray(vals, dtype=float)
                mz_blob = array_to_blob(np.arange(ivals.size, dtype=float))
                i_blob = array_to_blob(ivals)
                ms2_rows.append((1000 + i, float(i), "ms2", mz_blob, i_blob))
            con.executemany(
                "INSERT INTO ms2_scans (scan_id, rt, filter_string, mz_array, "
                "intensity_array) VALUES (?,?,?,?,?)",
                ms2_rows,
            )
        else:
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
        if pixel_ms1_ids is not None:
            con.execute(
                "CREATE TABLE pixel_ms1_scans ("
                "pixel_id INTEGER NOT NULL, scan_id INTEGER NOT NULL, "
                "PRIMARY KEY (pixel_id, scan_id))"
            )
            con.executemany(
                "INSERT INTO pixel_ms1_scans (pixel_id, scan_id) VALUES (?,?)",
                [(1, int(sid)) for sid in pixel_ms1_ids],
            )
        con.commit()
    finally:
        con.close()
    return path


def _agg(con, run_id, sample_id, command_id, mz):
    from msianalyzer.core.parser.mzml_parser import array_to_blob

    blob = array_to_blob(np.asarray(mz, dtype=float))
    con.execute(
        "INSERT INTO aggregated_spectra (run_id, sample_id, command_id, mz_array, "
        "intensity_array) VALUES (?,?,?,?,?)",
        (run_id, sample_id, command_id, blob, blob),
    )


def _analysis_db(tmp_path: Path) -> tuple[Path, dict[int, str]]:
    """Two samples: overlapping features, MS2 with purity, and a mix of
    associated / unassociated (recoverable, unrecoverable, no-precursor) scans.
    """
    # raw1 maps MS1 scans 1..20 to pixels; scan 99 is "off-pixel"
    raw1 = _raw_db(
        tmp_path / "s1.db", n_ms1=40, n_ms2=6, n_pixels=5,
        pixel_ms1_ids=range(1, 21),
    )
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

    log_command(
        adb, "group_ms2",
        {"assoc_ppm": 10.0, "default_isolation_half_width": 0.5}, run_id="r",
    )
    c_cent1 = log_command(adb, "detect_ms1_centroids", {}, run_id="r", sample_id=sid1)
    c_cent2 = log_command(adb, "detect_ms1_centroids", {}, run_id="r", sample_id=sid2)
    c_filt1 = log_command(adb, "filter_spectra", {}, run_id="r", sample_id=sid1)
    c_filt2 = log_command(adb, "filter_spectra", {}, run_id="r", sample_id=sid2)

    with sqlite3.connect(adb) as con:
        con.execute("PRAGMA foreign_keys = ON")
        # pre-filter centroids (detect_ms1_centroids) and filtered peaks
        _agg(con, "r", sid1, c_cent1, [500.0, 555.5, 600.0])
        _agg(con, "r", sid2, c_cent2, [500.0, 700.0])
        _agg(con, "r", sid1, c_filt1, [500.0, 600.0])
        _agg(con, "r", sid2, c_filt2, [500.0])

        con.executemany(
            "INSERT INTO ms2_associations "
            "(sample_id, scan_id, feature_id, match_key, "
            " n_peaks, fragmentation_factor, precursor_mz, isolation_window_target, "
            " isolation_window_lower, isolation_window_upper) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                # s1: two associated, one unassociated but recoverable (555.5)
                # -- 1003 is deliberately low-fragmentation (< the 0.2 default
                # cutoff), every other scan high (well above it).
                (sid1, 1001, 1, "precursor_mz", 10, 0.9, 500.0, 500.0, 0.5, 0.5),
                (sid1, 1002, 1, "precursor_mz", 20, 0.9, 500.0, 500.0, 0.5, 0.5),
                (sid1, 1003, None, "precursor_mz", 2, 0.1, 555.5, 555.5, 0.5, 0.5),
                # s2: one associated, one unassociated + unrecoverable (999.0),
                #     one unassociated with no precursor m/z at all
                (sid2, 1001, 2, "precursor_mz", 8, 0.9, 700.0, 700.0, 0.5, 0.5),
                (sid2, 1002, None, "precursor_mz", 4, 0.9, 999.0, 999.0, 0.5, 0.5),
                (sid2, 1003, None, "none", 1, 0.9, None, None, None, None),
            ],
        )
        con.executemany(
            "INSERT INTO precursor_purity "
            "(sample_id, ms2_scan_id, parent_ms1_scan_id, "
            " precursor_confirmed, purity_score) "
            "VALUES (?,?,?,?,?)",
            [
                # s1: 1001/1002 are ASSOCIATED (feature_id=1); 2001/2002 are
                # extra, unassociated scans kept only for "all MS2" context.
                (sid1, 1001, 1, 1, 0.90),
                (sid1, 1002, 1, 1, 0.40),
                (sid1, 2001, 1, 1, 0.05),
                (sid1, 2002, 1, 0, 0.15),
                # s2: 1001 is ASSOCIATED (feature_id=2)
                (sid2, 1001, 5, 1, 0.70),
                (sid2, 2001, 5, 1, 0.10),
            ],
        )
        con.commit()

    return adb, {sid1: str(raw1), sid2: str(raw2)}


# ---------------------------------------------------------------------------
# pure stats
# ---------------------------------------------------------------------------


def test_per_sample_counts(tmp_path):
    adb, raw_map = _analysis_db(tmp_path)
    by_name = {c.name: c for c in per_sample_counts(adb, raw_map)}
    assert by_name["s1"].n_ms1 == 40
    assert by_name["s1"].n_ms2 == 6
    assert by_name["s1"].n_pixels == 5
    assert by_name["s1"].n_detected_peaks == 2  # filter_spectra length
    assert by_name["s1"].n_features == 2
    assert by_name["s2"].n_features == 2


def test_feature_membership_and_overlap_combos():
    feats = pd.DataFrame(
        {"a": pd.array([0, 1, pd.NA], dtype="Int64"),
         "b": pd.array([0, pd.NA, 0], dtype="Int64")},
        index=[10.0, 20.0, 30.0],
    )
    combos = overlap_combos(feature_membership(feats), top_n=10)
    as_dict = {tuple(s): n for s, n in combos}
    assert as_dict[("a", "b")] == 1
    assert as_dict[("a",)] == 1
    assert as_dict[("b",)] == 1


def test_overlap_combos_empty():
    assert overlap_combos(pd.DataFrame()) == []


def test_ms2_summary(tmp_path):
    adb, _ = _analysis_db(tmp_path)
    s = ms2_summary(adb)
    assert s.n_total == 6
    assert s.n_associated == 3
    assert s.n_unassociated == 3
    assert s.n_low_fragmentation == 1


def test_per_sample_ms2(tmp_path):
    adb, _ = _analysis_db(tmp_path)
    by_name = {p.name: p for p in per_sample_ms2(adb)}
    assert (by_name["s1"].n_total, by_name["s1"].n_associated) == (3, 2)
    assert by_name["s1"].n_unassociated == 1
    assert (by_name["s2"].n_total, by_name["s2"].n_associated) == (3, 1)
    assert by_name["s2"].n_unassociated == 2


def test_associated_purity_scopes_to_associated_ms2(tmp_path):
    adb, _ = _analysis_db(tmp_path)
    a = associated_purity(adb, cutoff=0.8)
    # only the 3 associated scans (feature_id not null) count
    assert a.n_associated == 3
    assert sorted(a.frac_values) == [0.4, 0.7, 0.9]
    assert a.n_ge_cutoff == 1  # 0.9 only
    assert round(a.pct_ge_cutoff) == 33
    # every scan with a non-null purity_score is in the context list
    assert len(a.frac_values_all) == 6
    by_name = {p.name: p for p in a.per_sample}
    assert sorted(by_name["s1"].frac_values) == [0.4, 0.9]
    assert by_name["s1"].n_ge_cutoff == 1
    assert by_name["s2"].frac_values == [0.7]


def test_base_peak_intensity_scopes_to_all_ms2_regardless_of_association(tmp_path):
    adb, raw_map = _analysis_db(tmp_path)
    b = base_peak_intensity(adb, raw_map)
    # 6 MS2 in s1 + 4 in s2 = 10 total, all with peaks — vs. only 3
    # associated (see test_associated_purity_scopes_to_associated_ms2) —
    # the key behavioral contrast with associated_purity.
    assert b.n_total == 10
    assert b.n_valid == 10
    assert b.n_excluded == 0
    assert b.values == [3.0] * 10  # every scan shares the [1,2,3] fixture blob
    by_name = {p.name: p for p in b.per_sample}
    assert by_name["s1"].n_total == 6
    assert by_name["s2"].n_total == 4


def test_base_peak_intensity_excludes_empty_intensity_arrays(tmp_path):
    raw = _raw_db(
        tmp_path / "s1.db", n_ms1=1, n_ms2=3, n_pixels=0,
        ms2_intensity_arrays=[[10.0, 50.0], [], [5.0]],
    )
    adb = tmp_path / "analysis.db"
    init_analysis_db(adb).close()
    sid = register_sample(adb, name="s1", raw_db_path=raw)

    b = base_peak_intensity(adb, {sid: str(raw)})
    assert b.n_total == 3
    assert b.n_valid == 2
    assert b.n_excluded == 1
    assert b.values == [50.0, 5.0]
    p = b.per_sample[0]
    assert p.n_total == 3
    assert p.n_valid == 2


def test_base_peak_intensity_computes_max_not_tic_or_sum(tmp_path):
    raw = _raw_db(
        tmp_path / "s1.db", n_ms1=1, n_ms2=1, n_pixels=0,
        ms2_intensity_arrays=[[1.0, 2.0, 100.0, 3.0]],
    )
    adb = tmp_path / "analysis.db"
    init_analysis_db(adb).close()
    sid = register_sample(adb, name="s1", raw_db_path=raw)

    b = base_peak_intensity(adb, {sid: str(raw)})
    assert b.values == [100.0]  # max, not sum (106.0) or count (4)


def test_base_peak_intensity_handles_missing_raw_db(tmp_path):
    adb = tmp_path / "analysis.db"
    init_analysis_db(adb).close()
    register_sample(adb, name="s1", raw_db_path=tmp_path / "does_not_exist.db")

    b = base_peak_intensity(adb)
    assert b.n_total == 0
    assert b.n_valid == 0
    assert b.per_sample[0].n_total == 0


def test_unassociated_recheck(tmp_path):
    adb, _ = _analysis_db(tmp_path)
    r = unassociated_recheck(adb)
    assert r.assoc_ppm == 10.0
    assert r.n_unassociated == 3
    assert r.n_would_associate == 1  # s1/1003 precursor 555.5 hits pre-filter peak
    assert r.n_still_unassociated == 1  # s2/1002 precursor 999.0 hits nothing
    assert r.n_no_precursor_mz == 1  # s2/1003 has no precursor m/z
    by_name = {p["name"]: p for p in r.per_sample}
    assert by_name["s1"]["n_would_associate"] == 1
    assert by_name["s2"]["n_still_unassociated"] == 1
    assert by_name["s2"]["n_no_precursor_mz"] == 1


def test_mad_filter_summary_none_when_not_used(tmp_path):
    adb, _ = _analysis_db(tmp_path)  # filter_spectra logged with empty arguments
    assert mad_filter_summary(adb) is None


def test_mad_filter_summary(tmp_path):
    adb = tmp_path / "analysis.db"
    init_analysis_db(adb).close()
    sid = register_sample(adb, name="s1", raw_db_path=tmp_path / "s1.db")

    pre_intensities = [10.0, 20.0, 30.0, 40.0, 500.0]
    post_intensities = [500.0]

    c_cent = log_command(adb, "detect_ms1_centroids", {}, run_id="r", sample_id=sid)
    c_filt = log_command(
        adb, "filter_spectra",
        {"filter_mad": True, "filter_mad_log": True, "filter_mad_nmads": 2.0},
        run_id="r", sample_id=sid,
    )
    with sqlite3.connect(adb) as con:
        con.execute("PRAGMA foreign_keys = ON")
        _agg(con, "r", sid, c_cent, pre_intensities)
        _agg(con, "r", sid, c_filt, post_intensities)
        con.commit()

    m = mad_filter_summary(adb)
    assert m is not None
    assert m.n_mads == 2.0
    assert m.log is True
    assert len(m.per_sample) == 1
    s = m.per_sample[0]
    assert s.name == "s1"
    assert s.n_total == 5
    assert s.n_survived == 1
    assert s.n_removed == 4
    expected = mad_threshold(np.array(pre_intensities), log=True, n_mads=2.0)
    assert s.threshold == pytest.approx(expected)


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------


def _empty_assoc_purity() -> AssociatedPuritySummary:
    return AssociatedPuritySummary(0.8, 0, 0, [], [], [])


def _empty_bpi() -> BasePeakIntensitySummary:
    return BasePeakIntensitySummary(0, 0, 0, [], [])


def test_figures_return_figures_and_tolerate_empty():
    assert isinstance(figure_per_sample([]), go.Figure)
    assert isinstance(figure_overlap_upset([], []), go.Figure)
    assert isinstance(figure_ms2_association_per_sample([]), go.Figure)
    assert isinstance(figure_purity(_empty_assoc_purity()), go.Figure)
    assert isinstance(figure_purity_per_sample(_empty_assoc_purity()), go.Figure)
    assert isinstance(figure_base_peak_intensity(_empty_bpi()), go.Figure)
    assert isinstance(figure_base_peak_intensity_per_sample(_empty_bpi()), go.Figure)
    assert isinstance(
        figure_unassociated_recheck(RecheckSummary(10.0, 0, 0, 0, 0, [])),
        go.Figure,
    )


def test_figure_purity_per_sample_is_overlaid_density(tmp_path):
    adb, _ = _analysis_db(tmp_path)
    a = associated_purity(adb, cutoff=0.8)
    fig = figure_purity_per_sample(a)
    # s1 has 2 associated purities (enough for a density); s2 has only 1
    # and is skipped — a density needs at least two points.
    assert len(fig.data) == 1
    trace = fig.data[0]
    assert trace.mode == "lines"
    assert trace.name.startswith("s1")
    assert list(fig.layout.xaxis.range) == [0, 1]
    assert fig.layout.showlegend is not False  # legend stays on for toggling


def test_figure_purity_per_sample_handles_zero_variance_sample():
    assoc = AssociatedPuritySummary(
        cutoff=0.8, n_associated=3, n_ge_cutoff=0,
        frac_values=[0.5, 0.5, 0.5], frac_values_all=[0.5, 0.5, 0.5],
        per_sample=[
            AssocPuritySample(
                sample_id=1, name="s1", n_associated=3, n_ge_cutoff=0,
                frac_values=[0.5, 0.5, 0.5],
            )
        ],
    )
    fig = figure_purity_per_sample(assoc)
    assert len(fig.data) == 1
    assert np.max(fig.data[0].y) > 0  # a spike, not a crash


def test_figure_base_peak_intensity_is_log10_scaled():
    values = [10.0, 100.0, 1_000.0, 10_000.0, 100_000.0]
    summary = BasePeakIntensitySummary(
        n_total=5, n_valid=5, n_excluded=0, values=values, per_sample=[],
    )
    fig = figure_base_peak_intensity(summary)
    np.testing.assert_allclose(sorted(fig.data[0].x), sorted(np.log10(values)))
    assert fig.layout.xaxis.title.text == "log10(base peak intensity)"


def test_figure_base_peak_intensity_drops_nonpositive_values():
    summary = BasePeakIntensitySummary(
        n_total=3, n_valid=3, n_excluded=0,
        values=[0.0, 10.0, 100.0], per_sample=[],
    )
    fig = figure_base_peak_intensity(summary)
    assert len(fig.data[0].x) == 2  # 0.0 can't be log10'd — dropped, not crashed


def test_figure_base_peak_intensity_per_sample_is_overlaid_density_on_data_driven_grid():
    s1 = BasePeakIntensitySample(
        sample_id=1, name="s1", n_total=3, n_valid=3, values=[100.0, 300.0, 900.0],
    )
    s2 = BasePeakIntensitySample(
        sample_id=2, name="s2", n_total=3, n_valid=3, values=[1e5, 3e5, 9e5],
    )
    summary = BasePeakIntensitySummary(
        n_total=6, n_valid=6, n_excluded=0,
        values=s1.values + s2.values, per_sample=[s1, s2],
    )
    fig = figure_base_peak_intensity_per_sample(summary)
    assert len(fig.data) == 2
    assert all(trace.mode == "lines" for trace in fig.data)
    # grid spans the dataset's actual log10 range, not a fixed [0, 1]
    grid = np.asarray(fig.data[0].x)
    assert grid.min() < np.log10(200)
    assert grid.max() > np.log10(5e5)


def test_figure_base_peak_intensity_per_sample_skips_samples_with_fewer_than_two_values():
    s1 = BasePeakIntensitySample(sample_id=1, name="s1", n_total=1, n_valid=1, values=[100.0])
    s2 = BasePeakIntensitySample(
        sample_id=2, name="s2", n_total=2, n_valid=2, values=[100.0, 200.0],
    )
    summary = BasePeakIntensitySummary(
        n_total=3, n_valid=3, n_excluded=0,
        values=s1.values + s2.values, per_sample=[s1, s2],
    )
    fig = figure_base_peak_intensity_per_sample(summary)
    assert len(fig.data) == 1
    assert fig.data[0].name.startswith("s2")


def test_figure_base_peak_intensity_per_sample_handles_zero_variance_sample():
    s1 = BasePeakIntensitySample(
        sample_id=1, name="s1", n_total=3, n_valid=3, values=[500.0, 500.0, 500.0],
    )
    summary = BasePeakIntensitySummary(
        n_total=3, n_valid=3, n_excluded=0, values=s1.values, per_sample=[s1],
    )
    fig = figure_base_peak_intensity_per_sample(summary)
    assert len(fig.data) == 1
    assert np.max(fig.data[0].y) > 0  # a spike, not a crash


def test_figure_ms2_association_per_sample_is_100_pct(tmp_path):
    adb, _ = _analysis_db(tmp_path)
    fig = figure_ms2_association_per_sample(per_sample_ms2(adb))
    # two stacked traces, each bar sums to 100
    totals = np.array(fig.data[0].y) + np.array(fig.data[1].y)
    np.testing.assert_allclose(totals, 100.0)


# ---------------------------------------------------------------------------
# annotation section
# ---------------------------------------------------------------------------


def _seed_annotations(adb: Path) -> None:
    """A library + ms2_annotations on the `_analysis_db` fixture.

    Feature 1 (scans 1001/1002 of s1): CompA is the best hit (0.85), CompB a
    second plausible one (0.55); both scans' top hit is CompA. Feature 2
    (scan 1001 of s2): CompC (0.72). Consensus for feature 1 points at 1001.
    """
    _ann = (
        "INSERT INTO ms2_annotations (sample_id, scan_id, feature_id, library_id, "
        "library_spectrum_id, inchikey, compound_name, score, dot_product_score, "
        "lib_coverage, emp_coverage, coverage_score, n_matched_peaks, n_lib_peaks, "
        "n_emp_peaks_raw, n_emp_peaks_filtered, rank_ms2, rank_feature, "
        "rank_feature_sample, rank_scan_feature, rank_scan_feature_sample, "
        "precursor_confirmed, fragmentation_factor) "
        "VALUES (?,?,?,1,?,?,?,?,?,1,1,1,3,3,5,4,?,?,?,?,?,?,?)"
    )
    with sqlite3.connect(adb) as con:
        con.execute("PRAGMA foreign_keys = ON")
        con.execute(
            "INSERT INTO annotation_libraries (id, path, name, n_spectra, n_compounds) "
            "VALUES (1, 'lib.db', 'lib', 100, 50)"
        )
        con.executemany(
            _ann,
            [
                # sid, scan, feat, lib_spec_id, inchikey, name, score, dot,
                # rank_ms2, rank_feature, rank_feature_sample,
                # rank_scan_feature, rank_scan_feature_sample,
                # confirmed, fragmentation_factor (all "not low", 0.9)
                (1, 1001, 1, 10, "COMPA0000000AA", "Alpha", 0.85, 0.9, 1, 1, 1, 1, 1, 1, 0.9),
                (1, 1001, 1, 11, "COMPB0000000BB", "Beta", 0.55, 0.6, 2, 3, 3, 1, 1, 1, 0.9),
                (1, 1002, 1, 10, "COMPA0000000AA", "Alpha", 0.60, 0.7, 1, 2, 2, 2, 2, 1, 0.9),
                (2, 1001, 2, 12, "COMPC0000000CC", "Gamma", 0.72, 0.8, 1, 1, 1, 1, 1, 1, 0.9),
            ],
        )
        con.execute(
            "INSERT INTO feature_ms2_consensus (feature_id, feature_mz, "
            "best_sample_id, best_scan_id, n_ms2, n_ms2_considered, n_ms2_scored, "
            "consensus_score) VALUES (1, 500.0, ?, 1001, 2, 2, 2, 0.7)",
            (1,),
        )
        con.commit()


def test_annotation_summary_none_without_library(tmp_path):
    adb, _ = _analysis_db(tmp_path)
    assert annotation_summary(adb) is None


def test_annotation_summary(tmp_path):
    adb, _ = _analysis_db(tmp_path)
    _seed_annotations(adb)
    a = annotation_summary(adb)
    assert a is not None
    assert a.n_features_total == 3         # features 1, 2, 3 (mz 500/600/700)
    assert a.n_ms2_bearing_features == 2   # features 1 and 2 have associated MS2
    assert a.n_features_annotated == 2
    assert sorted(a.best_score_values) == [0.72, 0.85]
    assert a.n_ge == {0.5: 2, 0.75: 1}
    assert a.n_distinct_compounds == 2     # COMPA + COMPC (best hits >= 0.5)
    assert a.ambiguity == {1: 1, 2: 1}     # feature 2 unique, feature 1 has A+B
    assert a.median_gap == pytest.approx(0.30)
    assert a.n_unique_call == 1
    assert (a.n_multiscan_features, a.n_multiscan_agree) == (1, 1)
    assert a.n_best_confident == 1
    assert a.n_confident_precursor_confirmed == 1
    assert a.n_confident_not_flat_fragmentation == 1
    assert (a.n_consensus, a.n_consensus_matches_best) == (1, 1)
    names = {t[2] for t in a.top_features}
    assert names == {"COMPA0000000AA", "COMPC0000000CC"}
    assert [t[4] for t in a.top_features] == [0.85, 0.72]  # best score first
    assert a.libraries[0].n_best_hits == 2


def test_annotation_figures_and_report_section(tmp_path):
    adb, raw_map = _analysis_db(tmp_path)
    _seed_annotations(adb)
    from msianalyzer.core.report.summary import (
        figure_annotation_agreement,
        figure_annotation_ambiguity,
        figure_annotation_score,
        figure_annotation_yield,
    )

    a = annotation_summary(adb)
    for fn in (
        figure_annotation_yield, figure_annotation_score,
        figure_annotation_ambiguity, figure_annotation_agreement,
    ):
        assert isinstance(fn(a), go.Figure)

    out = tmp_path / "r"
    build_summary_report(adb, raw_db_paths=raw_map, out_dir=out)
    text = (out / "summary_report.html").read_text()
    assert "MS2 annotation" in text
    assert "Annotation funnel" in text
    assert "Top features by score" in text
    assert "COMPA00000" in text
    assert "Libraries used for annotation" in text
    assert "<ul class='lib-list'><li>lib — 100 spectra, 50 compounds</li></ul>" in text
    payload = json.loads((out / "summary.json").read_text())
    assert payload["annotation"]["n_features_annotated"] == 2
    assert payload["annotation"]["n_ge"] == {"0.5": 2, "0.75": 1}
    assert "best_score_values" not in payload["annotation"]


# ---------------------------------------------------------------------------
# end to end
# ---------------------------------------------------------------------------


def test_collect_stats(tmp_path):
    adb, raw_map = _analysis_db(tmp_path)
    stats = collect_stats(adb, raw_map)
    assert stats.n_features == 3
    assert len(stats.samples) == 2
    assert stats.ms2.n_total == 6
    assert len(stats.per_sample_ms2) == 2
    assert stats.recheck.n_would_associate == 1
    assert stats.mad_filter is None  # filter_spectra logged with empty arguments
    assert stats.associated_purity.n_associated == 3
    assert stats.associated_purity.n_ge_cutoff == 1
    # all 10 raw MS2 scans, not just the 3 associated ones
    assert stats.base_peak_intensity.n_total == 10
    assert stats.base_peak_intensity.n_valid == 10

    d = stats.to_dict()
    ap = d["associated_purity"]
    assert ap["n_frac_values"] == 3
    assert "frac_values" not in ap and "frac_values_all" not in ap
    assert all("frac_values" not in ps for ps in ap["per_sample"])
    assert ap["pct_ge_cutoff"] == pytest.approx(33.33, abs=0.1)
    bpi = d["base_peak_intensity"]
    assert bpi["n_values"] == 10
    assert "values" not in bpi
    assert all("values" not in ps for ps in bpi["per_sample"])
    assert d["recheck"]["n_would_associate"] == 1
    assert stats.annotation is None  # no library in the base fixture
    assert d["annotation"] is None


def test_build_summary_report_writes_files(tmp_path):
    adb, raw_map = _analysis_db(tmp_path)
    out = tmp_path / "report_out"
    html_path = build_summary_report(adb, raw_db_paths=raw_map, out_dir=out)

    assert html_path == out / "summary_report.html"
    text = html_path.read_text()
    assert "MSIAnalyzer summary report" in text
    assert "plotly" in text.lower()
    assert "pre-filter centroid list" in text  # the recheck sentence
    assert "feed the library search" in text  # the associated-purity sentence
    assert "purity_score" in text
    assert "base peak intensity" in text.lower()
    # the unscored-purity intro and its plot were dropped from the report
    assert "unscored for" not in text
    assert "laser flyback" not in text
    # table of contents links every rendered section
    assert "<nav class='toc'>" in text
    assert "href='#overview'" in text
    assert "href='#per-sample-counts'" in text
    assert "href='#ms2-intensity'" in text
    assert "href='#precursor-purity'" in text
    assert "href='#mad-filter'" not in text  # filter_spectra args were empty
    # section order: MS2 association -> MS2 intensity -> precursor purity
    assert (
        text.index("href='#ms2-association'")
        < text.index("href='#ms2-intensity'")
        < text.index("href='#precursor-purity'")
    )
    # figure titles are promoted to real HTML, not left inside the plotly
    # layout (where they can collide with a wrapped top-anchored legend)
    assert "<h3 class='fig-title'>Per-sample counts</h3>" in text

    payload = json.loads((out / "summary.json").read_text())
    assert payload["n_features"] == 3
    assert payload["base_peak_intensity"]["n_valid"] == 10
    assert "values" not in payload["base_peak_intensity"]
    assert payload["ms2"]["n_total"] == 6
    assert payload["recheck"]["assoc_ppm"] == 10.0
    assert len(payload["per_sample_ms2"]) == 2
    assert payload["associated_purity"]["n_associated"] == 3


def test_build_summary_report_includes_mad_section_when_used(tmp_path):
    adb, raw_map = _analysis_db(tmp_path)
    sid1 = next(iter(raw_map))
    # re-log filter_spectra for sid1 with MAD arguments (last one wins)
    log_command(
        adb, "filter_spectra",
        {"filter_mad": True, "filter_mad_log": True, "filter_mad_nmads": 2.0},
        run_id="r2", sample_id=sid1,
    )

    out = tmp_path / "r"
    build_summary_report(adb, raw_db_paths=raw_map, out_dir=out)
    text = (out / "summary_report.html").read_text()
    assert "href='#mad-filter'" in text
    assert "MS1 peak filtering (MAD)" in text
    assert "survived" in text and "removed" in text


def test_build_summary_report_defaults_out_dir_to_db_parent(tmp_path):
    adb, raw_map = _analysis_db(tmp_path)
    html_path = build_summary_report(adb, raw_db_paths=raw_map)
    assert html_path.parent == adb.parent
