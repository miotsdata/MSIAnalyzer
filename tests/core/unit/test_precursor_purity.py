"""Tests for :mod:`msianalyzer.core.annotation.precursor_purity`.

Split into three layers:

* pure array maths — ``window_bounds`` / ``detect_window_peaks`` /
  ``score_window`` / ``interpolate_purity`` (no I/O);
* raster reasoning against an in-memory-ish raw SQLite DB —
  ``infer_raster_geometry`` / ``resolve_parent_next``;
* the ``run_precursor_purity`` orchestrator end to end.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from msianalyzer.core.annotation.precursor_purity import (
    RasterGeometry,
    ResolvedScans,
    compute_scan_purity,
    detect_window_peaks,
    infer_raster_geometry,
    interpolate_purity,
    persist_purity,
    resolve_parent_next,
    run_precursor_purity,
    score_window,
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
    pixels: list[tuple[int, int, float, float]] | None = None,
) -> Path:
    """Write a raw-schema SQLite DB with MS1/MS2 scans and optional pixels."""
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

    if pixels is not None:
        from msianalyzer.core.utils.spectra_pixels_association import (
            map_pixels_to_db,
        )

        map_pixels_to_db(
            path,
            pd.DataFrame(pixels, columns=["x", "y", "t_start", "t_end"]),
        )
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
# detect_window_peaks
# ===========================================================================


def test_detect_window_peaks_finds_two_resolved_peaks():
    mz, inten = _profile([(500.00, 100.0), (500.15, 80.0)], 499.0, 501.0)
    peaks = detect_window_peaks(mz, inten, 499.5, 500.5)
    assert peaks.shape == (2, 2)
    np.testing.assert_allclose(peaks[:, 0], [500.00, 500.15], atol=2e-3)


def test_detect_window_peaks_drops_below_min_rel_intensity():
    mz, inten = _profile([(500.0, 100.0), (500.30, 1.0)], 499.0, 501.0)
    peaks = detect_window_peaks(mz, inten, 499.5, 500.5, min_rel_intensity=0.05)
    assert peaks.shape == (1, 2)
    assert peaks[0, 0] == pytest.approx(500.0, abs=2e-3)


def test_detect_window_peaks_empty_window_returns_empty():
    mz, inten = _profile([(500.0, 100.0)], 499.0, 501.0)
    peaks = detect_window_peaks(mz, inten, 400.0, 401.0)
    assert peaks.shape == (0, 2)


# ===========================================================================
# score_window
# ===========================================================================


def test_score_window_single_clean_peak_purity_one():
    wp = score_window(np.array([[500.0, 100.0]]), 500.0, ppm=20.0)
    assert wp.precursor_found is True
    assert wp.purity == pytest.approx(1.0)
    assert wp.n_peaks_in_window == 1
    assert wp.runner_up_rel_int is None


def test_score_window_two_equal_peaks_purity_half():
    wp = score_window(np.array([[500.0, 100.0], [500.3, 100.0]]), 500.0, ppm=20.0)
    assert wp.purity == pytest.approx(0.5)
    assert wp.runner_up_rel_int == pytest.approx(1.0)
    assert wp.n_peaks_in_window == 2


def test_score_window_minor_precursor_runner_up_gt_one():
    wp = score_window(np.array([[500.0, 10.0], [500.3, 90.0]]), 500.0, ppm=20.0)
    assert wp.purity == pytest.approx(0.1)
    assert wp.runner_up_rel_int == pytest.approx(9.0)


def test_score_window_precursor_absent_sets_not_found():
    wp = score_window(np.array([[500.5, 100.0]]), 500.0, ppm=20.0)
    assert wp.precursor_found is False
    assert wp.purity is None
    assert wp.n_peaks_in_window == 1


def test_score_window_empty_returns_zero_peaks():
    wp = score_window(np.empty((0, 2)), 500.0, ppm=20.0)
    assert wp.n_peaks_in_window == 0
    assert wp.purity is None
    assert wp.precursor_found is False


# ===========================================================================
# interpolate_purity
# ===========================================================================


def test_interpolate_purity_midpoint_between_scans():
    parent = np.array([[500.0, 100.0]])
    nxt = np.array([[500.0, 100.0], [500.3, 100.0]])
    purity, w = interpolate_purity(parent, nxt, 500.0, 20.0, 5.0, 0.0, 10.0)
    assert w == pytest.approx(0.5)
    # precursor 100, interferent 0.5*100=50 -> 100 / 150
    assert purity == pytest.approx(2.0 / 3.0)


def test_interpolate_purity_clamps_weight_outside_bracket():
    parent = np.array([[500.0, 100.0]])
    nxt = np.array([[500.0, 100.0], [500.3, 100.0]])
    purity, w = interpolate_purity(parent, nxt, 500.0, 20.0, 20.0, 0.0, 10.0)
    assert w == pytest.approx(1.0)
    assert purity == pytest.approx(0.5)


def test_interpolate_purity_none_next_returns_none():
    parent = np.array([[500.0, 100.0]])
    assert interpolate_purity(parent, None, 500.0, 20.0, 5.0, 0.0, 10.0) == (None, 0.0)
    assert interpolate_purity(
        parent, np.empty((0, 2)), 500.0, 20.0, 5.0, 0.0, 10.0
    ) == (None, 0.0)


# ===========================================================================
# infer_raster_geometry
# ===========================================================================


def _rows_grid(width: int, height: int, *, serpentine: bool, transpose: bool):
    """(x, y, t_start, t_end) pixel rows in acquisition order."""
    rows: list[tuple[int, int, float, float]] = []
    t = 0.0
    for row in range(height):
        cols = range(width)
        if serpentine and row % 2 == 1:
            cols = reversed(range(width))
        for col in cols:
            x, y = (row, col) if transpose else (col, row)
            rows.append((x, y, t, t + 10.0))
            t += 10.0
    return rows


def test_infer_raster_geometry_detects_fast_axis_x(tmp_path):
    db = _build_raw_db(
        tmp_path / "geo_x.db", [], [],
        pixels=_rows_grid(4, 3, serpentine=False, transpose=False),
    )
    with sqlite3.connect(db) as con:
        geom = infer_raster_geometry(con)
    assert geom is not None and geom.fast_axis == "x"
    assert geom.max_gap_sec > 0


def test_infer_raster_geometry_detects_fast_axis_y(tmp_path):
    db = _build_raw_db(
        tmp_path / "geo_y.db", [], [],
        pixels=_rows_grid(4, 3, serpentine=False, transpose=True),
    )
    with sqlite3.connect(db) as con:
        geom = infer_raster_geometry(con)
    assert geom is not None and geom.fast_axis == "y"


def test_infer_raster_geometry_handles_serpentine(tmp_path):
    db = _build_raw_db(
        tmp_path / "geo_s.db", [], [],
        pixels=_rows_grid(5, 4, serpentine=True, transpose=False),
    )
    with sqlite3.connect(db) as con:
        geom = infer_raster_geometry(con)
    assert geom is not None and geom.fast_axis == "x"


def test_infer_raster_geometry_none_without_pixels(tmp_path):
    db = _build_raw_db(tmp_path / "geo_none.db", [], [], pixels=None)
    with sqlite3.connect(db) as con:
        assert infer_raster_geometry(con) is None


def test_infer_raster_geometry_none_with_single_pixel(tmp_path):
    db = _build_raw_db(
        tmp_path / "geo_one.db", [], [], pixels=[(0, 0, 0.0, 10.0)]
    )
    with sqlite3.connect(db) as con:
        assert infer_raster_geometry(con) is None


def test_infer_raster_geometry_gap_override_is_used(tmp_path):
    db = _build_raw_db(
        tmp_path / "geo_ov.db", [], [],
        pixels=_rows_grid(4, 2, serpentine=False, transpose=False),
    )
    with sqlite3.connect(db) as con:
        geom = infer_raster_geometry(con, gap_override=42.0)
    assert geom.max_gap_sec == pytest.approx(42.0)


# ===========================================================================
# resolve_parent_next
# ===========================================================================

_GEOM_X = RasterGeometry(fast_axis="x", max_gap_sec=50.0)


def _two_pixel_db(tmp_path, name, *, pixel_b, ms1_rows, ms2_rows):
    return _build_raw_db(
        tmp_path / name, ms1_rows, ms2_rows,
        pixels=[(0, 0, 0.0, 10.0), pixel_b],
    )


def test_resolve_parent_next_uses_parent_scan_id(tmp_path):
    db = _build_raw_db(
        tmp_path / "p.db",
        [_ms1_scan(1, 5.0, 500.0, [(500.0, 100.0)])],
        [{"scan_id": 100, "parent_scan_id": 1, "rt": 6.0, "precursor_mz": 500.0}],
        pixels=[(0, 0, 0.0, 10.0)],
    )
    with sqlite3.connect(db) as con:
        row = con.execute(_MS2_SEL).fetchone()
        res = resolve_parent_next(con, _ms2_dict(row), _GEOM_X, use_next=False)
    assert res.parent_scan_id == 1
    assert res.parent_rt == pytest.approx(5.0)
    assert res.bracket_kind == "parent_only"
    assert res.next_scan_id is None


def test_resolve_parent_next_falls_back_when_parent_scan_id_null(tmp_path):
    db = _build_raw_db(
        tmp_path / "f.db",
        [
            _ms1_scan(1, 3.0, 500.0, [(500.0, 100.0)]),
            _ms1_scan(2, 9.0, 500.0, [(500.0, 100.0)]),
        ],
        [{"scan_id": 100, "parent_scan_id": None, "rt": 6.0, "precursor_mz": 500.0}],
        pixels=[(0, 0, 0.0, 12.0)],
    )
    with sqlite3.connect(db) as con:
        row = con.execute(_MS2_SEL).fetchone()
        res = resolve_parent_next(con, _ms2_dict(row), _GEOM_X, use_next=False)
    assert res.parent_scan_id == 1  # latest MS1 at or before rt 6.0


def test_resolve_parent_next_same_pixel_bracket(tmp_path):
    db = _build_raw_db(
        tmp_path / "sp.db",
        [
            _ms1_scan(1, 3.0, 500.0, [(500.0, 100.0)]),
            _ms1_scan(2, 7.0, 500.0, [(500.0, 100.0)]),
        ],
        [{"scan_id": 100, "parent_scan_id": 1, "rt": 4.0, "precursor_mz": 500.0}],
        pixels=[(0, 0, 0.0, 10.0)],  # both MS1 fall in this one pixel
    )
    with sqlite3.connect(db) as con:
        row = con.execute(_MS2_SEL).fetchone()
        res = resolve_parent_next(con, _ms2_dict(row), _GEOM_X, use_next=True)
    assert res.bracket_kind == "same_pixel"
    assert res.next_scan_id == 2


def test_resolve_parent_next_same_line_adjacent_bracket(tmp_path):
    db = _two_pixel_db(
        tmp_path, "sl.db",
        pixel_b=(1, 0, 10.0, 20.0),
        ms1_rows=[
            _ms1_scan(1, 5.0, 500.0, [(500.0, 100.0)]),
            _ms1_scan(2, 15.0, 500.0, [(500.0, 100.0)]),
        ],
        ms2_rows=[
            {"scan_id": 100, "parent_scan_id": 1, "rt": 6.0, "precursor_mz": 500.0}
        ],
    )
    with sqlite3.connect(db) as con:
        row = con.execute(_MS2_SEL).fetchone()
        res = resolve_parent_next(con, _ms2_dict(row), _GEOM_X, use_next=True)
    assert res.bracket_kind == "same_line"
    assert res.next_scan_id == 2


def test_resolve_parent_next_rejects_line_change(tmp_path):
    db = _two_pixel_db(
        tmp_path, "lc.db",
        pixel_b=(0, 1, 10.0, 20.0),  # next row, not adjacent on the line
        ms1_rows=[
            _ms1_scan(1, 5.0, 500.0, [(500.0, 100.0)]),
            _ms1_scan(2, 15.0, 500.0, [(500.0, 100.0)]),
        ],
        ms2_rows=[
            {"scan_id": 100, "parent_scan_id": 1, "rt": 6.0, "precursor_mz": 500.0}
        ],
    )
    with sqlite3.connect(db) as con:
        row = con.execute(_MS2_SEL).fetchone()
        res = resolve_parent_next(con, _ms2_dict(row), _GEOM_X, use_next=True)
    assert res.bracket_kind == "parent_only"
    assert res.next_scan_id is None


def test_resolve_parent_next_rejects_temporal_gap(tmp_path):
    db = _two_pixel_db(
        tmp_path, "tg.db",
        pixel_b=(1, 0, 100.0, 110.0),  # adjacent on the line but 90 s later
        ms1_rows=[
            _ms1_scan(1, 5.0, 500.0, [(500.0, 100.0)]),
            _ms1_scan(2, 105.0, 500.0, [(500.0, 100.0)]),
        ],
        ms2_rows=[
            {"scan_id": 100, "parent_scan_id": 1, "rt": 6.0, "precursor_mz": 500.0}
        ],
    )
    tight = RasterGeometry(fast_axis="x", max_gap_sec=5.0)
    with sqlite3.connect(db) as con:
        row = con.execute(_MS2_SEL).fetchone()
        res = resolve_parent_next(con, _ms2_dict(row), tight, use_next=True)
    assert res.bracket_kind == "parent_only"


def test_resolve_parent_next_parent_only_when_use_next_false(tmp_path):
    db = _two_pixel_db(
        tmp_path, "un.db",
        pixel_b=(1, 0, 10.0, 20.0),
        ms1_rows=[
            _ms1_scan(1, 5.0, 500.0, [(500.0, 100.0)]),
            _ms1_scan(2, 15.0, 500.0, [(500.0, 100.0)]),
        ],
        ms2_rows=[
            {"scan_id": 100, "parent_scan_id": 1, "rt": 6.0, "precursor_mz": 500.0}
        ],
    )
    with sqlite3.connect(db) as con:
        row = con.execute(_MS2_SEL).fetchone()
        res = resolve_parent_next(con, _ms2_dict(row), _GEOM_X, use_next=False)
    assert res.bracket_kind == "parent_only"


def test_resolve_parent_next_parent_only_when_geom_none(tmp_path):
    db = _two_pixel_db(
        tmp_path, "gn.db",
        pixel_b=(1, 0, 10.0, 20.0),
        ms1_rows=[
            _ms1_scan(1, 5.0, 500.0, [(500.0, 100.0)]),
            _ms1_scan(2, 15.0, 500.0, [(500.0, 100.0)]),
        ],
        ms2_rows=[
            {"scan_id": 100, "parent_scan_id": 1, "rt": 6.0, "precursor_mz": 500.0}
        ],
    )
    with sqlite3.connect(db) as con:
        row = con.execute(_MS2_SEL).fetchone()
        res = resolve_parent_next(con, _ms2_dict(row), None, use_next=True)
    assert res.bracket_kind == "parent_only"


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


# ===========================================================================
# compute_scan_purity
# ===========================================================================


def _resolved(parent_peaks, *, next_peaks=None, center=500.0):
    p_mz, p_int = _profile(parent_peaks, center - 3.0, center + 3.0)
    n_mz = n_int = None
    if next_peaks is not None:
        n_mz, n_int = _profile(next_peaks, center - 3.0, center + 3.0)
    return ResolvedScans(
        ms2_scan_id=1,
        parent_scan_id=10,
        parent_rt=0.0,
        parent_mz=p_mz,
        parent_inten=p_int,
        next_scan_id=11 if next_peaks is not None else None,
        next_rt=10.0 if next_peaks is not None else None,
        next_mz=n_mz,
        next_inten=n_int,
        bracket_kind="same_line" if next_peaks is not None else "parent_only",
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

_KW = dict(ppm=20.0, default_half_width=0.5, min_rel_intensity=0.01, merge_ppm=5.0)


def test_compute_scan_purity_clean_scan_high_purity():
    row = compute_scan_purity(_SCAN, _resolved([(500.0, 1.0e5)]), **_KW)
    assert row.precursor_found is True
    assert row.n_peaks_in_window == 1
    assert row.purity == pytest.approx(1.0, abs=1e-3)
    assert row.purity_parent == pytest.approx(1.0, abs=1e-3)
    assert row.bracket_kind == "parent_only"


def test_compute_scan_purity_chimeric_scan_low_purity():
    row = compute_scan_purity(
        _SCAN, _resolved([(500.0, 2.0e4), (500.25, 8.0e4)]), **_KW
    )
    assert row.n_peaks_in_window == 2
    assert row.purity == pytest.approx(0.2, abs=2e-2)
    assert row.runner_up_rel_int == pytest.approx(4.0, rel=0.1)


def test_compute_scan_purity_interpolates_with_next_scan():
    row = compute_scan_purity(
        _SCAN,
        _resolved([(500.0, 1.0e5)], next_peaks=[(500.0, 1.0e5), (500.3, 1.0e5)]),
        **_KW,
    )
    # parent alone is clean; the blended window is contaminated
    assert row.purity_parent == pytest.approx(1.0, abs=1e-3)
    assert row.purity < 0.8
    assert row.next_ms1_scan_id == 11
    assert row.rt_weight == pytest.approx(0.5)


def test_compute_scan_purity_no_parent_returns_empty_row():
    resolved = ResolvedScans(
        ms2_scan_id=1, parent_scan_id=None, parent_rt=None, parent_mz=None,
        parent_inten=None, next_scan_id=None, next_rt=None, next_mz=None,
        next_inten=None, bracket_kind="parent_only",
    )
    row = compute_scan_purity(_SCAN, resolved, **_KW)
    assert row.n_peaks_in_window == 0
    assert row.purity is None
    assert row.precursor_found is False


# ===========================================================================
# run_precursor_purity (orchestrator)
# ===========================================================================


def _analysis_db_with_sample(tmp_path, raw_db: Path) -> Path:
    adb = tmp_path / "analysis.db"
    init_analysis_db(adb).close()
    register_sample(adb, name=raw_db.stem, raw_db_path=raw_db)
    return adb


def _run_raw_db(tmp_path) -> Path:
    """One sample: a clean MS2 and a chimeric MS2, both on the same pixel line."""
    ms1 = [
        _ms1_scan(1, 5.0, 500.0, [(500.0, 1.0e5)]),
        _ms1_scan(2, 15.0, 700.0, [(700.0, 2.0e4), (700.25, 8.0e4)]),
        _ms1_scan(3, 25.0, 700.0, [(700.0, 2.0e4), (700.25, 8.0e4)]),
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
    pixels = [(0, 0, 0.0, 10.0), (1, 0, 10.0, 20.0), (2, 0, 20.0, 30.0)]
    return _build_raw_db(tmp_path / "sample.db", ms1, ms2, pixels=pixels)


def test_run_precursor_purity_persists_rows_and_returns_result(tmp_path):
    raw = _run_raw_db(tmp_path)
    adb = _analysis_db_with_sample(tmp_path, raw)

    result = run_precursor_purity(adb, PurityConfig(), command_id=None)

    assert result.n_scans == 2
    assert result.n_multi_peak == 1

    with sqlite3.connect(adb) as con:
        rows = {
            r[0]: r
            for r in con.execute(
                "SELECT ms2_scan_id, purity, n_peaks_in_window, precursor_found "
                "FROM precursor_purity"
            ).fetchall()
        }
    assert set(rows) == {100, 101}
    assert rows[100][1] == pytest.approx(1.0, abs=1e-3)  # clean
    assert rows[100][2] == 1
    assert rows[101][1] == pytest.approx(0.2, abs=3e-2)  # chimeric
    assert rows[101][2] == 2
    assert rows[101][3] == 1


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
        pixels=[(0, 0, 0.0, 10.0)],
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
