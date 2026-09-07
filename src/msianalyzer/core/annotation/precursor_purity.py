"""Stage A' of MS2 annotation: precursor ion purity per MS2 scan.

Chimericity judged against the *analysis-wide feature list* (what
:mod:`msianalyzer.core.annotation.group_ms2` records as
``n_features_in_window``) over-flags on large acquisitions: the feature
list is the union over every sample and pixel, so a wide enough isolation
window always straddles several features even when the scan's own parent
MS1 held a single clean peak there.

This stage measures contamination where it physically happened — in the
MS1 scan the MS2 was triggered from (``ms2_scans.parent_scan_id``), and,
when the laser had moved on to an adjacent spot on the same raster line,
the immediately following MS1 scan. For each MS2 scan it records:

* ``purity`` — precursor peak intensity / total in-window intensity,
  RT-interpolated across the two bracketing MS1 scans when both are usable
  (msPurity-style). Falls back to the parent-only value otherwise.
* ``n_peaks_in_window`` — real peaks detected in the **parent** MS1 inside
  ``[target - lower, target + upper]``. ``> 1`` is the co-isolation signal.
* ``runner_up_rel_int`` — most intense non-precursor in-window peak divided
  by the precursor peak; ``> 1`` means the precursor was a minor ion.

None of these depend on the feature list, so they stay meaningful however
many samples the analysis spans.

The "same raster line" test compares pixel identity and, for adjacent
pixels, ``(x, y)`` indices plus the acquisition-time gap — never raw
coordinates alone, so serpentine vs. flyback rastering is irrelevant. See
:func:`infer_raster_geometry` / :func:`resolve_parent_next`.

Output table: ``precursor_purity`` (schema in
:func:`msianalyzer.core.analysis_db.create_analysis_schema`). Written by
:func:`run_precursor_purity`; a re-run replaces every row.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Sequence

import numpy as np
from scipy.signal import find_peaks

from ..analysis_db import create_analysis_schema
from ..utils.db import safe_executemany
from ..utils.logging_utils import log_call

if TYPE_CHECKING:  # avoid importing the config package at module load
    from ..config.config import PurityConfig

logger = logging.getLogger(__name__)

__all__ = [
    "RasterGeometry",
    "WindowPurity",
    "ResolvedScans",
    "PurityRow",
    "PurityResult",
    "ppm_between",
    "window_bounds",
    "detect_window_peaks",
    "score_window",
    "interpolate_purity",
    "infer_raster_geometry",
    "resolve_parent_next",
    "compute_scan_purity",
    "persist_purity",
    "run_precursor_purity",
]


# ---------------------------------------------------------------------------
# small numeric helpers
# ---------------------------------------------------------------------------


def ppm_between(mz: float, ref: float) -> float:
    """Signed ppm of ``mz`` relative to ``ref`` (positive => ``mz`` is higher)."""
    return (mz - ref) / ref * 1e6


def _is_pos(x: object) -> bool:
    """True when ``x`` is a real, strictly positive number."""
    try:
        v = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return np.isfinite(v) and v > 0.0


def window_bounds(
    target: float | None,
    lower_off: float | None,
    upper_off: float | None,
    default_half: float,
) -> tuple[float, float]:
    """Resolve an isolation window to absolute ``(lo, hi)`` m/z.

    ``lower_off`` / ``upper_off`` are Da *offsets* from ``target`` (mzML
    ``MS:1000828`` / ``MS:1000829``). A missing or non-positive offset is
    replaced by ``default_half``.

    Raises:
        ValueError: If ``target`` is ``None``.
    """
    if target is None:
        raise ValueError("window_bounds: target m/z is None")
    lo_off = float(lower_off) if _is_pos(lower_off) else float(default_half)
    up_off = float(upper_off) if _is_pos(upper_off) else float(default_half)
    return float(target) - lo_off, float(target) + up_off


# ---------------------------------------------------------------------------
# window peak detection (thin slice of a profile MS1 scan)
# ---------------------------------------------------------------------------


def _parabolic(mz: np.ndarray, inten: np.ndarray, idx: int) -> tuple[float, float]:
    """Sub-sample vertex of the parabola through three points around ``idx``."""
    if idx <= 0 or idx >= inten.size - 1:
        return float(mz[idx]), float(inten[idx])
    y1, y2, y3 = inten[idx - 1 : idx + 2]
    x1, x2, x3 = mz[idx - 1 : idx + 2]
    denom = y1 - 2.0 * y2 + y3
    if abs(denom) <= 1e-12:
        return float(x2), float(y2)
    delta = 0.5 * (y1 - y3) / denom
    return float(x2 + delta * (x3 - x1) / 2.0), float(y2 - 0.25 * (y1 - y3) * delta)


def _merge_ppm(peaks: np.ndarray, merge_ppm: float) -> np.ndarray:
    """Collapse peaks closer than ``merge_ppm``, keeping the more intense one.

    ``peaks`` is an ``(k, 2)`` ``(mz, intensity)`` array; the result is
    sorted by m/z.
    """
    if peaks.shape[0] <= 1 or merge_ppm <= 0:
        return peaks[np.argsort(peaks[:, 0])] if peaks.shape[0] else peaks
    order = np.argsort(peaks[:, 1])[::-1]  # strongest first
    kept: list[np.ndarray] = []
    for row in peaks[order]:
        if all(abs(ppm_between(row[0], k[0])) > merge_ppm for k in kept):
            kept.append(row)
    out = np.array(kept, dtype=float)
    return out[np.argsort(out[:, 0])]


def detect_window_peaks(
    mz: Sequence[float] | np.ndarray,
    inten: Sequence[float] | np.ndarray,
    lo: float,
    hi: float,
    *,
    min_rel_intensity: float = 0.01,
    merge_ppm: float = 5.0,
) -> np.ndarray:
    """Detect real peaks in ``[lo, hi]`` of a profile MS1 scan.

    A purpose-built local picker: local maxima above
    ``min_rel_intensity * (window base peak)``, each refined by parabolic
    interpolation, then merged within ``merge_ppm``. No baseline model —
    the slice is only a few Da wide.

    Returns:
        An ``(k, 2)`` array of ``(mz, intensity)`` sorted by m/z; empty
        ``(0, 2)`` when the window holds no signal.
    """
    mz = np.asarray(mz, dtype=float)
    inten = np.asarray(inten, dtype=float)
    empty = np.empty((0, 2), dtype=float)
    if mz.size == 0 or mz.size != inten.size:
        return empty

    in_win = (mz >= lo) & (mz <= hi)
    if not np.any(in_win):
        return empty
    w_mz = mz[in_win]
    w_int = inten[in_win]
    base = float(w_int.max())
    if base <= 0.0:
        return empty
    thresh = float(min_rel_intensity) * base

    if w_mz.size >= 3:
        idx, _ = find_peaks(w_int, height=thresh)
        if idx.size == 0:
            # a monotonic ramp / single-sided edge still carries a precursor
            top = int(np.argmax(w_int))
            idx = np.array([top]) if w_int[top] >= thresh else np.array([], dtype=int)
    else:
        top = int(np.argmax(w_int))
        idx = np.array([top]) if w_int[top] >= thresh else np.array([], dtype=int)

    if idx.size == 0:
        return empty

    refined = np.array([_parabolic(w_mz, w_int, int(i)) for i in idx], dtype=float)
    refined = refined[refined[:, 1] >= thresh]
    if refined.shape[0] == 0:
        return empty
    return _merge_ppm(refined, merge_ppm)


# ---------------------------------------------------------------------------
# scoring one window
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WindowPurity:
    """Purity of one isolation window in one MS1 scan.

    Attributes:
        n_peaks_in_window: Detected peaks in the window.
        purity: Precursor intensity / total in-window intensity, or
            ``None`` when the precursor peak was not found.
        runner_up_rel_int: Strongest non-precursor peak / precursor peak,
            or ``None`` when the precursor was not found or stood alone.
        precursor_found: A peak matched the reference m/z within tolerance.
        precursor_mz: m/z of that matched peak (observed), else ``None``.
        precursor_intensity: Intensity of that matched peak, else ``None``.
        total_intensity: Sum of every in-window peak intensity.
    """

    n_peaks_in_window: int
    purity: float | None
    runner_up_rel_int: float | None
    precursor_found: bool
    precursor_mz: float | None
    precursor_intensity: float | None
    total_intensity: float


def _match_precursor(peaks: np.ndarray, ref_mz: float | None, ppm: float) -> int | None:
    """Index of the in-window peak nearest ``ref_mz`` within ``ppm``, or None."""
    if ref_mz is None or peaks.shape[0] == 0:
        return None
    d = np.array([abs(ppm_between(m, ref_mz)) for m in peaks[:, 0]])
    j = int(np.argmin(d))
    return j if d[j] <= ppm else None


def score_window(
    peaks: np.ndarray, ref_mz: float | None, ppm: float
) -> WindowPurity:
    """Score a detected window peak list against the precursor reference m/z."""
    k = int(peaks.shape[0])
    if k == 0:
        return WindowPurity(0, None, None, False, None, None, 0.0)

    total = float(peaks[:, 1].sum())
    j = _match_precursor(peaks, ref_mz, ppm)
    if j is None or total <= 0.0:
        return WindowPurity(k, None, None, False, None, None, total)

    prec_int = float(peaks[j, 1])
    others = np.delete(peaks[:, 1], j)
    runner_up = float(others.max() / prec_int) if others.size and prec_int > 0 else None
    return WindowPurity(
        n_peaks_in_window=k,
        purity=prec_int / total,
        runner_up_rel_int=runner_up,
        precursor_found=True,
        precursor_mz=float(peaks[j, 0]),
        precursor_intensity=prec_int,
        total_intensity=total,
    )


def interpolate_purity(
    parent_peaks: np.ndarray,
    next_peaks: np.ndarray | None,
    ref_mz: float | None,
    ppm: float,
    rt_ms2: float | None,
    rt_parent: float | None,
    rt_next: float | None,
) -> tuple[float | None, float]:
    """RT-interpolate purity across the parent and next MS1 windows.

    Peaks are matched between the two scans within ``ppm``; each matched
    intensity is linearly interpolated by the MS2's position between the
    two scan times, unmatched peaks contribute their share scaled by the
    same weight. Purity is then the interpolated precursor intensity over
    the interpolated total.

    Returns:
        ``(purity, weight)``. ``purity`` is ``None`` (use the parent-only
        value) when ``next_peaks`` is missing, the scan times are unusable,
        or the precursor cannot be located in the blended spectrum.
        ``weight`` is the clamped fraction of the way from parent to next.
    """
    if (
        next_peaks is None
        or next_peaks.shape[0] == 0
        or rt_ms2 is None
        or rt_parent is None
        or rt_next is None
        or rt_next == rt_parent
    ):
        return None, 0.0

    w = (float(rt_ms2) - float(rt_parent)) / (float(rt_next) - float(rt_parent))
    w = float(min(1.0, max(0.0, w)))

    used_next = np.zeros(next_peaks.shape[0], dtype=bool)
    blended: list[tuple[float, float]] = []
    for pm, pi in parent_peaks:
        d = np.array([abs(ppm_between(nm, pm)) for nm in next_peaks[:, 0]])
        if d.size and not used_next.all():
            d[used_next] = np.inf
            j = int(np.argmin(d))
            if d[j] <= ppm:
                used_next[j] = True
                blended.append((pm, (1.0 - w) * pi + w * float(next_peaks[j, 1])))
                continue
        blended.append((pm, (1.0 - w) * pi))
    for j, (nm, ni) in enumerate(next_peaks):
        if not used_next[j]:
            blended.append((float(nm), w * float(ni)))

    arr = np.array(blended, dtype=float)
    return score_window(arr, ref_mz, ppm).purity, w


# ---------------------------------------------------------------------------
# raster geometry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RasterGeometry:
    """How a sample's pixel grid was rastered.

    Attributes:
        fast_axis: ``"x"`` or ``"y"`` — the index that steps by one between
            temporally consecutive pixels of the same line.
        max_gap_sec: Largest tolerated ``t_start(next) - t_end(parent)`` for
            two adjacent-line pixels to still count as one continuous sweep.
    """

    fast_axis: str
    max_gap_sec: float


@log_call
def infer_raster_geometry(
    con: sqlite3.Connection, *, gap_override: float | None = None
) -> RasterGeometry | None:
    """Infer the fast axis and inter-pixel gap tolerance from ``spatial_pixels``.

    Returns ``None`` when there is no pixel grid (mapping never run, or too
    few pixels, or the steps are too irregular to call an axis) — callers
    then fall back to parent-MS1-only purity.
    """
    try:
        rows = con.execute(
            "SELECT x, y, t_start, t_end FROM spatial_pixels ORDER BY t_start"
        ).fetchall()
    except sqlite3.OperationalError:
        return None
    if len(rows) < 2:
        return None

    xs = np.array([r[0] for r in rows], dtype=float)
    ys = np.array([r[1] for r in rows], dtype=float)
    t_start = np.array([r[2] for r in rows], dtype=float)
    t_end = np.array([r[3] for r in rows], dtype=float)

    dx = np.abs(np.diff(xs))
    dy = np.abs(np.diff(ys))
    x_steps = np.sum((dx == 1) & (dy == 0))
    y_steps = np.sum((dy == 1) & (dx == 0))
    if x_steps == 0 and y_steps == 0:
        return None
    fast_axis = "x" if x_steps >= y_steps else "y"

    if gap_override is not None:
        max_gap = float(gap_override)
    else:
        in_line = (dx == 1) & (dy == 0) if fast_axis == "x" else (dy == 1) & (dx == 0)
        gaps = t_start[1:][in_line] - t_end[:-1][in_line]
        gaps = gaps[np.isfinite(gaps)]
        median = float(np.median(gaps)) if gaps.size else 0.0
        span = float(np.median(t_end - t_start)) if len(rows) else 0.0
        max_gap = max(3.0 * median, 0.5 * span, 1e-6)
    return RasterGeometry(fast_axis=fast_axis, max_gap_sec=max_gap)


# ---------------------------------------------------------------------------
# parent / next MS1 resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedScans:
    """The MS1 scan(s) bracketing one MS2 scan.

    Attributes:
        ms2_scan_id: The MS2 scan this was resolved for.
        parent_scan_id / parent_rt / parent_mz / parent_inten: The parent
            MS1 scan (``None`` throughout when no MS1 precedes the MS2).
        next_scan_id / next_rt / next_mz / next_inten: The following MS1
            scan, kept only when ``bracket_kind`` is not ``"parent_only"``.
        bracket_kind: ``"parent_only"`` | ``"same_pixel"`` | ``"same_line"``.
    """

    ms2_scan_id: int
    parent_scan_id: int | None
    parent_rt: float | None
    parent_mz: np.ndarray | None
    parent_inten: np.ndarray | None
    next_scan_id: int | None
    next_rt: float | None
    next_mz: np.ndarray | None
    next_inten: np.ndarray | None
    bracket_kind: str


def _load_ms1(
    con: sqlite3.Connection, scan_id: int, cache: dict, decode
) -> tuple[float, np.ndarray, np.ndarray] | None:
    """``(rt, mz, intensity)`` for one MS1 scan, memoised by ``scan_id``."""
    if scan_id in cache:
        return cache[scan_id]
    row = con.execute(
        "SELECT rt, mz_array, intensity_array FROM ms1_scans WHERE scan_id = ?",
        (int(scan_id),),
    ).fetchone()
    cache[scan_id] = (
        None if row is None else (float(row[0]), decode(row[1]), decode(row[2]))
    )
    return cache[scan_id]


def _pixel_of(con: sqlite3.Connection, scan_id: int) -> int | None:
    try:
        row = con.execute(
            "SELECT pixel_id FROM pixel_ms1_scans WHERE scan_id = ? LIMIT 1",
            (int(scan_id),),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return None if row is None else int(row[0])


def _pixel_xy_t(con: sqlite3.Connection, pixel_id: int):
    row = con.execute(
        "SELECT x, y, t_start, t_end FROM spatial_pixels WHERE pixel_id = ?",
        (int(pixel_id),),
    ).fetchone()
    return None if row is None else (float(row[0]), float(row[1]), float(row[2]), float(row[3]))


def resolve_parent_next(
    con: sqlite3.Connection,
    ms2_row: dict,
    geom: RasterGeometry | None,
    *,
    use_next: bool,
    match_polarity: bool = True,
    ms1_cache: dict | None = None,
    decode: Callable[[bytes], np.ndarray] | None = None,
) -> ResolvedScans:
    """Find the parent MS1 scan of an MS2 scan and, when valid, the next one.

    Parent: ``ms2_scans.parent_scan_id`` when set and present, otherwise the
    latest MS1 at or before the MS2's ``rt`` (same polarity when
    ``match_polarity``). Next: the first MS1 after the parent, kept only
    when it is the **same pixel** as the parent, or an **adjacent pixel on
    the same raster line** within ``geom.max_gap_sec`` — otherwise
    ``bracket_kind`` stays ``"parent_only"`` and the next scan is dropped.

    Args:
        con: Open connection to the sample's raw database (read-only use).
        ms2_row: Mapping with ``scan_id``, ``parent_scan_id``, ``rt`` and
            ``polarity``.
        geom: The sample's :class:`RasterGeometry`, or ``None`` to force
            parent-only.
        use_next: Master switch for interpolation.
        match_polarity: Restrict MS1 candidates to the MS2's polarity.
        ms1_cache: Optional dict reused across calls to memoise MS1 loads.
        decode: Blob decoder; defaults to the parser's ``blob_to_array``.
    """
    if decode is None:
        from ..parser.mzml_parser import blob_to_array as decode  # noqa: N813
    cache = ms1_cache if ms1_cache is not None else {}

    scan_id = int(ms2_row["scan_id"])
    ms2_rt = ms2_row.get("rt")
    polarity = ms2_row.get("polarity")
    pol_ok = match_polarity and polarity is not None

    # --- parent -------------------------------------------------------------
    parent_id = ms2_row.get("parent_scan_id")
    parent = _load_ms1(con, parent_id, cache, decode) if parent_id is not None else None
    if parent is None:
        params: list = []
        clause = ""
        if ms2_rt is not None:
            clause = "WHERE rt <= ?"
            params.append(float(ms2_rt))
        if pol_ok:
            clause = f"{clause + ' AND ' if clause else 'WHERE '}polarity = ?"
            params.append(polarity)
        row = con.execute(
            f"SELECT scan_id FROM ms1_scans {clause} ORDER BY rt DESC LIMIT 1",
            params,
        ).fetchone()
        parent_id = None if row is None else int(row[0])
        parent = _load_ms1(con, parent_id, cache, decode) if parent_id is not None else None

    none_result = ResolvedScans(
        ms2_scan_id=scan_id,
        parent_scan_id=parent_id if parent is not None else None,
        parent_rt=parent[0] if parent else None,
        parent_mz=parent[1] if parent else None,
        parent_inten=parent[2] if parent else None,
        next_scan_id=None,
        next_rt=None,
        next_mz=None,
        next_inten=None,
        bracket_kind="parent_only",
    )
    if parent is None or not use_next or geom is None:
        return none_result

    # --- next -------------------------------------------------------------
    params = [parent[0]]
    clause = "WHERE rt > ?"
    if pol_ok:
        clause += " AND polarity = ?"
        params.append(polarity)
    row = con.execute(
        f"SELECT scan_id FROM ms1_scans {clause} ORDER BY rt ASC LIMIT 1", params
    ).fetchone()
    if row is None:
        return none_result
    next_id = int(row[0])
    nxt = _load_ms1(con, next_id, cache, decode)
    if nxt is None:
        return none_result

    parent_pixel = _pixel_of(con, parent_id)
    next_pixel = _pixel_of(con, next_id)
    bracket = "parent_only"
    if parent_pixel is not None and parent_pixel == next_pixel:
        bracket = "same_pixel"
    elif parent_pixel is not None and next_pixel is not None:
        p_xy = _pixel_xy_t(con, parent_pixel)
        n_xy = _pixel_xy_t(con, next_pixel)
        if p_xy is not None and n_xy is not None:
            if geom.fast_axis == "x":
                d_fast, d_slow = abs(n_xy[0] - p_xy[0]), abs(n_xy[1] - p_xy[1])
            else:
                d_fast, d_slow = abs(n_xy[1] - p_xy[1]), abs(n_xy[0] - p_xy[0])
            gap = n_xy[2] - p_xy[3]  # next.t_start - parent.t_end
            if d_slow == 0 and d_fast == 1 and 0.0 <= gap <= geom.max_gap_sec:
                bracket = "same_line"

    if bracket == "parent_only":
        return none_result
    return ResolvedScans(
        ms2_scan_id=scan_id,
        parent_scan_id=parent_id,
        parent_rt=parent[0],
        parent_mz=parent[1],
        parent_inten=parent[2],
        next_scan_id=next_id,
        next_rt=nxt[0],
        next_mz=nxt[1],
        next_inten=nxt[2],
        bracket_kind=bracket,
    )


# ---------------------------------------------------------------------------
# one MS2 scan -> one row
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PurityRow:
    """One ``precursor_purity`` row (see the module docstring for meaning)."""

    sample_id: int | None
    ms2_scan_id: int
    parent_ms1_scan_id: int | None
    next_ms1_scan_id: int | None
    bracket_kind: str
    rt_weight: float
    window_lo_mz: float | None
    window_hi_mz: float | None
    precursor_found: bool
    precursor_mz_ms1: float | None
    precursor_intensity_ms1: float | None
    n_peaks_in_window: int
    runner_up_rel_int: float | None
    purity: float | None
    purity_parent: float | None


def _ref_and_target(ms2_row: dict) -> tuple[float | None, float | None]:
    """``(reference m/z for the precursor peak, window centre)``."""
    prec = ms2_row.get("precursor_mz")
    tgt = ms2_row.get("isolation_window_target")
    ref = float(prec) if prec is not None else (float(tgt) if tgt is not None else None)
    center = float(tgt) if tgt is not None else ref
    return ref, center


def compute_scan_purity(
    ms2_row: dict,
    resolved: ResolvedScans,
    *,
    ppm: float,
    default_half_width: float,
    min_rel_intensity: float,
    merge_ppm: float,
) -> PurityRow:
    """Turn one MS2 scan + its resolved MS1 bracket into a :class:`PurityRow`."""
    sample_id = ms2_row.get("sample_id")
    scan_id = int(ms2_row["scan_id"])
    ref_mz, center = _ref_and_target(ms2_row)

    base = PurityRow(
        sample_id=sample_id,
        ms2_scan_id=scan_id,
        parent_ms1_scan_id=resolved.parent_scan_id,
        next_ms1_scan_id=None,
        bracket_kind=resolved.bracket_kind,
        rt_weight=0.0,
        window_lo_mz=None,
        window_hi_mz=None,
        precursor_found=False,
        precursor_mz_ms1=None,
        precursor_intensity_ms1=None,
        n_peaks_in_window=0,
        runner_up_rel_int=None,
        purity=None,
        purity_parent=None,
    )
    if center is None or resolved.parent_mz is None:
        return base

    lo, hi = window_bounds(
        center,
        ms2_row.get("isolation_window_lower"),
        ms2_row.get("isolation_window_upper"),
        default_half_width,
    )
    parent_peaks = detect_window_peaks(
        resolved.parent_mz,
        resolved.parent_inten,
        lo,
        hi,
        min_rel_intensity=min_rel_intensity,
        merge_ppm=merge_ppm,
    )
    wp = score_window(parent_peaks, ref_mz, ppm)

    purity = wp.purity
    purity_parent = wp.purity
    rt_weight = 0.0
    next_id = None
    if resolved.next_mz is not None:
        next_peaks = detect_window_peaks(
            resolved.next_mz,
            resolved.next_inten,
            lo,
            hi,
            min_rel_intensity=min_rel_intensity,
            merge_ppm=merge_ppm,
        )
        interp, rt_weight = interpolate_purity(
            parent_peaks,
            next_peaks,
            ref_mz,
            ppm,
            ms2_row.get("rt"),
            resolved.parent_rt,
            resolved.next_rt,
        )
        if interp is not None:
            purity = interp
            next_id = resolved.next_scan_id

    return PurityRow(
        sample_id=sample_id,
        ms2_scan_id=scan_id,
        parent_ms1_scan_id=resolved.parent_scan_id,
        next_ms1_scan_id=next_id,
        bracket_kind=resolved.bracket_kind if next_id is not None else "parent_only",
        rt_weight=float(rt_weight),
        window_lo_mz=float(lo),
        window_hi_mz=float(hi),
        precursor_found=wp.precursor_found,
        precursor_mz_ms1=wp.precursor_mz,
        precursor_intensity_ms1=wp.precursor_intensity,
        n_peaks_in_window=wp.n_peaks_in_window,
        runner_up_rel_int=wp.runner_up_rel_int,
        purity=purity,
        purity_parent=purity_parent,
    )


# ---------------------------------------------------------------------------
# result container + persistence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PurityResult:
    """Everything :func:`run_precursor_purity` produced.

    Attributes:
        rows: One :class:`PurityRow` per MS2 scan across every sample.
        n_scans: ``len(rows)``.
        n_multi_peak: Rows whose parent window held more than one peak.
        n_precursor_missing: Rows where no in-window peak matched the
            precursor reference m/z.
        n_interpolated: Rows whose purity used a second MS1 scan.
    """

    rows: list[PurityRow]
    n_scans: int
    n_multi_peak: int
    n_precursor_missing: int
    n_interpolated: int


_PURITY_COLS = (
    "sample_id",
    "ms2_scan_id",
    "parent_ms1_scan_id",
    "next_ms1_scan_id",
    "bracket_kind",
    "rt_weight",
    "window_lo_mz",
    "window_hi_mz",
    "precursor_found",
    "precursor_mz_ms1",
    "precursor_intensity_ms1",
    "n_peaks_in_window",
    "runner_up_rel_int",
    "purity",
    "purity_parent",
    "command_id",
)


@log_call(source="db_path")
def persist_purity(
    db_path: Path | str,
    result: PurityResult,
    *,
    command_id: int | None = None,
    replace_existing: bool = True,
) -> None:
    """Write a :class:`PurityResult` into ``precursor_purity``.

    A re-run replaces every row (one purity computation per analysis, like
    ``features``); ``samples`` and the grouper tables are untouched.
    """
    with sqlite3.connect(Path(db_path)) as con:
        con.execute("PRAGMA foreign_keys = ON")
        create_analysis_schema(con)
        if replace_existing:
            con.execute("DELETE FROM precursor_purity")

        placeholders = ", ".join("?" * len(_PURITY_COLS))
        safe_executemany(
            con,
            f"INSERT INTO precursor_purity ({', '.join(_PURITY_COLS)}) "
            f"VALUES ({placeholders})",
            [
                (
                    r.sample_id,
                    r.ms2_scan_id,
                    r.parent_ms1_scan_id,
                    r.next_ms1_scan_id,
                    r.bracket_kind,
                    r.rt_weight,
                    r.window_lo_mz,
                    r.window_hi_mz,
                    int(r.precursor_found),
                    r.precursor_mz_ms1,
                    r.precursor_intensity_ms1,
                    int(r.n_peaks_in_window),
                    r.runner_up_rel_int,
                    r.purity,
                    r.purity_parent,
                    command_id,
                )
                for r in result.rows
            ],
            table="precursor_purity",
            logger=logger,
            source=db_path,
        )
        con.commit()


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

_MS2_QUERY = (
    "SELECT scan_id, parent_scan_id, rt, polarity, precursor_mz, "
    "isolation_window_target, isolation_window_lower, isolation_window_upper "
    "FROM ms2_scans"
)


def _make_result(rows: list[PurityRow]) -> PurityResult:
    return PurityResult(
        rows=rows,
        n_scans=len(rows),
        n_multi_peak=sum(r.n_peaks_in_window > 1 for r in rows),
        n_precursor_missing=sum(not r.precursor_found for r in rows),
        n_interpolated=sum(r.next_ms1_scan_id is not None for r in rows),
    )


@log_call(source="analysis_db_path")
def run_precursor_purity(
    analysis_db_path: Path | str,
    config: "PurityConfig",
    *,
    command_id: int | None = None,
) -> PurityResult:
    """Score every sample's MS2 scans for precursor purity and persist them.

    Expects ``samples`` already populated in the analysis database. Each
    raw database is opened read-only.

    Args:
        analysis_db_path: The analysis database.
        config: Anything exposing the :class:`~msianalyzer.core.config.config.PurityConfig`
            fields (``ppm_precursor_match``, ``default_half_window_da``,
            ``min_rel_intensity``, ``merge_ppm``, ``use_next_ms1``,
            ``max_interpixel_gap_sec``).
        command_id: Optional ``commands.id`` stamped on every row.
    """
    from ..parser.mzml_parser import blob_to_array

    analysis_db_path = Path(analysis_db_path)
    with sqlite3.connect(analysis_db_path) as con:
        samples = con.execute(
            "SELECT sample_id, raw_db_path FROM samples"
        ).fetchall()

    ppm = float(config.ppm_precursor_match)
    default_half = float(config.default_half_window_da)
    min_rel = float(config.min_rel_intensity)
    merge_ppm = float(config.merge_ppm)
    use_next = bool(config.use_next_ms1)
    gap_override = config.max_interpixel_gap_sec

    rows: list[PurityRow] = []
    for sample_id, raw_db_path in samples:
        # opened for reading only — this stage never writes a raw database
        with sqlite3.connect(str(raw_db_path)) as rcon:
            geom = infer_raster_geometry(rcon, gap_override=gap_override)
            ms2_rows = rcon.execute(_MS2_QUERY).fetchall()
            logger.info(
                "precursor purity: sample %s — %d MS2 scans, geometry=%s",
                sample_id,
                len(ms2_rows),
                geom,
                extra={"source_file": str(raw_db_path)},
            )
            ms1_cache: dict = {}
            for r in ms2_rows:
                ms2_row = {
                    "sample_id": sample_id,
                    "scan_id": r[0],
                    "parent_scan_id": r[1],
                    "rt": r[2],
                    "polarity": r[3],
                    "precursor_mz": r[4],
                    "isolation_window_target": r[5],
                    "isolation_window_lower": r[6],
                    "isolation_window_upper": r[7],
                }
                resolved = resolve_parent_next(
                    rcon,
                    ms2_row,
                    geom,
                    use_next=use_next,
                    ms1_cache=ms1_cache,
                    decode=blob_to_array,
                )
                rows.append(
                    compute_scan_purity(
                        ms2_row,
                        resolved,
                        ppm=ppm,
                        default_half_width=default_half,
                        min_rel_intensity=min_rel,
                        merge_ppm=merge_ppm,
                    )
                )

    result = _make_result(rows)
    persist_purity(analysis_db_path, result, command_id=command_id)
    logger.info(
        "precursor purity: %d scans (%d multi-peak, %d precursor-missing, "
        "%d interpolated)",
        result.n_scans,
        result.n_multi_peak,
        result.n_precursor_missing,
        result.n_interpolated,
    )
    return result
