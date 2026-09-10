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


def _row(scan_id, score, *, feature_id=1, sample_id=1):
    empty = np.array([], dtype=float)
    return AnnotationRow(
        sample_id=sample_id,
        scan_id=scan_id,
        feature_id=feature_id,
        library_id=1,
        library_spectrum_id=scan_id,
        compound_id=scan_id,
        compound_name="C",
        compound_formula="F",
        inchikey="K",
        score=score,
        dot_product_score=score,
        lib_coverage=1.0,
        emp_coverage=1.0,
        coverage_score=1.0,
        n_matched_peaks=3,
        n_lib_peaks=3,
        n_emp_peaks_raw=5,
        n_emp_peaks_filtered=4,
        is_chimeric=False,
        n_features_in_window=1,
        precursor_only=False,
        emp_filtered_mz=empty,
        emp_filtered_intensity=empty,
        lib_filtered_mz=empty,
        lib_filtered_intensity=empty,
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


def test_assign_feature_ranks_orders_scans_by_best_hit():
    rows = [
        _row(10, 0.4),
        _row(10, 0.9),  # scan 10 best = 0.9
        _row(20, 0.5),  # scan 20 best = 0.5
        _row(20, 0.1),
    ]
    assign_feature_ranks(rows)
    rf = {r.scan_id: r.rank_feature for r in rows}
    assert rf[10] == 1
    assert rf[20] == 2


def test_assign_feature_ranks_scopes_sample_rank_within_each_sample():
    # feature 1: scan 10 (sample 1, best 0.9), scan 20 (sample 1, best 0.4),
    #            scan 30 (sample 2, best 0.6)
    rows = [
        _row(10, 0.9, sample_id=1),
        _row(20, 0.4, sample_id=1),
        _row(30, 0.6, sample_id=2),
    ]
    assign_feature_ranks(rows)
    by_scan = {r.scan_id: r for r in rows}
    # global order: 10 (0.9) > 30 (0.6) > 20 (0.4)
    assert by_scan[10].rank_feature == 1
    assert by_scan[30].rank_feature == 2
    assert by_scan[20].rank_feature == 3
    # within sample 1: 10 then 20; sample 2 has only scan 30 -> rank 1
    assert by_scan[10].rank_feature_sample == 1
    assert by_scan[20].rank_feature_sample == 2
    assert by_scan[30].rank_feature_sample == 1


# ---------------------------------------------------------------------------
# annotate_feature
# ---------------------------------------------------------------------------


def test_annotate_feature_true_compound_outranks_decoys_and_carries_filtered_arrays():
    emp_mz = np.array([90.0, 130.0, 170.0, 210.0, 260.0])
    emp_int = np.array([200.0, 900.0, 400.0, 750.0, 120.0])
    scan = {
        "scan_id": 7,
        "sample_id": 1,
        "n_features_in_window": 1,
        "is_chimeric": False,
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
    assert top.emp_filtered_mz.size > 0
    assert top.lib_filtered_mz.size > 0
    assert top.emp_filtered_intensity.max() == pytest.approx(1.0)


def test_annotate_feature_skips_chimeric_when_disabled():
    scan = {
        "scan_id": 1,
        "sample_id": 1,
        "n_features_in_window": 3,
        "is_chimeric": True,
        "precursor_only": False,
        "emp_mz": np.array([100.0, 200.0]),
        "emp_int": np.array([1.0, 1.0]),
    }
    cand = _cand(1, [100.0, 200.0], [1.0, 1.0])
    assert (
        annotate_feature(
            1,
            150.0,
            [scan],
            [cand],
            fragment_ppm=10.0,
            noise_threshold=0.0,
            mz_power=2.0,
            int_power=0.5,
            min_matched_peaks=1,
            annotate_chimeric=False,
        )
        == []
    )


def _purity_scan(scan_id, purity, *, runner_up=None):
    return {
        "scan_id": scan_id,
        "sample_id": 1,
        "n_features_in_window": 1,
        "is_chimeric": False,
        "precursor_only": False,
        "purity": purity,
        "runner_up_rel_int": runner_up,
        "emp_mz": np.array([100.0, 200.0]),
        "emp_int": np.array([1.0, 1.0]),
    }


def _annotate_one(scans, *, min_purity=None):
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
        min_purity=min_purity,
    )


def test_annotate_feature_carries_purity_onto_rows():
    rows = _annotate_one([_purity_scan(1, 0.83, runner_up=0.2)])
    assert rows[0].purity == pytest.approx(0.83)
    assert rows[0].runner_up_rel_int == pytest.approx(0.2)


def test_annotate_feature_min_purity_skips_low_and_keeps_unscored():
    rows = _annotate_one(
        [_purity_scan(1, 0.4), _purity_scan(2, 0.9), _purity_scan(3, None)],
        min_purity=0.5,
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


def test_persist_annotations_round_trip_keeps_filtered_spectra(tmp_path):
    db = _fresh_analysis_db(tmp_path)

    r = _row(3, 0.87, feature_id=None)
    r.rank_ms2 = 1
    r.rank_feature = 1
    r.rank_feature_sample = 1
    r.emp_filtered_mz = np.array([100.1234, 150.5678])
    r.emp_filtered_intensity = np.array([1.0, 0.4])
    r.lib_filtered_mz = np.array([100.1230, 150.5680, 200.9999])
    r.lib_filtered_intensity = np.array([1.0, 0.5, 0.2])

    persist_annotations(db, [r], 1)

    with sqlite3.connect(db) as con:
        row = con.execute(
            "SELECT score, rank_ms2, rank_feature, rank_feature_sample, "
            "emp_filtered_mz, emp_filtered_intensity, "
            "lib_filtered_mz, lib_filtered_intensity FROM ms2_annotations"
        ).fetchone()
    assert row[0] == pytest.approx(0.87)
    assert (row[1], row[2], row[3]) == (1, 1, 1)
    np.testing.assert_allclose(blob_to_array(row[4]), [100.1234, 150.5678], atol=1e-3)
    np.testing.assert_allclose(blob_to_array(row[5]), [1.0, 0.4], atol=1e-3)
    assert blob_to_array(row[6]).size == 3

    # a second call replaces rather than appends
    persist_annotations(db, [r], 1)
    with sqlite3.connect(db) as con:
        n = con.execute("SELECT COUNT(*) FROM ms2_annotations").fetchone()[0]
    assert n == 1


def test_persist_annotations_can_drop_filtered_spectra(tmp_path):
    db = _fresh_analysis_db(tmp_path)
    r = _row(3, 0.5, feature_id=None)
    r.emp_filtered_mz = np.array([1.0, 2.0])
    r.lib_filtered_mz = np.array([1.0, 2.0])
    r.emp_filtered_intensity = np.array([1.0, 1.0])
    r.lib_filtered_intensity = np.array([1.0, 1.0])

    persist_annotations(db, [r], 1, store_filtered_spectra=False)

    with sqlite3.connect(db) as con:
        row = con.execute(
            "SELECT score, emp_filtered_mz, emp_filtered_intensity, "
            "lib_filtered_mz, lib_filtered_intensity FROM ms2_annotations"
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


def _seed_purity(adb, scan_id, purity, *, sample_id=1, confirmed=1, frac=0.42):
    with sqlite3.connect(adb) as con:
        con.execute("PRAGMA foreign_keys = ON")
        con.execute(
            "INSERT INTO precursor_purity (sample_id, ms2_scan_id, bracket_kind, "
            "precursor_found, n_peaks_in_window, purity, precursor_confirmed, "
            "precursor_frac) VALUES (?,?,'parent_only',1,3,?,?,?)",
            (sample_id, scan_id, purity, confirmed, frac),
        )
        con.commit()


def test_run_annotation_carries_purity_and_min_purity_filters(
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
            "SELECT purity, precursor_confirmed, precursor_frac "
            "FROM ms2_annotations WHERE scan_id = ?", (single_scan,)
        ).fetchone()
    assert row is not None
    assert row[0] == pytest.approx(0.2)
    assert row[1] == 1  # precursor_confirmed carried through
    assert row[2] == pytest.approx(0.42)

    cfg_filtered = AnnotateConfig(
        library_path=str(lib), candidate_ppm=25.0, n_workers=1, min_purity=0.5
    )
    run_annotation(adb, cfg_filtered)
    with sqlite3.connect(adb) as con:
        n = con.execute(
            "SELECT COUNT(*) FROM ms2_annotations WHERE scan_id = ?", (single_scan,)
        ).fetchone()[0]
    assert n == 0  # dropped by min_purity


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
            "emp_filtered_mz, lib_filtered_mz "
            "FROM ms2_annotations WHERE scan_id = ? AND rank_ms2 = 1",
            (single_scan_id,),
        ).fetchone()
    assert n_libs == 1
    assert top is not None
    assert top[0] == "TrueCompound"
    assert top[2] == 1  # best (only) scan on its feature
    assert top[3] == 1  # ... and the only scan in its sample
    assert blob_to_array(top[4]).size > 0
    assert blob_to_array(top[5]).size > 0


def test_run_annotation_flags_chimeric_scans(
    ms2_grouper_mock_data, make_ms2_db, make_library_db, tmp_path
):
    mock = ms2_grouper_mock_data(n=1000)
    adb = _seeded_analysis_db(tmp_path, mock, make_ms2_db)
    lib = make_library_db(mock, case="chimeric")

    cfg = AnnotateConfig(library_path=str(lib), candidate_ppm=25.0, n_workers=1)
    run_annotation(adb, cfg)

    chim_scan_id = mock.planted["chimeric"].scan_id
    with sqlite3.connect(adb) as con:
        rows = con.execute(
            "SELECT DISTINCT is_chimeric, n_features_in_window FROM ms2_annotations "
            "WHERE scan_id = ?",
            (chim_scan_id,),
        ).fetchall()
    assert rows, "the chimeric scan should still be annotated"
    assert all(r[0] == 1 and r[1] == 3 for r in rows)


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
        # the chimeric feature is only in lib_b; it is still annotated + flagged
        chim_rows = con.execute(
            "SELECT DISTINCT is_chimeric FROM ms2_annotations WHERE scan_id = ?",
            (chim_scan_id,),
        ).fetchall()
    assert single_top is not None and single_top[0] == "TrueCompound"
    assert chim_rows and all(r[0] == 1 for r in chim_rows)


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
