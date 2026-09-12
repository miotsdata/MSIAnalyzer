"""Stage A' of MS2 annotation: precursor ion purity per MS2 scan.

Chimericity judged against the *analysis-wide feature list* (what
:mod:`msianalyzer.core.annotation.group_ms2` used to record as
``n_features_in_window``) over-flags on large acquisitions: the feature
list is the union over every sample and pixel, so a wide enough isolation
window always straddles several features even when the scan's own parent
MS1 held a single clean peak there. That flag (and the peak-picking-based
``purity`` this stage originally also computed, msPurity-style, interpolated
across the parent + next MS1 scan) has been retired — see ADR 0019. Real
MALDI-imaging data showed the in-window peak-picker failing to resolve the
precursor as a discrete peak in ~56% of dense, matrix-heavy, low-m/z
windows, even when the signal was plainly present; a metric that fails that
often cannot be the primary "is this scan trustworthy" signal.

This stage now measures contamination where it physically happened — in the
MS1 scan the MS2 was triggered from (``ms2_scans.parent_scan_id``) — using
only the peak-detection-free view, which is always computable:

* ``precursor_frac`` — above-baseline profile area within
  ``precursor_confirm_ppm`` of the recorded ``precursor_mz`` over the whole
  isolation window's area. ``1.0`` means all of the window's ion current
  sits on the precursor; a low value means another, unrelated ion
  dominates the window — the real, per-scan chimeric-risk signal. Robust
  where peak detection fails (dense, matrix-heavy, low-m/z windows).
* ``precursor_confirmed`` — ``precursor_frac >= precursor_confirm_min_frac``:
  the recorded precursor really does carry signal in its own parent MS1
  (association-confidence gate; carried onto ``ms2_annotations``).
* ``precursor_mz_snapped`` / ``snap_shift_ppm`` — ``precursor_mz`` snapped
  to the nearest parent-MS1 local maximum within ``precursor_snap_ppm``
  (a tight radius, so it can never jump to a neighbouring ion; a no-op when
  the recorded value is already on a peak). Association is **not** re-run —
  this is a refined value for downstream QC.

None of these depend on the feature list, so they stay meaningful however
many samples the analysis spans, and none require detecting a discrete peak
in the isolation window.

Samples are independent — each opens its own read-only raw database and
builds its own in-memory scan index — so :func:`run_precursor_purity`
scores them one process per sample (``purity.n_workers``), then writes the
whole ``precursor_purity`` table once in the parent.

Output table: ``precursor_purity`` (schema in
:func:`msianalyzer.core.analysis_db.create_analysis_schema`). Written by
:func:`run_precursor_purity`; a re-run replaces every row.
"""

from __future__ import annotations

import bisect
import logging
import os
import sqlite3
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor, as_completed
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
    "ResolvedParent",
    "SampleScanIndex",
    "PurityRow",
    "PurityResult",
    "ppm_between",
    "window_bounds",
    "integrate_precursor_fraction",
    "snap_precursor_mz",
    "resolve_parent",
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
# peak-detection-free precursor confirmation + m/z snap
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


def integrate_precursor_fraction(
    mz: Sequence[float] | np.ndarray,
    inten: Sequence[float] | np.ndarray,
    lo: float,
    hi: float,
    ref_mz: float | None,
    *,
    band_ppm: float,
    baseline_pct: float = 10.0,
) -> float:
    """Above-baseline profile area within ``band_ppm`` of ``ref_mz`` / window area.

    A purity proxy that needs no peak detection: ``1.0`` means all of the
    isolation window's ion current sits on the precursor, ``~0`` means the
    precursor is a minor co-isolate. Robust in dense, matrix-heavy, low-m/z
    windows where a discrete peak often cannot be resolved at all. Returns
    ``0.0`` when there is no signal.
    """
    if ref_mz is None or hi <= lo:
        return 0.0
    mz = np.asarray(mz)
    inten = np.asarray(inten)
    if mz.size == 0 or mz.size != inten.size:
        return 0.0
    i0 = int(np.searchsorted(mz, lo, side="left"))
    i1 = int(np.searchsorted(mz, hi, side="right"))
    w_mz = np.asarray(mz[i0:i1], dtype=float)
    w_it = np.asarray(inten[i0:i1], dtype=float)
    if w_mz.size == 0:
        return 0.0
    base = float(np.percentile(w_it, baseline_pct))
    above = np.clip(w_it - base, 0.0, None)
    total = float(above.sum())
    if total <= 0.0:
        return 0.0
    b_lo = max(lo, ref_mz * (1.0 - band_ppm / 1e6))
    b_hi = min(hi, ref_mz * (1.0 + band_ppm / 1e6))
    band = (w_mz >= b_lo) & (w_mz <= b_hi)
    return float(above[band].sum() / total)


def snap_precursor_mz(
    mz: Sequence[float] | np.ndarray,
    inten: Sequence[float] | np.ndarray,
    ref_mz: float | None,
    *,
    snap_ppm: float,
) -> tuple[float | None, float]:
    """Snap ``ref_mz`` to the nearest parent-MS1 local maximum within ``snap_ppm``.

    Returns ``(snapped_mz, signed_ppm_shift)``. A no-op — ``(ref_mz, 0.0)`` —
    when snapping is disabled (``snap_ppm <= 0``), ``ref_mz`` is ``None``, or
    no local maximum lies in the band (e.g. the precursor is an unresolved
    shoulder). The search radius is deliberately tight so the snap can never
    jump to a neighbouring ion.
    """
    if ref_mz is None:
        return None, 0.0
    if snap_ppm <= 0:
        return float(ref_mz), 0.0
    mz = np.asarray(mz)
    inten = np.asarray(inten)
    if mz.size < 3:
        return float(ref_mz), 0.0
    lo = ref_mz * (1.0 - snap_ppm / 1e6)
    hi = ref_mz * (1.0 + snap_ppm / 1e6)
    i0 = int(np.searchsorted(mz, lo, side="left"))
    i1 = int(np.searchsorted(mz, hi, side="right"))
    if i1 - i0 < 3:
        return float(ref_mz), 0.0
    b_mz = np.asarray(mz[i0:i1], dtype=float)
    b_it = np.asarray(inten[i0:i1], dtype=float)
    idx, _ = find_peaks(b_it)
    if idx.size == 0:
        return float(ref_mz), 0.0
    j = int(idx[np.argmin(np.abs(b_mz[idx] - ref_mz))])
    apex_mz, _ = _parabolic(b_mz, b_it, j)
    return float(apex_mz), float(ppm_between(apex_mz, ref_mz))


# ---------------------------------------------------------------------------
# parent MS1 resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedParent:
    """The MS1 scan an MS2 scan was triggered from.

    Attributes:
        ms2_scan_id: The MS2 scan this was resolved for.
        parent_scan_id / parent_mz / parent_inten: The parent MS1 scan's id
            and profile arrays (``None`` throughout when no MS1 precedes
            the MS2, or its arrays can't be read).
    """

    ms2_scan_id: int
    parent_scan_id: int | None
    parent_mz: np.ndarray | None
    parent_inten: np.ndarray | None


class SampleScanIndex:
    """Per-sample MS1 lookups, built once and queried in memory.

    Preloads MS1 ``(scan_id, rt, polarity)`` sorted by rt, so
    :func:`resolve_parent` needs no per-scan SQL. MS1 intensity arrays are
    still read on demand, behind a small LRU (MS2 scans of one parent are
    consecutive in ``scan_id`` order, so a handful of slots is plenty).
    """

    def __init__(
        self,
        con: sqlite3.Connection,
        *,
        match_polarity: bool = True,
        decode: Callable[[bytes], np.ndarray] | None = None,
        array_cache_size: int = 8,
    ) -> None:
        from ..parser.mzml_parser import blob_to_array

        self._con = con
        self._decode = decode or blob_to_array
        self._match_polarity = bool(match_polarity)
        self._cache_size = max(1, int(array_cache_size))
        self._arrays: "OrderedDict[int, tuple | None]" = OrderedDict()
        # A single acquisition method almost always writes a byte-identical
        # profile m/z grid to every MS1 scan; decoding it once per distinct
        # blob roughly halves the zlib cost of this stage.
        self._mz_by_blob: "OrderedDict[bytes, np.ndarray]" = OrderedDict()

        rows = con.execute(
            "SELECT scan_id, rt, polarity FROM ms1_scans ORDER BY rt"
        ).fetchall()
        self._ids = [int(r[0]) for r in rows]
        self._rts = [float(r[1]) for r in rows]
        self._pols = [r[2] for r in rows]
        self._rt_of = dict(zip(self._ids, self._rts))

    def rt_of(self, scan_id: int | None) -> float | None:
        return None if scan_id is None else self._rt_of.get(int(scan_id))

    def _ok_pol(self, i: int, polarity) -> bool:
        return (
            not (self._match_polarity and polarity is not None)
            or self._pols[i] == polarity
        )

    def parent_before(self, rt: float | None, polarity) -> int | None:
        """Latest MS1 ``scan_id`` at or before ``rt`` (matching polarity)."""
        if rt is None or not self._ids:
            return None
        i = bisect.bisect_right(self._rts, float(rt))
        while i > 0:
            i -= 1
            if self._ok_pol(i, polarity):
                return self._ids[i]
        return None

    def arrays(
        self, scan_id: int | None
    ) -> tuple[np.ndarray, np.ndarray] | None:
        """``(mz, intensity)`` for one MS1 scan, LRU-cached; ``None`` if absent."""
        if scan_id is None:
            return None
        scan_id = int(scan_id)
        hit = self._arrays.get(scan_id, _MISSING)
        if hit is not _MISSING:
            self._arrays.move_to_end(scan_id)
            return hit
        row = self._con.execute(
            "SELECT mz_array, intensity_array FROM ms1_scans WHERE scan_id = ?",
            (scan_id,),
        ).fetchone()
        val = None if row is None else (self._decode_mz(row[0]), self._decode(row[1]))
        self._arrays[scan_id] = val
        if len(self._arrays) > self._cache_size:
            self._arrays.popitem(last=False)
        return val

    def _decode_mz(self, blob: bytes) -> np.ndarray:
        hit = self._mz_by_blob.get(blob)
        if hit is not None:
            self._mz_by_blob.move_to_end(blob)
            return hit
        arr = self._decode(blob)
        self._mz_by_blob[blob] = arr
        if len(self._mz_by_blob) > 4:
            self._mz_by_blob.popitem(last=False)
        return arr


_MISSING = object()


def resolve_parent(
    index: "SampleScanIndex | sqlite3.Connection",
    ms2_row: dict,
    *,
    match_polarity: bool = True,
    decode: Callable[[bytes], np.ndarray] | None = None,
) -> ResolvedParent:
    """Find the parent MS1 scan of an MS2 scan.

    Parent: ``ms2_scans.parent_scan_id`` when set and present, otherwise the
    latest MS1 at or before the MS2's ``rt`` (same polarity when
    ``match_polarity``).

    Args:
        index: A :class:`SampleScanIndex` for the sample. A raw
            ``sqlite3.Connection`` is also accepted — a throw-away index is
            built from it (fine for one-off calls, wasteful in a loop).
        ms2_row: Mapping with ``scan_id``, ``parent_scan_id``, ``rt`` and
            ``polarity``.
        match_polarity: Restrict MS1 candidates to the MS2's polarity (only
            used when ``index`` is a bare connection).
        decode: Blob decoder (only used when ``index`` is a bare
            connection); defaults to the parser's ``blob_to_array``.
    """
    if isinstance(index, sqlite3.Connection):
        index = SampleScanIndex(
            index, match_polarity=match_polarity, decode=decode
        )

    scan_id = int(ms2_row["scan_id"])
    ms2_rt = ms2_row.get("rt")
    polarity = ms2_row.get("polarity")

    parent_id = ms2_row.get("parent_scan_id")
    parent_rt = index.rt_of(parent_id)
    if parent_id is None or parent_rt is None:
        parent_id = index.parent_before(ms2_rt, polarity)
    parent_arr = index.arrays(parent_id)

    return ResolvedParent(
        ms2_scan_id=scan_id,
        parent_scan_id=parent_id if parent_arr is not None else None,
        parent_mz=parent_arr[0] if parent_arr is not None else None,
        parent_inten=parent_arr[1] if parent_arr is not None else None,
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
    window_lo_mz: float | None
    window_hi_mz: float | None
    precursor_confirmed: bool | None
    precursor_frac: float | None
    precursor_mz_snapped: float | None
    snap_shift_ppm: float | None


def _ref_and_target(ms2_row: dict) -> tuple[float | None, float | None]:
    """``(reference m/z for the precursor peak, window centre)``."""
    prec = ms2_row.get("precursor_mz")
    tgt = ms2_row.get("isolation_window_target")
    ref = float(prec) if prec is not None else (float(tgt) if tgt is not None else None)
    center = float(tgt) if tgt is not None else ref
    return ref, center


def compute_scan_purity(
    ms2_row: dict,
    resolved: ResolvedParent,
    *,
    default_half_width: float,
    confirm_ppm: float = 25.0,
    confirm_min_frac: float = 0.01,
    snap_ppm: float = 15.0,
) -> PurityRow:
    """Turn one MS2 scan + its resolved parent MS1 into a :class:`PurityRow`."""
    sample_id = ms2_row.get("sample_id")
    scan_id = int(ms2_row["scan_id"])
    ref_mz, center = _ref_and_target(ms2_row)

    base = PurityRow(
        sample_id=sample_id,
        ms2_scan_id=scan_id,
        parent_ms1_scan_id=resolved.parent_scan_id,
        window_lo_mz=None,
        window_hi_mz=None,
        precursor_confirmed=None,
        precursor_frac=None,
        precursor_mz_snapped=None,
        snap_shift_ppm=None,
    )
    if center is None or resolved.parent_mz is None:
        return base

    lo, hi = window_bounds(
        center,
        ms2_row.get("isolation_window_lower"),
        ms2_row.get("isolation_window_upper"),
        default_half_width,
    )

    frac = integrate_precursor_fraction(
        resolved.parent_mz, resolved.parent_inten, lo, hi, ref_mz,
        band_ppm=confirm_ppm,
    )
    snapped_mz, snap_shift = snap_precursor_mz(
        resolved.parent_mz, resolved.parent_inten, ref_mz, snap_ppm=snap_ppm
    )

    return PurityRow(
        sample_id=sample_id,
        ms2_scan_id=scan_id,
        parent_ms1_scan_id=resolved.parent_scan_id,
        window_lo_mz=float(lo),
        window_hi_mz=float(hi),
        precursor_confirmed=bool(frac >= confirm_min_frac),
        precursor_frac=float(frac),
        precursor_mz_snapped=snapped_mz,
        snap_shift_ppm=float(snap_shift),
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
        n_confirmed: Rows where the precursor was confirmed present in its
            own parent MS1 (peak-detection-free).
        n_snapped: Rows whose ``precursor_mz`` was moved to a parent-MS1
            local maximum.
    """

    rows: list[PurityRow]
    n_scans: int
    n_confirmed: int
    n_snapped: int


_PURITY_COLS = (
    "sample_id",
    "ms2_scan_id",
    "parent_ms1_scan_id",
    "window_lo_mz",
    "window_hi_mz",
    "precursor_confirmed",
    "precursor_frac",
    "precursor_mz_snapped",
    "snap_shift_ppm",
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
                    r.window_lo_mz,
                    r.window_hi_mz,
                    None if r.precursor_confirmed is None else int(r.precursor_confirmed),
                    r.precursor_frac,
                    r.precursor_mz_snapped,
                    r.snap_shift_ppm,
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
    "FROM ms2_scans ORDER BY scan_id"
)

#: how often (in scans) the per-sample loop logs progress
_PROGRESS_EVERY = 20_000


@dataclass(frozen=True)
class _SampleJob:
    """One sample's scoring unit — every field a scalar, so it pickles cheaply.

    Passed to :func:`_score_one_sample`, which runs either inline (serial
    path) or in a :class:`~concurrent.futures.ProcessPoolExecutor` worker.
    """

    sample_id: int | None
    raw_db_path: str
    default_half: float
    confirm_ppm: float
    confirm_min_frac: float
    snap_ppm: float


def _init_purity_worker() -> None:
    """Quiet a forked worker's inherited handlers.

    Workers inherit the parent's root logger and its file/stream handlers by
    ``fork``; left at DEBUG they would all write the same debug log
    concurrently. The parent logs per-sample progress as futures complete, so
    a worker only needs to surface warnings and errors.
    """
    logging.getLogger().setLevel(logging.WARNING)


def _score_one_sample(
    job: _SampleJob, *, progress: bool = False
) -> tuple[int | None, list[PurityRow]]:
    """Score every MS2 scan of one sample; return ``(sample_id, rows)``.

    Opens the sample's raw database read-only, builds one
    :class:`SampleScanIndex`, and never touches the analysis database — the
    parent persists all rows once, after every sample is in. Safe to call
    inline or as a pool worker.
    """
    from ..parser.mzml_parser import blob_to_array

    rows: list[PurityRow] = []
    with sqlite3.connect(str(job.raw_db_path)) as rcon:
        index = SampleScanIndex(rcon, decode=blob_to_array)
        ms2_rows = rcon.execute(_MS2_QUERY).fetchall()
        n = len(ms2_rows)
        logger.info(
            "precursor purity: sample %s — %d MS2 scans",
            job.sample_id,
            n,
            extra={"source_file": str(job.raw_db_path)},
        )
        for k, r in enumerate(ms2_rows):
            ms2_row = {
                "sample_id": job.sample_id,
                "scan_id": r[0],
                "parent_scan_id": r[1],
                "rt": r[2],
                "polarity": r[3],
                "precursor_mz": r[4],
                "isolation_window_target": r[5],
                "isolation_window_lower": r[6],
                "isolation_window_upper": r[7],
            }
            resolved = resolve_parent(index, ms2_row)
            rows.append(
                compute_scan_purity(
                    ms2_row,
                    resolved,
                    default_half_width=job.default_half,
                    confirm_ppm=job.confirm_ppm,
                    confirm_min_frac=job.confirm_min_frac,
                    snap_ppm=job.snap_ppm,
                )
            )
            if progress and (k + 1) % _PROGRESS_EVERY == 0:
                logger.info(
                    "precursor purity: sample %s — %d/%d scans scored",
                    job.sample_id,
                    k + 1,
                    n,
                    extra={"source_file": str(job.raw_db_path)},
                )
    return job.sample_id, rows


def _make_result(rows: list[PurityRow]) -> PurityResult:
    return PurityResult(
        rows=rows,
        n_scans=len(rows),
        n_confirmed=sum(bool(r.precursor_confirmed) for r in rows),
        n_snapped=sum(bool(r.snap_shift_ppm) for r in rows),
    )


def _resolve_n_workers(
    config: "PurityConfig", override: int | None, n_samples: int
) -> int:
    """Clamp the requested worker count to ``[1, n_samples]``.

    ``override`` wins over ``config.n_workers``; ``None`` / ``0`` / negative
    means "one per CPU". One sample is always scored inline.
    """
    want = override if override is not None else getattr(config, "n_workers", None)
    if not want or int(want) < 1:
        want = os.cpu_count() or 1
    return max(1, min(int(want), max(1, n_samples)))


@log_call(source="analysis_db_path")
def run_precursor_purity(
    analysis_db_path: Path | str,
    config: "PurityConfig",
    *,
    command_id: int | None = None,
    n_workers: int | None = None,
) -> PurityResult:
    """Score every sample's MS2 scans for precursor purity and persist them.

    Samples are independent — each opens its own read-only raw database and
    builds its own in-memory scan index — so they are scored **one process
    per sample** (``concurrent.futures.ProcessPoolExecutor``). The analysis
    database is written once, in the parent, after every sample is in; a
    single sample (or ``n_workers == 1``) takes the serial path.

    Expects ``samples`` already populated in the analysis database.

    Args:
        analysis_db_path: The analysis database.
        config: Anything exposing the :class:`~msianalyzer.core.config.config.PurityConfig`
            fields (``default_half_window_da``, ``precursor_confirm_ppm``,
            ``precursor_confirm_min_frac``, ``precursor_snap_ppm``,
            ``n_workers``).
        command_id: Optional ``commands.id`` stamped on every row.
        n_workers: Overrides ``config.n_workers``. ``None`` / ``0`` uses
            ``os.cpu_count()``; ``1`` forces the serial path. Capped at the
            sample count.
    """
    analysis_db_path = Path(analysis_db_path)
    with sqlite3.connect(analysis_db_path) as con:
        samples = con.execute(
            "SELECT sample_id, raw_db_path FROM samples"
        ).fetchall()

    jobs = [
        _SampleJob(
            sample_id=sample_id,
            raw_db_path=str(raw_db_path),
            default_half=float(config.default_half_window_da),
            confirm_ppm=float(getattr(config, "precursor_confirm_ppm", 25.0)),
            confirm_min_frac=float(getattr(config, "precursor_confirm_min_frac", 0.01)),
            snap_ppm=float(getattr(config, "precursor_snap_ppm", 15.0)),
        )
        for sample_id, raw_db_path in samples
    ]

    workers = _resolve_n_workers(config, n_workers, len(jobs))
    rows: list[PurityRow] = []

    if workers == 1 or len(jobs) <= 1:
        for job in jobs:
            _, sample_rows = _score_one_sample(job, progress=True)
            rows.extend(sample_rows)
            logger.info(
                "precursor purity: sample %s done — %d scans",
                job.sample_id,
                len(sample_rows),
            )
    else:
        logger.info(
            "precursor purity: scoring %d samples on %d worker processes",
            len(jobs),
            workers,
        )
        done = 0
        with ProcessPoolExecutor(
            max_workers=workers, initializer=_init_purity_worker
        ) as executor:
            futures = {executor.submit(_score_one_sample, job): job for job in jobs}
            for future in as_completed(futures):
                sample_id, sample_rows = future.result()
                rows.extend(sample_rows)
                done += 1
                logger.info(
                    "precursor purity: sample %s done — %d scans (%d/%d samples)",
                    sample_id,
                    len(sample_rows),
                    done,
                    len(jobs),
                )

    result = _make_result(rows)
    persist_purity(analysis_db_path, result, command_id=command_id)
    logger.info(
        "precursor purity: %d scans (%d precursor-confirmed in MS1, %d snapped)",
        result.n_scans,
        result.n_confirmed,
        result.n_snapped,
    )
    return result
