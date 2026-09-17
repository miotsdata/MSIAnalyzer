"""Tests for :mod:`msianalyzer.core.annotation.precursor_purity`.

Split into two layers:

* pure array maths — ``window_bounds`` / ``integrate_precursor_fraction`` /
  ``snap_precursor_mz`` (no I/O);
* the ``run_precursor_purity`` orchestrator, and its building blocks
  (``SampleScanIndex`` / ``resolve_parent`` / ``compute_scan_purity``)
  against an in-memory-ish raw SQLite DB.

The peak-picking-based ``purity``/``n_peaks_in_window``/``runner_up_rel_int``
(and the parent+next-MS1 raster interpolation that fed them) were retired —
see ADR 0019 — so this file no longer covers them.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pytest

from msianalyzer.core.annotation.precursor_purity import (
    ResolvedParent,
    SampleScanIndex,
    compute_scan_purity,
    integrate_precursor_fraction,
    persist_purity,
    resolve_parent,
    run_precursor_purity,
    snap_precursor_mz,
    window_bounds,
)
from msianalyzer.core.analysis_db import (
    init_analysis_db,
    log_command,
    register_sample,
)
from msianalyzer.core.config.config import PurityConfig


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------


def _profile(
    peaks: list[tuple[float, float]],
    mz_lo: float,
    mz_hi: float,
    *,
    step: float = 0.001,
    sigma: float = 0.006,
) -> tuple[np.ndarray, np.ndarray]:
    """A profile trace: sum of Gaussians on a regular m/z grid."""
    grid = np.arange(mz_lo, mz_hi, step)
    y = np.zeros_like(grid)
    for mz, amp in peaks:
        y = y + amp * np.exp(-0.5 * ((grid - mz) / sigma) ** 2)
    return grid, y


def _build_raw_db(
    path: Path,
    ms1_rows: list[dict],
    ms2_rows: list[dict],
) -> Path:
    """Write a raw-schema SQLite DB with MS1/MS2 scans."""
    from msianalyzer.core.parser.mzml_parser import array_to_blob, init_raw_db

    con = init_raw_db(path)
    try:
        for s in ms1_rows:
            mz = np.asarray(s["mz"], dtype=float)
            it = np.asarray(s["inten"], dtype=float)
            con.execute(
                "INSERT INTO ms1_scans (scan_id, rt, n_peaks, mz_min, mz_max, "
                "tic, polarity, mz_array, intensity_array) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    s["scan_id"],
                    s["rt"],
                    int(mz.size),
                    float(mz.min()) if mz.size else 0.0,
                    float(mz.max()) if mz.size else 0.0,
                    float(it.sum()),
                    s.get("polarity", "+"),
                    array_to_blob(mz),
                    array_to_blob(it),
                ),
            )
        for s in ms2_rows:
            fmz = np.asarray(s.get("frag_mz", [80.0, 120.0]), dtype=float)
            fit = np.asarray(s.get("frag_inten", [10.0, 20.0]), dtype=float)
            con.execute(
                "INSERT INTO ms2_scans (scan_id, parent_scan_id, polarity, rt, "
                "filter_string, precursor_mz, precursor_charge, "
                "precursor_intensity, isolation_window_target, "
                "isolation_window_lower, isolation_window_upper, "
                "collision_energy, n_peaks, tic, mz_array, intensity_array) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    s["scan_id"],
                    s.get("parent_scan_id"),
                    s.get("polarity", "+"),
                    s["rt"],
                    s.get("filter_string", "ms2"),
                    s.get("precursor_mz"),
                    s.get("precursor_charge", 1),
                    s.get("precursor_intensity"),
                    s.get("isolation_window_target"),
                    s.get("isolation_window_lower"),
                    s.get("isolation_window_upper"),
                    s.get("collision_energy", 25.0),
                    int(fmz.size),
                    float(fit.sum()),
                    array_to_blob(fmz),
                    array_to_blob(fit),
                ),
            )
        con.commit()
    finally:
        con.close()
    return path


def _ms1_scan(scan_id: int, rt: float, center: float, peaks, **kw) -> dict:
    mz, inten = _profile(peaks, center - 3.0, center + 3.0)
    return {"scan_id": scan_id, "rt": rt, "mz": mz, "inten": inten, **kw}


# ===========================================================================
# window_bounds
# ===========================================================================


def test_window_bounds_uses_offsets():
    lo, hi = window_bounds(500.0, 0.4, 0.6, default_half=1.0)
    assert (lo, hi) == (pytest.approx(499.6), pytest.approx(500.6))


def test_window_bounds_falls_back_to_default_half_window():
    assert window_bounds(500.0, None, None, 0.5) == (
        pytest.approx(499.5),
        pytest.approx(500.5),
    )
    # non-positive / NaN offsets are treated as missing too
    assert window_bounds(500.0, 0.0, float("nan"), 0.7) == (
        pytest.approx(499.3),
        pytest.approx(500.7),
    )


def test_window_bounds_none_target_raises():
    with pytest.raises(ValueError):
        window_bounds(None, 0.5, 0.5, 0.5)


# ===========================================================================
# integrate_precursor_fraction  (peak-detection-free confirmation)
# ===========================================================================


def test_integrate_precursor_fraction_clean_precursor_near_one():
    mz, inten = _profile([(500.0, 100.0)], 499.0, 501.0)
    frac = integrate_precursor_fraction(mz, inten, 499.0, 501.0, 500.0, band_ppm=30.0)
    assert frac > 0.8


def test_integrate_precursor_fraction_minor_precursor_is_small():
    # precursor 500.0 is 5% of a dominant co-isolant at 500.4
    mz, inten = _profile([(500.0, 5.0), (500.4, 95.0)], 499.0, 501.0)
    frac = integrate_precursor_fraction(mz, inten, 499.0, 501.0, 500.0, band_ppm=30.0)
    assert frac < 0.15


def test_integrate_precursor_fraction_no_signal_returns_zero():
    mz, inten = _profile([(500.0, 100.0)], 499.0, 501.0)
    # window far from any peak
    assert integrate_precursor_fraction(mz, inten, 480.0, 481.0, 480.5, band_ppm=30.0) == 0.0


def test_integrate_precursor_fraction_none_ref_returns_zero():
    mz, inten = _profile([(500.0, 100.0)], 499.0, 501.0)
    assert integrate_precursor_fraction(mz, inten, 499.0, 501.0, None, band_ppm=30.0) == 0.0


# ===========================================================================
# snap_precursor_mz
# ===========================================================================


def test_snap_precursor_mz_moves_to_nearby_apex():
    mz, inten = _profile([(500.010, 100.0)], 499.0, 501.0)
    ref = 500.010 * (1 + 8e-6)  # 8 ppm off the true apex
    snapped, shift = snap_precursor_mz(mz, inten, ref, snap_ppm=15.0)
    assert abs(snapped - 500.010) < 500.010 * 3e-6
    assert abs(shift) == pytest.approx(8.0, abs=1.0)


def test_snap_precursor_mz_noop_when_already_on_peak():
    mz, inten = _profile([(500.0, 100.0)], 499.0, 501.0)
    snapped, shift = snap_precursor_mz(mz, inten, 500.0, snap_ppm=15.0)
    assert snapped == pytest.approx(500.0, abs=1e-3)
    assert abs(shift) < 2.0


def test_snap_precursor_mz_noop_when_disabled():
    mz, inten = _profile([(500.010, 100.0)], 499.0, 501.0)
    assert snap_precursor_mz(mz, inten, 500.005, snap_ppm=0.0) == (500.005, 0.0)


def test_snap_precursor_mz_noop_when_no_local_max_in_band():
    mz, inten = _profile([(500.0, 100.0)], 499.0, 501.0)
    ref = 500.0 * (1 + 200e-6)  # 200 ppm away, nothing within snap_ppm
    snapped, shift = snap_precursor_mz(mz, inten, ref, snap_ppm=15.0)
    assert snapped == pytest.approx(ref)
    assert shift == 0.0


def test_snap_precursor_mz_none_ref():
    assert snap_precursor_mz(np.array([]), np.array([]), None, snap_ppm=15.0) == (None, 0.0)


# ===========================================================================
# SampleScanIndex / resolve_parent
# ===========================================================================

_MS2_SEL = (
    "SELECT scan_id, parent_scan_id, rt, polarity, precursor_mz, "
    "isolation_window_target, isolation_window_lower, isolation_window_upper "
    "FROM ms2_scans"
)


def _ms2_dict(row) -> dict:
    return {
        "sample_id": 1,
        "scan_id": row[0],
        "parent_scan_id": row[1],
        "rt": row[2],
        "polarity": row[3],
        "precursor_mz": row[4],
        "isolation_window_target": row[5],
        "isolation_window_lower": row[6],
        "isolation_window_upper": row[7],
    }


def test_sample_scan_index_in_memory_lookups(tmp_path):
    db = _build_raw_db(
        tmp_path / "idx.db",
        [
            _ms1_scan(1, 5.0, 500.0, [(500.0, 100.0)]),
            _ms1_scan(2, 15.0, 500.0, [(500.0, 100.0)]),
            _ms1_scan(3, 25.0, 500.0, [(500.0, 100.0)]),
        ],
        [],
    )
    with sqlite3.connect(db) as con:
        idx = SampleScanIndex(con, array_cache_size=2)

        assert idx.rt_of(2) == pytest.approx(15.0)
        assert idx.parent_before(16.0, "+") == 2
        assert idx.parent_before(4.0, "+") is None

        # arrays load + LRU eviction (cache size 2)
        assert idx.arrays(1) is not None
        idx.arrays(2)
        idx.arrays(3)
        assert 1 not in idx._arrays  # evicted
        assert idx.arrays(999) is None


def test_resolve_parent_uses_parent_scan_id(tmp_path):
    db = _build_raw_db(
        tmp_path / "p.db",
        [_ms1_scan(1, 5.0, 500.0, [(500.0, 100.0)])],
        [{"scan_id": 100, "parent_scan_id": 1, "rt": 6.0, "precursor_mz": 500.0}],
    )
    with sqlite3.connect(db) as con:
        row = con.execute(_MS2_SEL).fetchone()
        res = resolve_parent(con, _ms2_dict(row))
    assert res.parent_scan_id == 1
    assert res.parent_mz is not None


def test_resolve_parent_falls_back_when_parent_scan_id_null(tmp_path):
    db = _build_raw_db(
        tmp_path / "f.db",
        [
            _ms1_scan(1, 3.0, 500.0, [(500.0, 100.0)]),
            _ms1_scan(2, 9.0, 500.0, [(500.0, 100.0)]),
        ],
        [{"scan_id": 100, "parent_scan_id": None, "rt": 6.0, "precursor_mz": 500.0}],
    )
    with sqlite3.connect(db) as con:
        row = con.execute(_MS2_SEL).fetchone()
        res = resolve_parent(con, _ms2_dict(row))
    assert res.parent_scan_id == 1  # latest MS1 at or before rt 6.0


def test_resolve_parent_none_when_no_ms1_before(tmp_path):
    db = _build_raw_db(
        tmp_path / "np.db",
        [_ms1_scan(1, 10.0, 500.0, [(500.0, 100.0)])],
        [{"scan_id": 100, "parent_scan_id": None, "rt": 6.0, "precursor_mz": 500.0}],
    )
    with sqlite3.connect(db) as con:
        row = con.execute(_MS2_SEL).fetchone()
        res = resolve_parent(con, _ms2_dict(row))
    assert res.parent_scan_id is None
    assert res.parent_mz is None


# ===========================================================================
# compute_scan_purity
# ===========================================================================


def _resolved(parent_peaks, *, center=500.0):
    p_mz, p_int = _profile(parent_peaks, center - 3.0, center + 3.0)
    return ResolvedParent(
        ms2_scan_id=1,
        parent_scan_id=10,
        parent_mz=p_mz,
        parent_inten=p_int,
    )


_SCAN = {
    "sample_id": 1,
    "scan_id": 1,
    "rt": 5.0,
    "precursor_mz": 500.0,
    "isolation_window_target": 500.0,
    "isolation_window_lower": 0.5,
    "isolation_window_upper": 0.5,
}

_KW = dict(default_half_width=0.5, confirm_ppm=25.0, confirm_min_frac=0.01, snap_ppm=15.0)


def test_compute_scan_purity_clean_scan_high_frac():
    row = compute_scan_purity(_SCAN, _resolved([(500.0, 1.0e5)]), **_KW)
    assert row.precursor_confirmed is True
    assert row.purity_score > 0.9


def test_compute_scan_purity_chimeric_scan_low_frac():
    row = compute_scan_purity(
        _SCAN, _resolved([(500.0, 2.0e4), (500.25, 8.0e4)]), **_KW
    )
    assert row.purity_score < 0.5


def test_compute_scan_purity_no_parent_returns_empty_row():
    resolved = ResolvedParent(
        ms2_scan_id=1, parent_scan_id=None, parent_mz=None, parent_inten=None,
    )
    row = compute_scan_purity(_SCAN, resolved, **_KW)
    assert row.precursor_confirmed is None
    assert row.purity_score is None
    assert row.precursor_mz_snapped is None


def test_compute_scan_purity_confirms_and_snaps_minor_coisolate():
    # a matrix-dominated window: precursor is a minor co-isolate, but the
    # signal is plainly there -> confirmed, and snappable
    scan = dict(_SCAN, precursor_mz=500.0 * (1 + 6e-6))  # 6 ppm off the real apex
    resolved = _resolved([(500.0, 2.0e4), (500.45, 9.0e5)])
    row = compute_scan_purity(scan, resolved, **_KW)
    assert row.precursor_confirmed is True       # the ion is there
    assert 0.0 < row.purity_score < 0.2        # minor co-isolate
    assert abs(row.snap_shift_ppm) == pytest.approx(6.0, abs=1.5)


# ===========================================================================
# run_precursor_purity (orchestrator)
# ===========================================================================


def _analysis_db_with_sample(tmp_path, raw_db: Path) -> Path:
    adb = tmp_path / "analysis.db"
    init_analysis_db(adb).close()
    register_sample(adb, name=raw_db.stem, raw_db_path=raw_db)
    return adb


def _run_raw_db(tmp_path, name: str = "sample.db") -> Path:
    """One sample: a clean MS2 and a chimeric MS2."""
    ms1 = [
        _ms1_scan(1, 5.0, 500.0, [(500.0, 1.0e5)]),
        _ms1_scan(2, 15.0, 700.0, [(700.0, 2.0e4), (700.25, 8.0e4)]),
    ]
    ms2 = [
        {
            "scan_id": 100, "parent_scan_id": 1, "rt": 6.0,
            "precursor_mz": 500.0, "isolation_window_target": 500.0,
            "isolation_window_lower": 0.5, "isolation_window_upper": 0.5,
        },
        {
            "scan_id": 101, "parent_scan_id": 2, "rt": 16.0,
            "precursor_mz": 700.0, "isolation_window_target": 700.0,
            "isolation_window_lower": 0.5, "isolation_window_upper": 0.5,
        },
    ]
    return _build_raw_db(tmp_path / name, ms1, ms2)


def test_run_precursor_purity_persists_rows_and_returns_result(tmp_path):
    raw = _run_raw_db(tmp_path)
    adb = _analysis_db_with_sample(tmp_path, raw)

    result = run_precursor_purity(adb, PurityConfig(), command_id=None)

    assert result.n_scans == 2

    with sqlite3.connect(adb) as con:
        rows = {
            r[0]: r
            for r in con.execute(
                "SELECT ms2_scan_id, precursor_confirmed, purity_score, "
                "precursor_mz_snapped FROM precursor_purity"
            ).fetchall()
        }
    assert set(rows) == {100, 101}
    assert rows[100][1] == 1 and rows[100][2] > 0.5  # clean
    assert rows[101][1] == 1 and rows[101][2] < 0.5  # chimeric
    assert result.n_confirmed == 2


def test_run_precursor_purity_is_idempotent(tmp_path):
    raw = _run_raw_db(tmp_path)
    adb = _analysis_db_with_sample(tmp_path, raw)

    run_precursor_purity(adb, PurityConfig())
    run_precursor_purity(adb, PurityConfig())

    with sqlite3.connect(adb) as con:
        (n,) = con.execute("SELECT COUNT(*) FROM precursor_purity").fetchone()
    assert n == 2  # replaced, not appended


def test_run_precursor_purity_noop_when_sample_has_no_ms2(tmp_path):
    raw = _build_raw_db(
        tmp_path / "empty.db",
        [_ms1_scan(1, 5.0, 500.0, [(500.0, 1.0e5)])],
        [],
    )
    adb = _analysis_db_with_sample(tmp_path, raw)

    result = run_precursor_purity(adb, PurityConfig())

    assert result.n_scans == 0
    with sqlite3.connect(adb) as con:
        (n,) = con.execute("SELECT COUNT(*) FROM precursor_purity").fetchone()
    assert n == 0


def test_persist_purity_replaces_and_stamps_command_id(tmp_path):
    raw = _run_raw_db(tmp_path)
    adb = _analysis_db_with_sample(tmp_path, raw)
    cmd_id = log_command(adb, "precursor_purity", {}, run_id="ana")

    result = run_precursor_purity(adb, PurityConfig())
    persist_purity(adb, result, command_id=cmd_id)

    with sqlite3.connect(adb) as con:
        cmd_ids = {
            r[0] for r in con.execute("SELECT command_id FROM precursor_purity")
        }
    assert cmd_ids == {cmd_id}


def test_run_precursor_purity_parallel_matches_serial(tmp_path):
    """Two samples scored on a process pool give the same rows as serial."""
    raw1 = _run_raw_db(tmp_path, "s1.db")
    raw2 = _run_raw_db(tmp_path, "s2.db")

    adb_serial = tmp_path / "serial.db"
    init_analysis_db(adb_serial).close()
    sid1_s = register_sample(adb_serial, name="s1", raw_db_path=raw1)
    sid2_s = register_sample(adb_serial, name="s2", raw_db_path=raw2)
    serial = run_precursor_purity(adb_serial, PurityConfig(), n_workers=1)

    adb_par = tmp_path / "parallel.db"
    init_analysis_db(adb_par).close()
    register_sample(adb_par, name="s1", raw_db_path=raw1)
    register_sample(adb_par, name="s2", raw_db_path=raw2)
    parallel = run_precursor_purity(adb_par, PurityConfig(), n_workers=2)

    assert serial.n_scans == parallel.n_scans == 4
    assert {sid1_s, sid2_s} == {1, 2}

    def _snapshot(db: Path):
        with sqlite3.connect(db) as con:
            return {
                (r[0], r[1]): round(r[2], 6) if r[2] is not None else None
                for r in con.execute(
                    "SELECT sample_id, ms2_scan_id, purity_score "
                    "FROM precursor_purity"
                )
            }

    assert _snapshot(adb_serial) == _snapshot(adb_par)
    assert len(_snapshot(adb_par)) == 4
