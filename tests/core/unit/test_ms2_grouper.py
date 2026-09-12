"""Tests for Stage A of annotation: MS2 -> feature association (the grouper).

Mock data comes from the ``ms2_grouper_mock_data`` factory fixture in
``conftest.py``; every call plants the ``single`` / ``none`` / ``chimeric`` /
``precursor_only`` / ``null_precursor`` cases with their expected answers in
``mock.planted``.
"""

import sqlite3

import numpy as np
import pytest

from msianalyzer.core.annotation.group_ms2 import (
    associate_scan,
    detect_flat_fragmentation,
    detect_precursor_only,
    group_ms2,
    persist_grouping,
    run_grouper,
    summarize_features,
)


# ---------------------------------------------------------------------------
# the three originally named edge cases
# ---------------------------------------------------------------------------


def test_ms2_associated_to_single_mz(ms2_grouper_mock_data):
    mock = ms2_grouper_mock_data(n=1000)
    case = mock.planted["single"]
    scan = mock.planted_scan("single")

    a = associate_scan(scan, mock.master_mz, assoc_ppm=mock.assoc_ppm)

    assert a.match_key == "precursor_mz"
    assert a.feature_mz == pytest.approx(case.expected_feature_mz)
    assert a.ppm_offset == pytest.approx(case.expected_ppm_offset)
    assert a.nearest_other_feature_ppm == pytest.approx(
        case.nearest_other_feature_ppm
    )


def test_ms2_not_associated_to_any_mz(ms2_grouper_mock_data):
    mock = ms2_grouper_mock_data(n=1000)
    case = mock.planted["none"]
    scan = mock.planted_scan("none")

    a = associate_scan(scan, mock.master_mz, assoc_ppm=mock.assoc_ppm)

    assert a.feature_id is None
    assert a.feature_mz is None
    assert a.ppm_offset is None
    # a precursor was present, it simply matched nothing
    assert a.match_key == "precursor_mz"
    # the nearest feature is far outside the association tolerance
    assert abs(a.nearest_other_feature_ppm) > mock.assoc_ppm


def test_multiple_mz_in_same_ms2_isolation_window_still_picks_nearest(
    ms2_grouper_mock_data,
):
    # A scan whose isolation window physically holds several aligned
    # features still resolves to a single primary match — the nearest one
    # within assoc_ppm. That window-crowding count is no longer tracked as
    # a "chimeric" signal at all (see ADR 0019); this only checks the
    # matching outcome itself still picks correctly among several
    # candidates.
    mock = ms2_grouper_mock_data(n=1000)
    case = mock.planted["chimeric"]
    scan = mock.planted_scan("chimeric")

    a = associate_scan(scan, mock.master_mz, assoc_ppm=mock.assoc_ppm)

    assert a.feature_mz == pytest.approx(case.expected_feature_mz)
    assert a.ppm_offset == pytest.approx(case.expected_ppm_offset)


# ---------------------------------------------------------------------------
# null precursor -> isolation-window-target fallback
# ---------------------------------------------------------------------------


def test_null_precursor_falls_back_to_isolation_window_target(ms2_grouper_mock_data):
    mock = ms2_grouper_mock_data(n=1000)
    case = mock.planted["null_precursor"]
    scan = mock.planted_scan("null_precursor")
    assert scan["precursor_mz"] is None

    a = associate_scan(scan, mock.master_mz, assoc_ppm=mock.assoc_ppm)

    assert a.match_key == case.expected_match_key == "isolation_window_target"
    assert a.precursor_mz is None
    assert a.feature_mz == pytest.approx(case.expected_feature_mz)
    assert a.ppm_offset == pytest.approx(case.expected_ppm_offset)
    assert a.precursor_target_delta_ppm is None


# ---------------------------------------------------------------------------
# fragmentation-failure flagging (no min_n_peaks filter)
# ---------------------------------------------------------------------------


def test_detect_precursor_only_true_for_surviving_precursor():
    mz = np.array([120.0, 200.0, 499.985])
    inten = np.array([50.0, 40.0, 9000.0])
    assert detect_precursor_only(mz, inten, 500.0) is True


def test_detect_precursor_only_false_for_real_fragmentation():
    mz = np.array([120.0, 200.0, 333.0, 480.0])
    inten = np.array([5000.0, 4000.0, 6000.0, 100.0])
    assert detect_precursor_only(mz, inten, 500.0) is False


def test_detect_precursor_only_false_when_precursor_null():
    assert detect_precursor_only(np.array([1.0]), np.array([1.0]), None) is False


# ---------------------------------------------------------------------------
# flat-fragmentation ("comb") flagging
# ---------------------------------------------------------------------------


def test_detect_flat_fragmentation_true_for_uniform_height_peaks():
    mz = np.array([120.0, 200.0, 333.0, 480.0])
    inten = np.array([1000.0, 950.0, 1020.0, 980.0])  # CV ~= 0.026
    assert detect_flat_fragmentation(mz, inten) is True


def test_detect_flat_fragmentation_false_for_decaying_real_spectrum():
    mz = np.array([120.0, 200.0, 333.0, 480.0])
    inten = np.array([10000.0, 3000.0, 800.0, 200.0])  # CV ~= 1.1
    assert detect_flat_fragmentation(mz, inten) is False


def test_detect_flat_fragmentation_false_below_min_peaks():
    # only 2 peaks survive -> too few for the CV test to be trusted
    mz = np.array([120.0, 200.0])
    inten = np.array([1000.0, 1000.0])
    assert detect_flat_fragmentation(mz, inten, min_peaks=3) is False


def test_detect_flat_fragmentation_drops_noise_floor_before_cv():
    # 4 near-uniform peaks + one baseline-noise peak far below 1% of base;
    # left in, its huge relative deviation would flip the CV above threshold
    mz = np.array([120.0, 200.0, 333.0, 480.0, 410.0])
    inten = np.array([1000.0, 950.0, 1020.0, 980.0, 1.0])
    assert detect_flat_fragmentation(mz, inten, min_rel_intensity=0.01) is True


def test_detect_flat_fragmentation_false_when_arrays_missing():
    assert detect_flat_fragmentation(None, None) is False


def test_flat_fragmentation_propagates_onto_scan_association():
    features = np.array([500.0])
    flat_scan = {
        "scan_id": 1,
        "sample_id": 1,
        "precursor_mz": 500.0,
        "mz_array": np.array([120.0, 200.0, 333.0, 480.0]),
        "intensity_array": np.array([1000.0, 950.0, 1020.0, 980.0]),
    }
    normal_scan = {
        "scan_id": 2,
        "sample_id": 1,
        "precursor_mz": 500.0,
        "mz_array": np.array([120.0, 200.0, 333.0, 480.0]),
        "intensity_array": np.array([10000.0, 3000.0, 800.0, 200.0]),
    }
    assert associate_scan(flat_scan, features, assoc_ppm=10.0).flat_fragmentation is True
    assert associate_scan(normal_scan, features, assoc_ppm=10.0).flat_fragmentation is False


def test_feature_ms2_summary_counts_flat_fragmentation():
    features = np.array([500.0])
    scans = [
        {
            "scan_id": 1, "sample_id": 1, "precursor_mz": 500.0,
            "mz_array": np.array([120.0, 200.0, 333.0, 480.0]),
            "intensity_array": np.array([1000.0, 950.0, 1020.0, 980.0]),
        },
        {
            "scan_id": 2, "sample_id": 1, "precursor_mz": 500.0,
            "mz_array": np.array([120.0, 200.0, 333.0, 480.0]),
            "intensity_array": np.array([10000.0, 3000.0, 800.0, 200.0]),
        },
    ]
    result = group_ms2(scans, features, assoc_ppm=10.0)
    fs = result.feature_summary[0]
    assert fs.n_ms2 == 2
    assert fs.n_flat_fragmentation == 1


def test_precursor_only_scan_is_flagged_but_still_associated(ms2_grouper_mock_data):
    mock = ms2_grouper_mock_data(n=1000)
    case = mock.planted["precursor_only"]

    a = associate_scan(
        mock.planted_scan("precursor_only"), mock.master_mz, assoc_ppm=mock.assoc_ppm
    )
    assert a.precursor_only is True
    assert case.expected_precursor_only is True
    assert a.feature_mz == pytest.approx(case.expected_feature_mz)  # not dropped

    # an ordinary scan is not flagged
    normal = associate_scan(
        mock.planted_scan("single"), mock.master_mz, assoc_ppm=mock.assoc_ppm
    )
    assert normal.precursor_only is False


def test_feature_ms2_summary_counts_fragmentation_failures(ms2_grouper_mock_data):
    mock = ms2_grouper_mock_data(n=1000)

    result = group_ms2(
        mock.scans,
        mock.master_mz,
        assoc_ppm=mock.assoc_ppm,
        align_ppm=mock.align_ppm,
    )
    by_feat = {s.feature_id: s for s in result.feature_summary}

    po_scan_id = mock.planted["precursor_only"].scan_id
    po_assoc = next(a for a in result.associations if a.scan_id == po_scan_id)
    fs = by_feat[po_assoc.feature_id]

    assert fs.n_precursor_only >= 1
    assert fs.n_ms2 == sum(
        1 for a in result.associations if a.feature_id == po_assoc.feature_id
    )
    # totals across the batch: every summary row is internally consistent
    assert sum(s.n_ms2 for s in result.feature_summary) == sum(
        1 for a in result.associations if a.feature_id is not None
    )


# ---------------------------------------------------------------------------
# include-unmatched toggle
# ---------------------------------------------------------------------------


def test_unmatched_scan_included_with_null_feature_when_toggle_on(ms2_grouper_mock_data):
    mock = ms2_grouper_mock_data(n=1000)
    res = group_ms2(mock.scans, mock.master_mz, assoc_ppm=mock.assoc_ppm)

    assert len(res.associations) == len(mock.scans)
    none_id = mock.planted["none"].scan_id
    unmatched = [a for a in res.associations if a.feature_id is None]
    assert [a.scan_id for a in unmatched] == [none_id]


def test_unmatched_scan_dropped_when_toggle_off(ms2_grouper_mock_data):
    mock = ms2_grouper_mock_data(n=1000)
    res = group_ms2(
        mock.scans, mock.master_mz, assoc_ppm=mock.assoc_ppm, include_unmatched=False
    )

    assert all(a.feature_id is not None for a in res.associations)
    assert len(res.associations) == len(mock.scans) - 1  # only the "none" case gone
    none_id = mock.planted["none"].scan_id
    assert all(a.scan_id != none_id for a in res.associations)


# ---------------------------------------------------------------------------
# tolerance-relationship guard
# ---------------------------------------------------------------------------


def test_assoc_ppm_below_align_ppm_warns(ms2_grouper_mock_data):
    mock = ms2_grouper_mock_data(n=1000)
    with pytest.warns(UserWarning, match="assoc_ppm"):
        group_ms2(
            mock.scans[:10], mock.master_mz, assoc_ppm=1.0, align_ppm=10.0
        )


def test_assoc_ppm_at_or_above_align_ppm_is_quiet(ms2_grouper_mock_data, recwarn):
    mock = ms2_grouper_mock_data(n=1000)
    group_ms2(mock.scans[:10], mock.master_mz, assoc_ppm=10.0, align_ppm=10.0)
    assert not [w for w in recwarn.list if issubclass(w.category, UserWarning)]


# ---------------------------------------------------------------------------
# persistence: the two-table + summary layout
# ---------------------------------------------------------------------------


def _seed_features(db_path, master_mz):
    """Minimal ``features`` / ``commands`` tables so grouper FKs resolve.

    Feature ids are 0..N-1 to line up with ``group_ms2``'s default ids.
    """
    con = sqlite3.connect(db_path)
    con.execute(
        "CREATE TABLE features (feature_id INTEGER PRIMARY KEY, mz REAL NOT NULL, "
        "members_json TEXT, command_id INTEGER)"
    )
    con.execute(
        "CREATE TABLE commands (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, "
        "sample_id INTEGER, command_name TEXT, datetime TEXT, arguments TEXT)"
    )
    con.executemany(
        "INSERT INTO features (feature_id, mz, members_json) VALUES (?, ?, '{}')",
        [(i, float(mz)) for i, mz in enumerate(master_mz)],
    )
    con.commit()
    con.close()


def test_persist_grouping_round_trip(ms2_grouper_mock_data, tmp_path):
    mock = ms2_grouper_mock_data(n=1000)
    result = group_ms2(mock.scans, mock.master_mz, assoc_ppm=mock.assoc_ppm)

    db = tmp_path / "analysis.db"
    _seed_features(db, mock.master_mz)
    persist_grouping(db, result)

    con = sqlite3.connect(db)
    try:
        n_assoc = con.execute("SELECT COUNT(*) FROM ms2_associations").fetchone()[0]
        assert n_assoc == len(result.associations)

        n_summary = con.execute(
            "SELECT COUNT(*) FROM feature_ms2_summary"
        ).fetchone()[0]
        assert n_summary == len(result.feature_summary)

        # a re-run replaces rather than appends
        persist_grouping(db, result)
        assert (
            con.execute("SELECT COUNT(*) FROM ms2_associations").fetchone()[0]
            == n_assoc
        )
    finally:
        con.close()


# ---------------------------------------------------------------------------
# end-to-end: raw ms2 DB + analysis DB -> persisted grouping
# ---------------------------------------------------------------------------


def test_run_grouper_end_to_end(ms2_grouper_mock_data, make_ms2_db, tmp_path):
    from msianalyzer.core.analysis_db import (
        init_analysis_db,
        register_sample,
        save_features,
    )

    mock = ms2_grouper_mock_data(n=1000)
    raw_db = make_ms2_db(mock, name="s0_ms2.db")

    analysis_db = tmp_path / "analysis.db"
    init_analysis_db(analysis_db).close()
    sample_id = register_sample(analysis_db, "s0", str(raw_db), "+")
    save_features(analysis_db, mock.features_df)

    result = run_grouper(
        analysis_db, assoc_ppm=mock.assoc_ppm, align_ppm=mock.align_ppm
    )
    assert len(result.associations) == len(mock.scans)

    con = sqlite3.connect(analysis_db)
    try:
        assert (
            con.execute("SELECT COUNT(*) FROM ms2_associations").fetchone()[0]
            == len(mock.scans)
        )
        # the unmatched planted scan stored with NULL feature
        none_id = mock.planted["none"].scan_id
        row = con.execute(
            "SELECT feature_id, match_key FROM ms2_associations "
            "WHERE scan_id = ? AND sample_id = ?",
            (none_id, sample_id),
        ).fetchone()
        assert row == (None, "precursor_mz")

        # the precursor-only planted scan is flagged and rolled up
        po_id = mock.planted["precursor_only"].scan_id
        feat_id, prec_only = con.execute(
            "SELECT feature_id, precursor_only FROM ms2_associations "
            "WHERE scan_id = ? AND sample_id = ?",
            (po_id, sample_id),
        ).fetchone()
        assert prec_only == 1
        assert (
            con.execute(
                "SELECT n_precursor_only FROM feature_ms2_summary WHERE feature_id = ?",
                (feat_id,),
            ).fetchone()[0]
            >= 1
        )

        # the null-precursor planted scan matched via the target
        np_id = mock.planted["null_precursor"].scan_id
        assert (
            con.execute(
                "SELECT match_key FROM ms2_associations "
                "WHERE scan_id = ? AND sample_id = ?",
                (np_id, sample_id),
            ).fetchone()[0]
            == "isolation_window_target"
        )
    finally:
        con.close()


def test_summarize_features_empty_input():
    assert summarize_features([]) == []
