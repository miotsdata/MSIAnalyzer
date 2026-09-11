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
    AssociatedPuritySummary,
    Ms2Summary,
    RecheckSummary,
    UnscoredSummary,
    annotation_summary,
    associated_purity,
    build_summary_report,
    collect_stats,
    feature_membership,
    figure_ms2_association_per_sample,
    figure_overlap_upset,
    figure_per_sample,
    figure_purity,
    figure_purity_per_sample,
    figure_purity_unscored,
    figure_unassociated_recheck,
    ms2_summary,
    overlap_combos,
    per_sample_counts,
    per_sample_ms2,
    purity_unscored,
    unassociated_recheck,
)


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
) -> Path:
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
            "(sample_id, scan_id, feature_id, match_key, n_features_in_window, "
            " n_peaks, precursor_only, precursor_mz, isolation_window_target, "
            " isolation_window_lower, isolation_window_upper) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                # s1: two associated, one unassociated but recoverable (555.5)
                (sid1, 1001, 1, "precursor_mz", 1, 10, 0, 500.0, 500.0, 0.5, 0.5),
                (sid1, 1002, 1, "precursor_mz", 3, 20, 0, 500.0, 500.0, 0.5, 0.5),
                (sid1, 1003, None, "precursor_mz", 0, 2, 1, 555.5, 555.5, 0.5, 0.5),
                # s2: one associated, one unassociated + unrecoverable (999.0),
                #     one unassociated with no precursor m/z at all
                (sid2, 1001, 2, "precursor_mz", 2, 8, 0, 700.0, 700.0, 0.5, 0.5),
                (sid2, 1002, None, "precursor_mz", 0, 4, 0, 999.0, 999.0, 0.5, 0.5),
                (sid2, 1003, None, "none", 0, 1, 0, None, None, None, None),
            ],
        )
        con.executemany(
            "INSERT INTO precursor_purity "
            "(sample_id, ms2_scan_id, bracket_kind, parent_ms1_scan_id, "
            " window_lo_mz, precursor_found, n_peaks_in_window, purity, "
            " precursor_confirmed, precursor_frac) "
            "VALUES (?,?,'parent_only',?,?,?,?,?,?,?)",
            [
                # s1: 1001/1002 are ASSOCIATED (feature_id=1); 2001.. are extras
                (sid1, 1001, 1, 499.5, 1, 1, 0.97, 1, 0.90),
                (sid1, 1002, 1, 499.5, 1, 3, 0.35, 1, 0.40),
                (sid1, 2001, 1, 499.5, 0, 2, None, 1, 0.05),   # on-pixel, confirmed, peak unresolved
                (sid1, 2005, 1, 499.5, 0, 2, None, 0, 0.001),  # on-pixel, NOT confirmed
                (sid1, 2002, 99, 499.5, 0, 2, None, 1, 0.05),  # parent MS1 off-pixel
                (sid1, 2003, 1, None, 0, 0, None, 0, None),    # no precursor m/z (no window)
                (sid1, 2004, None, None, 0, 0, None, 0, None), # no parent MS1
                # s2: 1001 is ASSOCIATED (feature_id=2)
                (sid2, 1001, 5, 699.5, 1, 2, 0.60, 1, 0.70),
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
    assert s.n_precursor_only == 1


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
    # every scan with a non-null precursor_frac is in the context list
    assert len(a.frac_values_all) == 6
    by_name = {p.name: p for p in a.per_sample}
    assert sorted(by_name["s1"].frac_values) == [0.4, 0.9]
    assert by_name["s1"].n_ge_cutoff == 1
    assert by_name["s2"].frac_values == [0.7]


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


def test_purity_unscored(tmp_path):
    adb, raw_map = _analysis_db(tmp_path)
    u = purity_unscored(adb, raw_map)
    assert u.n_scored == 3  # 2 in s1 + 1 in s2
    assert u.n_unresolved_confirmed == 1  # 2001: on-pixel, confirmed, peak unresolved
    assert u.n_not_confirmed == 1         # 2005: on-pixel, not confirmed
    assert u.n_off_pixel == 1             # 2002
    assert u.n_no_precursor_mz == 1       # 2003
    assert u.n_no_parent == 1             # 2004
    assert u.n_unscored == 5
    s1 = next(p for p in u.per_sample if p.name == "s1")
    assert (s1.n_scored, s1.n_off_pixel, s1.n_unresolved_confirmed) == (2, 1, 1)


def test_purity_unscored_without_pixel_map_folds_off_pixel_by_confirmed(tmp_path):
    adb, raw_map = _analysis_db(tmp_path)
    sid1, sid2 = sorted(raw_map)
    # point s1 at the raw DB that has no pixel_ms1_scans table
    u = purity_unscored(adb, raw_db_paths={sid1: raw_map[sid2]})
    # off-pixel can no longer be distinguished; 2002 was confirmed -> unresolved
    assert u.n_off_pixel == 0
    assert u.n_unresolved_confirmed == 2  # 2001 + 2002
    assert u.n_not_confirmed == 1         # 2005


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------


def _empty_assoc_purity() -> AssociatedPuritySummary:
    return AssociatedPuritySummary(0.8, 0, 0, [], [], [])


def test_figures_return_figures_and_tolerate_empty():
    assert isinstance(figure_per_sample([]), go.Figure)
    assert isinstance(figure_overlap_upset([], []), go.Figure)
    assert isinstance(figure_ms2_association_per_sample([]), go.Figure)
    assert isinstance(figure_purity(_empty_assoc_purity()), go.Figure)
    assert isinstance(figure_purity_per_sample(_empty_assoc_purity()), go.Figure)
    assert isinstance(
        figure_unassociated_recheck(RecheckSummary(10.0, 0, 0, 0, 0, [])),
        go.Figure,
    )
    assert isinstance(
        figure_purity_unscored(UnscoredSummary([], 0, 0, 0, 0, 0, 0)), go.Figure
    )


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
        "precursor_confirmed, is_chimeric, precursor_only) "
        "VALUES (?,?,?,1,?,?,?,?,?,1,1,1,3,3,5,4,?,?,?,?,?,?,?,?)"
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
                # confirmed, chimeric, prec_only
                (1, 1001, 1, 10, "COMPA0000000AA", "Alpha", 0.85, 0.9, 1, 1, 1, 1, 1, 1, 0, 0),  # noqa: E501
                (1, 1001, 1, 11, "COMPB0000000BB", "Beta", 0.55, 0.6, 2, 3, 3, 1, 1, 1, 0, 0),  # noqa: E501
                (1, 1002, 1, 10, "COMPA0000000AA", "Alpha", 0.60, 0.7, 1, 2, 2, 2, 2, 1, 0, 0),  # noqa: E501
                (2, 1001, 2, 12, "COMPC0000000CC", "Gamma", 0.72, 0.8, 1, 1, 1, 1, 1, 1, 0, 0),  # noqa: E501
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
    assert stats.unscored.n_unscored == 5
    assert stats.unscored.n_off_pixel == 1
    assert stats.associated_purity.n_associated == 3
    assert stats.associated_purity.n_ge_cutoff == 1

    d = stats.to_dict()
    ap = d["associated_purity"]
    assert ap["n_frac_values"] == 3
    assert "frac_values" not in ap and "frac_values_all" not in ap
    assert all("frac_values" not in ps for ps in ap["per_sample"])
    assert ap["pct_ge_cutoff"] == pytest.approx(33.33, abs=0.1)
    assert d["recheck"]["n_would_associate"] == 1
    assert d["unscored"]["n_unscored"] == 5
    assert d["unscored"]["n_off_pixel"] == 1
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
    assert "unscored for" in text  # the unscored-purity sentence
    assert "laser flyback" in text
    assert "feed the library search" in text  # the associated-purity sentence
    assert "precursor_frac" in text

    payload = json.loads((out / "summary.json").read_text())
    assert payload["n_features"] == 3
    assert payload["ms2"]["n_total"] == 6
    assert payload["recheck"]["assoc_ppm"] == 10.0
    assert len(payload["per_sample_ms2"]) == 2
    assert payload["unscored"]["n_unscored"] == 5
    assert payload["associated_purity"]["n_associated"] == 3


def test_build_summary_report_defaults_out_dir_to_db_parent(tmp_path):
    adb, raw_map = _analysis_db(tmp_path)
    html_path = build_summary_report(adb, raw_db_paths=raw_map)
    assert html_path.parent == adb.parent
