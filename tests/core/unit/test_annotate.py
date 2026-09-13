"""Tests for Stage B of annotation: MS2 -> spectral-library matching.

Pure helpers are exercised with hand-built `Candidate`s; the orchestrator
is exercised end-to-end against a real (tiny) libviz library built by the
``make_library_db`` factory fixture in ``conftest.py``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace

import numpy as np
import pytest

from msianalyzer.core.annotation.annotate import (
    AnnotationRow,
    Candidate,
    annotate_feature,
    assign_feature_ranks,
    normalize_library_paths,
    normalize_polarity,
    persist_annotations,
    rank_scan_rows,
    run_annotation,
    score_scan_against_candidates,
)
from msianalyzer.core.config.config import AnnotateConfig
from msianalyzer.core.parser.mzml_parser import blob_to_array


# ---------------------------------------------------------------------------
# small builders
# ---------------------------------------------------------------------------


def _cand(spectrum_id, mz, inten, *, name="C", library_id=1):
    return Candidate(
        library_id=library_id,
        spectrum_id=spectrum_id,
        compound_id=spectrum_id,
        compound_name=name,
        compound_formula="C6H12O6",
        inchikey=f"{name}-{spectrum_id}",
        mz=np.asarray(mz, dtype=float),
        intensity=np.asarray(inten, dtype=float),
    )


def _row(scan_id, score, *, feature_id=1, sample_id=1, n_matched_peaks=3, compound_name="C"):
    empty = np.array([], dtype=float)
    return AnnotationRow(
        sample_id=sample_id,
        scan_id=scan_id,
        feature_id=feature_id,
        library_id=1,
        library_spectrum_id=scan_id,
        compound_id=scan_id,
        compound_name=compound_name,
        compound_formula="F",
        inchikey="K",
        score=score,
        dot_product_score=score,
        lib_coverage=1.0,
        emp_coverage=1.0,
        coverage_score=1.0,
        n_matched_peaks=n_matched_peaks,
        n_lib_peaks=3,
        n_emp_peaks_raw=5,
        n_emp_peaks_filtered=4,
        precursor_only=False,
        flat_fragmentation=False,
        emp_raw_mz=empty,
        emp_raw_intensity=empty,
        lib_raw_mz=empty,
        lib_raw_intensity=empty,
    )


# ---------------------------------------------------------------------------
# normalize_polarity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        ("+", "POSITIVE"),
        ("pos", "POSITIVE"),
        ("Positive", "POSITIVE"),
        ("POSITIVE scan", "POSITIVE"),
        ("-", "NEGATIVE"),
        ("neg", "NEGATIVE"),
        ("Negative", "NEGATIVE"),
        (None, None),
        ("", None),
        ("weird", None),
    ],
)
def test_normalize_polarity(value, expected):
    assert normalize_polarity(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, []),
        ("", []),
        ([], []),
        ("a.db", ["a.db"]),
        (["a.db", "b.db"], ["a.db", "b.db"]),
        (["a.db", "a.db", "", "b.db"], ["a.db", "b.db"]),  # de-dup + drop blanks
        (("a.db", "b.db"), ["a.db", "b.db"]),  # tuple accepted
    ],
)
def test_normalize_library_paths(value, expected):
    assert normalize_library_paths(value) == expected


# ---------------------------------------------------------------------------
# score_scan_against_candidates
# ---------------------------------------------------------------------------


def test_score_filters_by_min_matched_peaks_and_sorts_by_score():
    emp_mz = np.array([100.0, 150.0, 200.0, 250.0])
    emp_int = np.array([1.0, 1.0, 1.0, 1.0])

    good = _cand(1, emp_mz, emp_int, name="good")  # identical -> high score
    partial = _cand(2, [100.0, 999.0, 998.0], [1.0, 1.0, 1.0], name="partial")
    miss = _cand(3, [500.0, 600.0], [1.0, 1.0], name="miss")

    scored = score_scan_against_candidates(
        emp_mz,
        emp_int,
        [miss, partial, good],
        fragment_ppm=10.0,
        noise_threshold=0.0,
        mz_power=2.0,
        int_power=0.5,
        min_matched_peaks=1,
    )
    names = [c.compound_name for c, _ in scored]
    assert names == ["good", "partial"]  # "miss" dropped, sorted by score desc
    assert scored[0][1].score >= scored[1][1].score

    strict = score_scan_against_candidates(
        emp_mz,
        emp_int,
        [good, partial, miss],
        fragment_ppm=10.0,
        noise_threshold=0.0,
        mz_power=2.0,
        int_power=0.5,
        min_matched_peaks=2,
    )
    assert [c.compound_name for c, _ in strict] == ["good"]


# ---------------------------------------------------------------------------
# ranking
# ---------------------------------------------------------------------------


def test_rank_scan_rows_orders_by_score():
    rows = [_row(1, 0.2), _row(1, 0.9), _row(1, 0.5)]
    ranked = rank_scan_rows(rows)
    assert [r.rank_ms2 for r in ranked] == [1, 2, 3]
    assert ranked[0].score == pytest.approx(0.9)


def test_assign_feature_ranks_row_level_marks_single_best_hit():
    rows = [
        _row(10, 0.4),
        _row(10, 0.9),
        _row(20, 0.5),
        _row(20, 0.1),
    ]
    assign_feature_ranks(rows)
    ordered = sorted(rows, key=lambda r: r.rank_feature)
    assert [r.score for r in ordered] == [0.9, 0.5, 0.4, 0.1]
    assert ordered[0].rank_feature == 1
    # exactly one row is rank_feature == 1
    assert sum(r.rank_feature == 1 for r in rows) == 1


def test_assign_feature_ranks_default_tolerance_zero_ignores_peak_count():
    # No representative_score_tolerance passed -> old behavior: whichever
    # row scored highest wins rank_feature == 1, regardless of how many
    # peaks it matched.
    rows = [
        _row(10, 0.93, n_matched_peaks=1, compound_name="A"),
        _row(20, 0.86, n_matched_peaks=3, compound_name="B"),
    ]
    assign_feature_ranks(rows)
    winner = next(r for r in rows if r.rank_feature == 1)
    assert winner.compound_name == "A"


def test_assign_feature_ranks_tolerance_prefers_more_matched_peaks_within_band():
    # Reproduces the real case reported against feature 76 of a run: a
    # 1-matched-peak candidate outscored a 3-matched-peak one by ~0.07.
    # With a tolerance covering that gap, the richer match should win
    # representative selection even though its raw score is lower.
    rows = [
        _row(10, 0.93, n_matched_peaks=1, compound_name="A"),
        _row(20, 0.86, n_matched_peaks=3, compound_name="B"),
    ]
    assign_feature_ranks(rows, representative_score_tolerance=0.1)
    winner = next(r for r in rows if r.rank_feature == 1)
    assert winner.compound_name == "B"
    # score itself is completely untouched by this.
    assert {r.compound_name: r.score for r in rows} == {"A": 0.93, "B": 0.86}


def test_assign_feature_ranks_tolerance_too_narrow_keeps_top_score_winner():
    # Same rows, but the gap (0.07) exceeds the tolerance (0.05) -> the
    # plain highest score still wins.
    rows = [
        _row(10, 0.93, n_matched_peaks=1, compound_name="A"),
        _row(20, 0.86, n_matched_peaks=3, compound_name="B"),
    ]
    assign_feature_ranks(rows, representative_score_tolerance=0.05)
    winner = next(r for r in rows if r.rank_feature == 1)
    assert winner.compound_name == "A"


def test_assign_feature_ranks_tolerance_only_affects_rank_one():
    # A third, even-lower-scoring candidate must still rank below both,
    # in plain score order -> the tolerance/peak-count rule only ever
    # changes which row lands at rank 1.
    rows = [
        _row(10, 0.93, n_matched_peaks=1, compound_name="A"),
        _row(20, 0.86, n_matched_peaks=3, compound_name="B"),
        _row(30, 0.50, n_matched_peaks=5, compound_name="C"),
    ]
    assign_feature_ranks(rows, representative_score_tolerance=0.1)
    ranks = {r.compound_name: r.rank_feature for r in rows}
    assert ranks == {"B": 1, "A": 2, "C": 3}


def test_assign_feature_ranks_tolerance_exact_tie_prefers_more_peaks():
    # An exact score tie, with any positive tolerance: the peak-count
    # tie-break applies among rows tied at the top score — no arbitrary
    # pick left to sort stability.
    rows = [
        _row(10, 0.9, n_matched_peaks=1, compound_name="A"),
        _row(20, 0.9, n_matched_peaks=2, compound_name="B"),
    ]
    assign_feature_ranks(rows, representative_score_tolerance=0.001)
    winner = next(r for r in rows if r.rank_feature == 1)
    assert winner.compound_name == "B"


def test_assign_feature_ranks_zero_tolerance_is_plain_score_order():
    # Zero tolerance (the documented "disable" value) skips the
    # peak-count tie-break entirely, even for an exact score tie —
    # falls back to sorted()'s stable order (original list order here).
    rows = [
        _row(10, 0.9, n_matched_peaks=1, compound_name="A"),
        _row(20, 0.9, n_matched_peaks=2, compound_name="B"),
    ]
    assign_feature_ranks(rows, representative_score_tolerance=0.0)
    winner = next(r for r in rows if r.rank_feature == 1)
    assert winner.compound_name == "A"


def test_assign_feature_ranks_scan_level_orders_scans_by_best_hit():
    rows = [
        _row(10, 0.4),
        _row(10, 0.9),  # scan 10 best = 0.9
        _row(20, 0.5),  # scan 20 best = 0.5
        _row(20, 0.1),
    ]
    assign_feature_ranks(rows)
    rsf = {r.scan_id: r.rank_scan_feature for r in rows}
    assert rsf[10] == 1
    assert rsf[20] == 2
    # broadcast onto every row of the scan
    assert [r.rank_scan_feature for r in rows if r.scan_id == 10] == [1, 1]


def test_assign_feature_ranks_sample_scoping():
    # feature 1, scans: 10 (s1: rows 0.9, 0.3), 20 (s1: row 0.4),
    #                   30 (s2: rows 0.6, 0.5)
    rows = [
        _row(10, 0.9, sample_id=1),
        _row(10, 0.3, sample_id=1),
        _row(20, 0.4, sample_id=1),
        _row(30, 0.6, sample_id=2),
        _row(30, 0.5, sample_id=2),
    ]
    assign_feature_ranks(rows)
    by_key = {(r.scan_id, r.score): r for r in rows}

    # row-level, all samples: 0.9 > 0.6 > 0.5 > 0.4 > 0.3
    assert by_key[(10, 0.9)].rank_feature == 1
    assert by_key[(30, 0.6)].rank_feature == 2
    assert by_key[(30, 0.5)].rank_feature == 3
    assert by_key[(20, 0.4)].rank_feature == 4
    assert by_key[(10, 0.3)].rank_feature == 5

    # row-level, per sample
    assert by_key[(10, 0.9)].rank_feature_sample == 1  # s1
    assert by_key[(20, 0.4)].rank_feature_sample == 2  # s1
    assert by_key[(10, 0.3)].rank_feature_sample == 3  # s1
    assert by_key[(30, 0.6)].rank_feature_sample == 1  # s2
    assert by_key[(30, 0.5)].rank_feature_sample == 2  # s2

    # scan-level, all samples: scan 10 (0.9) > 30 (0.6) > 20 (0.4)
    assert by_key[(10, 0.9)].rank_scan_feature == 1
    assert by_key[(30, 0.6)].rank_scan_feature == 2
    assert by_key[(20, 0.4)].rank_scan_feature == 3

    # scan-level, per sample: s1 -> 10 then 20; s2 -> 30 alone
    assert by_key[(10, 0.9)].rank_scan_feature_sample == 1
    assert by_key[(20, 0.4)].rank_scan_feature_sample == 2
    assert by_key[(30, 0.6)].rank_scan_feature_sample == 1


# ---------------------------------------------------------------------------
# annotate_feature
# ---------------------------------------------------------------------------


def test_annotate_feature_true_compound_outranks_decoys_and_carries_raw_arrays():
    emp_mz = np.array([90.0, 130.0, 170.0, 210.0, 260.0])
    emp_int = np.array([200.0, 900.0, 400.0, 750.0, 120.0])
    scan = {
        "scan_id": 7,
        "sample_id": 1,
        "precursor_only": False,
        "emp_mz": emp_mz,
        "emp_int": emp_int,
    }
    true_c = _cand(1, emp_mz * (1 + 1e-6), emp_int, name="true")
    decoy1 = _cand(2, [51.0, 63.0, 77.0], [1.0, 1.0, 1.0], name="d1")
    decoy2 = _cand(3, [300.0, 340.0], [1.0, 1.0], name="d2")

    rows = annotate_feature(
        5,
        170.0,
        [scan],
        [decoy1, true_c, decoy2],
        fragment_ppm=10.0,
        noise_threshold=0.01,
        mz_power=2.0,
        int_power=0.5,
        min_matched_peaks=1,
    )

    assert rows, "true compound shares peaks -> at least one row"
    top = min(rows, key=lambda r: r.rank_ms2)
    assert top.rank_ms2 == 1
    assert top.compound_name == "true"
    assert top.feature_id == 5
    # emp_raw_*/lib_raw_* are the untouched spectra — this scan's own
    # emp_mz/emp_int, and true_c's own mz/intensity, neither noise-filtered
    # nor normalised.
    assert top.emp_raw_mz.size > 0
    assert top.lib_raw_mz.size > 0
    np.testing.assert_allclose(top.emp_raw_mz, emp_mz)
    np.testing.assert_allclose(top.emp_raw_intensity, emp_int)
    np.testing.assert_allclose(top.lib_raw_mz, true_c.mz)
    np.testing.assert_allclose(top.lib_raw_intensity, true_c.intensity)


def test_annotate_feature_scores_every_scan_regardless_of_window_crowding():
    # There is no more "chimeric" gate keyed on isolation-window feature
    # density (see ADR 0019) — every scan is scored unconditionally.
    scan = {
        "scan_id": 1,
        "sample_id": 1,
        "precursor_only": False,
        "emp_mz": np.array([100.0, 200.0]),
        "emp_int": np.array([1.0, 1.0]),
    }
    cand = _cand(1, [100.0, 200.0], [1.0, 1.0])
    rows = annotate_feature(
        1,
        150.0,
        [scan],
        [cand],
        fragment_ppm=10.0,
        noise_threshold=0.0,
        mz_power=2.0,
        int_power=0.5,
        min_matched_peaks=1,
    )
    assert len(rows) == 1


def _purity_scan(scan_id, precursor_frac):
    return {
        "scan_id": scan_id,
        "sample_id": 1,
        "precursor_only": False,
        "precursor_frac": precursor_frac,
        "emp_mz": np.array([100.0, 200.0]),
        "emp_int": np.array([1.0, 1.0]),
    }


def _annotate_one(scans, *, min_precursor_frac=None):
    return annotate_feature(
        1,
        150.0,
        scans,
        [_cand(1, [100.0, 200.0], [1.0, 1.0])],
        fragment_ppm=10.0,
        noise_threshold=0.0,
        mz_power=2.0,
        int_power=0.5,
        min_matched_peaks=1,
        min_precursor_frac=min_precursor_frac,
    )


def test_annotate_feature_carries_precursor_frac_onto_rows():
    rows = _annotate_one([_purity_scan(1, 0.83)])
    assert rows[0].precursor_frac == pytest.approx(0.83)


def test_annotate_feature_min_precursor_frac_skips_low_and_keeps_unscored():
    rows = _annotate_one(
        [_purity_scan(1, 0.4), _purity_scan(2, 0.9), _purity_scan(3, None)],
        min_precursor_frac=0.5,
    )
    kept = {r.scan_id for r in rows}
    assert kept == {2, 3}  # 0.4 dropped; None (unscored) kept


# ---------------------------------------------------------------------------
# persist_annotations
# ---------------------------------------------------------------------------


def _fresh_analysis_db(tmp_path):
    from msianalyzer.core.analysis_db import init_analysis_db

    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO annotation_libraries (id, path, name) VALUES (1, '/x.db', 'x')"
        )
        con.commit()
    return db


def test_persist_annotations_round_trip_keeps_raw_spectra(tmp_path):
    db = _fresh_analysis_db(tmp_path)

    r = _row(3, 0.87, feature_id=None)
    r.rank_ms2 = 1
    r.rank_feature = 1
    r.rank_feature_sample = 1
    r.rank_scan_feature = 1
    r.rank_scan_feature_sample = 1
    r.emp_raw_mz = np.array([100.1234, 150.5678])
    r.emp_raw_intensity = np.array([0.6, 0.4])
    # The untouched library candidate spectrum — a different array than
    # emp_raw_mz/intensity above, so the round trip can't accidentally pass
    # by mixing the two up.
    r.lib_raw_mz = np.array([90.0, 100.1230, 150.5680, 200.9999, 250.0])
    r.lib_raw_intensity = np.array([0.1, 1.0, 0.5, 0.2, 0.05])

    persist_annotations(db, [r], 1)

    with sqlite3.connect(db) as con:
        row = con.execute(
            "SELECT score, rank_ms2, rank_feature, rank_feature_sample, "
            "rank_scan_feature, rank_scan_feature_sample, "
            "emp_raw_mz, emp_raw_intensity, "
            "lib_raw_mz, lib_raw_intensity FROM ms2_annotations"
        ).fetchone()
    assert row[0] == pytest.approx(0.87)
    assert (row[1], row[2], row[3], row[4], row[5]) == (1, 1, 1, 1, 1)
    np.testing.assert_allclose(blob_to_array(row[6]), [100.1234, 150.5678], atol=1e-3)
    np.testing.assert_allclose(blob_to_array(row[7]), [0.6, 0.4], atol=1e-3)
    assert blob_to_array(row[8]).size == 5
    np.testing.assert_allclose(
        blob_to_array(row[8]), [90.0, 100.1230, 150.5680, 200.9999, 250.0], atol=1e-3
    )

    # a second call replaces rather than appends
    persist_annotations(db, [r], 1)
    with sqlite3.connect(db) as con:
        n = con.execute("SELECT COUNT(*) FROM ms2_annotations").fetchone()[0]
    assert n == 1


def test_persist_annotations_can_drop_raw_spectra(tmp_path):
    db = _fresh_analysis_db(tmp_path)
    r = _row(3, 0.5, feature_id=None)
    r.emp_raw_mz = np.array([1.0, 2.0])
    r.emp_raw_intensity = np.array([1.0, 1.0])
    r.lib_raw_mz = np.array([1.0, 2.0])
    r.lib_raw_intensity = np.array([1.0, 1.0])

    persist_annotations(db, [r], 1, store_raw_spectra=False)

    with sqlite3.connect(db) as con:
        row = con.execute(
            "SELECT score, emp_raw_mz, emp_raw_intensity, "
            "lib_raw_mz, lib_raw_intensity FROM ms2_annotations"
        ).fetchone()
    assert row[0] == pytest.approx(0.5)
    assert row[1] is None and row[2] is None
    assert row[3] is None and row[4] is None


# ---------------------------------------------------------------------------
# run_annotation end-to-end
# ---------------------------------------------------------------------------


def _seeded_analysis_db(tmp_path, mock, make_ms2_db):
    from msianalyzer.core.analysis_db import (
        init_analysis_db,
        register_sample,
        save_features,
    )
    from msianalyzer.core.annotation.group_ms2 import run_grouper

    raw_db = make_ms2_db(mock, name="s0_ms2.db")
    adb = tmp_path / "analysis.db"
    init_analysis_db(adb).close()
    register_sample(adb, "s0", str(raw_db), "+")
    save_features(adb, mock.features_df)
    run_grouper(adb, assoc_ppm=mock.assoc_ppm, align_ppm=mock.align_ppm)
    return adb


def _seed_purity(adb, scan_id, precursor_frac, *, sample_id=1, confirmed=1):
    with sqlite3.connect(adb) as con:
        con.execute("PRAGMA foreign_keys = ON")
        con.execute(
            "INSERT INTO precursor_purity (sample_id, ms2_scan_id, "
            "precursor_confirmed, precursor_frac) VALUES (?,?,?,?)",
            (sample_id, scan_id, confirmed, precursor_frac),
        )
        con.commit()


def test_run_annotation_carries_precursor_frac_and_min_precursor_frac_filters(
    ms2_grouper_mock_data, make_ms2_db, make_library_db, tmp_path
):
    mock = ms2_grouper_mock_data(n=1000)
    adb = _seeded_analysis_db(tmp_path, mock, make_ms2_db)
    lib = make_library_db(mock, case="single")
    single_scan = mock.planted["single"].scan_id
    _seed_purity(adb, single_scan, 0.2)

    cfg = AnnotateConfig(library_path=str(lib), candidate_ppm=25.0, n_workers=1)
    run_annotation(adb, cfg)
    with sqlite3.connect(adb) as con:
        row = con.execute(
            "SELECT precursor_confirmed, precursor_frac "
            "FROM ms2_annotations WHERE scan_id = ?", (single_scan,)
        ).fetchone()
    assert row is not None
    assert row[0] == 1  # precursor_confirmed carried through
    assert row[1] == pytest.approx(0.2)

    cfg_filtered = AnnotateConfig(
        library_path=str(lib), candidate_ppm=25.0, n_workers=1,
        min_precursor_frac=0.5,
    )
    run_annotation(adb, cfg_filtered)
    with sqlite3.connect(adb) as con:
        n = con.execute(
            "SELECT COUNT(*) FROM ms2_annotations WHERE scan_id = ?", (single_scan,)
        ).fetchone()[0]
    assert n == 0  # dropped by min_precursor_frac


def test_run_annotation_end_to_end_ranks_true_compound(
    ms2_grouper_mock_data, make_ms2_db, make_library_db, tmp_path
):
    mock = ms2_grouper_mock_data(n=1000)
    adb = _seeded_analysis_db(tmp_path, mock, make_ms2_db)
    lib = make_library_db(mock, case="single")

    cfg = AnnotateConfig(library_path=str(lib), candidate_ppm=25.0, n_workers=1)
    result = run_annotation(adb, cfg)

    assert result.libraries and len(result.libraries) == 1
    assert result.libraries[0].n_spectra >= 1
    assert result.n_scans_annotated >= 1

    single_scan_id = mock.planted["single"].scan_id
    with sqlite3.connect(adb) as con:
        n_libs = con.execute("SELECT COUNT(*) FROM annotation_libraries").fetchone()[0]
        top = con.execute(
            "SELECT compound_name, rank_ms2, rank_feature, rank_feature_sample, "
            "rank_scan_feature, rank_scan_feature_sample, "
            "emp_raw_mz, lib_raw_mz "
            "FROM ms2_annotations WHERE scan_id = ? AND rank_ms2 = 1",
            (single_scan_id,),
        ).fetchone()
    assert n_libs == 1
    assert top is not None
    assert top[0] == "TrueCompound"
    # its single best scan, single best row, only scan in its sample
    assert (top[2], top[3], top[4], top[5]) == (1, 1, 1, 1)
    assert blob_to_array(top[6]).size > 0
    assert blob_to_array(top[7]).size > 0


def test_run_annotation_scores_scans_whose_window_holds_several_features(
    ms2_grouper_mock_data, make_ms2_db, make_library_db, tmp_path
):
    # A scan whose isolation window physically holds several aligned
    # features (the "chimeric" mock case) is still scored and stored —
    # there is no more window-crowding gate (see ADR 0019).
    mock = ms2_grouper_mock_data(n=1000)
    adb = _seeded_analysis_db(tmp_path, mock, make_ms2_db)
    lib = make_library_db(mock, case="chimeric")

    cfg = AnnotateConfig(library_path=str(lib), candidate_ppm=25.0, n_workers=1)
    run_annotation(adb, cfg)

    chim_scan_id = mock.planted["chimeric"].scan_id
    with sqlite3.connect(adb) as con:
        n = con.execute(
            "SELECT COUNT(*) FROM ms2_annotations WHERE scan_id = ?",
            (chim_scan_id,),
        ).fetchone()[0]
    assert n > 0, "the scan should still be annotated"


def test_run_annotation_no_library_is_a_noop(
    ms2_grouper_mock_data, make_ms2_db, tmp_path
):
    mock = ms2_grouper_mock_data(n=1000)
    adb = _seeded_analysis_db(tmp_path, mock, make_ms2_db)

    result = run_annotation(adb, AnnotateConfig(library_path=None))

    assert result.rows == []
    assert result.libraries == []
    with sqlite3.connect(adb) as con:
        assert con.execute("SELECT COUNT(*) FROM ms2_annotations").fetchone()[0] == 0
        assert (
            con.execute("SELECT COUNT(*) FROM annotation_libraries").fetchone()[0] == 0
        )


def test_run_annotation_empty_list_is_a_noop(
    ms2_grouper_mock_data, make_ms2_db, tmp_path
):
    mock = ms2_grouper_mock_data(n=1000)
    adb = _seeded_analysis_db(tmp_path, mock, make_ms2_db)

    result = run_annotation(adb, AnnotateConfig(library_path=[]))

    assert result.rows == []
    assert result.libraries == []


def test_run_annotation_multiple_libraries_pooled_and_registered(
    ms2_grouper_mock_data, make_ms2_db, make_library_db, tmp_path
):
    mock = ms2_grouper_mock_data(n=1000)
    adb = _seeded_analysis_db(tmp_path, mock, make_ms2_db)
    lib_a = make_library_db(mock, name="lib_a.db", case="single")
    lib_b = make_library_db(mock, name="lib_b.db", case="chimeric")

    cfg = AnnotateConfig(
        library_path=[str(lib_a), str(lib_b)], candidate_ppm=25.0, n_workers=1
    )
    result = run_annotation(adb, cfg)

    assert len(result.libraries) == 2
    lib_ids = {li.library_id for li in result.libraries}

    single_scan_id = mock.planted["single"].scan_id
    chim_scan_id = mock.planted["chimeric"].scan_id
    with sqlite3.connect(adb) as con:
        assert (
            con.execute("SELECT COUNT(*) FROM annotation_libraries").fetchone()[0] == 2
        )
        # every annotation row points at one of the two registered libraries
        row_lib_ids = {
            r[0]
            for r in con.execute(
                "SELECT DISTINCT library_id FROM ms2_annotations"
            ).fetchall()
        }
        assert row_lib_ids <= lib_ids
        # the "single" feature is only in lib_a; its rank-1 hit is the true cpd
        single_top = con.execute(
            "SELECT compound_name FROM ms2_annotations "
            "WHERE scan_id = ? AND rank_ms2 = 1",
            (single_scan_id,),
        ).fetchone()
        # the chimeric feature is only in lib_b; it is still annotated
        chim_n = con.execute(
            "SELECT COUNT(*) FROM ms2_annotations WHERE scan_id = ?",
            (chim_scan_id,),
        ).fetchone()[0]
    assert single_top is not None and single_top[0] == "TrueCompound"
    assert chim_n > 0


def test_run_annotation_re_run_replaces_all_configured_libraries(
    ms2_grouper_mock_data, make_ms2_db, make_library_db, tmp_path
):
    mock = ms2_grouper_mock_data(n=1000)
    adb = _seeded_analysis_db(tmp_path, mock, make_ms2_db)
    lib = make_library_db(mock, case="single")
    cfg = AnnotateConfig(library_path=[str(lib)], candidate_ppm=25.0, n_workers=1)

    run_annotation(adb, cfg)
    with sqlite3.connect(adb) as con:
        first = con.execute("SELECT COUNT(*) FROM ms2_annotations").fetchone()[0]

    run_annotation(adb, cfg)
    with sqlite3.connect(adb) as con:
        second = con.execute("SELECT COUNT(*) FROM ms2_annotations").fetchone()[0]
        n_libs = con.execute("SELECT COUNT(*) FROM annotation_libraries").fetchone()[0]

    assert first > 0
    assert second == first  # replaced, not appended
    assert n_libs == 1  # library row upserted, not duplicated
