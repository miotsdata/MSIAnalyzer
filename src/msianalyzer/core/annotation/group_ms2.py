"""Stage A of MS2 annotation: associate each MS2 scan with a master feature.

The "grouper" snaps every MS2 scan to a feature from the aligned
cross-sample m/z list (``features`` table). It does *not* filter scans — a
scan that matches nothing, or whose fragmentation clearly failed, is kept
and flagged so the counts stay visible downstream.

Three distinct tolerances are involved and must not be conflated:

* ``align_ppm`` — MS1<->MS1 tolerance used once by
  ``align_mz_across_samples`` to decide feature identity. It also sets each
  feature's internal width.
* ``assoc_ppm`` — MS2 ``precursor_mz`` <-> feature acceptance tolerance,
  used here. It must cover the feature's own width *plus* the extra
  measurement / calibration slack of a single survey-scan precursor, so
  ``assoc_ppm >= align_ppm`` always (a warning is emitted otherwise).
* the isolation window ``[target - lower, target + upper]`` — physical, in
  Da, read from the mzML. Used only to enumerate the features that could
  have co-fragmented into a scan (the chimera count), never as the matcher.

Outputs (schema in :func:`msianalyzer.core.analysis_db.create_analysis_schema`):

* ``ms2_associations`` — one row per MS2 scan (the primary pick + flags).
* ``ms2_window_features`` — one row per (scan, feature inside its isolation
  window); ``is_primary`` marks the chosen one. Always populated: a clean
  single match is one row.
* ``feature_ms2_summary`` — one row per feature: how many MS2 hit it, how
  many across samples, and how many of those show no real fragmentation.
"""

from __future__ import annotations

import logging
import sqlite3
import warnings
from collections import defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

from ..analysis_db import create_analysis_schema
from ..utils.db import safe_execute, safe_executemany
from ..utils.logging_utils import log_call

logger = logging.getLogger(__name__)

__all__ = [
    "WindowFeature",
    "ScanAssociation",
    "FeatureMs2Summary",
    "GroupingResult",
    "ppm_between",
    "detect_precursor_only",
    "detect_flat_fragmentation",
    "associate_scan",
    "group_ms2",
    "summarize_features",
    "persist_grouping",
    "run_grouper",
]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def ppm_between(mz: float, ref: float) -> float:
    """Signed ppm of ``mz`` relative to ``ref`` (positive => ``mz`` is higher)."""
    return (mz - ref) / ref * 1e6


def detect_precursor_only(
    mz_array: Sequence[float] | np.ndarray | None,
    intensity_array: Sequence[float] | np.ndarray | None,
    precursor_mz: float | None,
    *,
    tic_frac: float = 0.8,
    mz_tol_da: float = 2.0,
) -> bool:
    """True when an MS2 spectrum is just the surviving precursor.

    The real "fragmentation did not occur" signal is not a low peak count
    (noise inflates that) but that the base peak sits on the precursor and
    carries essentially all of the signal.

    Args:
        mz_array: Fragment m/z values (decoded, not a blob).
        intensity_array: Matching intensities.
        precursor_mz: The scan's precursor m/z (``None`` -> returns False).
        tic_frac: Minimum fraction of total intensity that must fall within
            ``mz_tol_da`` of ``precursor_mz``.
        mz_tol_da: Half-width (Da) of the "on the precursor" band.
    """
    if precursor_mz is None or mz_array is None or intensity_array is None:
        return False
    mz = np.asarray(mz_array, dtype=float)
    inten = np.asarray(intensity_array, dtype=float)
    if mz.size == 0 or inten.size == 0:
        return False
    total = float(inten.sum())
    if total <= 0.0:
        return False
    base_mz = float(mz[int(np.argmax(inten))])
    if abs(base_mz - precursor_mz) > mz_tol_da:
        return False
    near = np.abs(mz - precursor_mz) <= mz_tol_da
    return float(inten[near].sum()) / total >= tic_frac


def detect_flat_fragmentation(
    mz_array: Sequence[float] | np.ndarray | None,
    intensity_array: Sequence[float] | np.ndarray | None,
    *,
    min_peaks: int = 3,
    cv_threshold: float = 0.2,
    min_rel_intensity: float = 0.01,
) -> bool:
    """True when a spectrum looks like a "comb" rather than real fragmentation.

    Real CID/HCD fragmentation decays: one or a few dominant fragments, many
    minor ones — high dispersion in peak height. Several peaks at different
    m/z but near-identical height is more consistent with chemical/
    electronic noise or an isobaric co-isolation smear than genuine
    fragments. Measured as the coefficient of variation
    (``std(intensity) / mean(intensity)``) of the peaks surviving a noise
    floor: low CV => flat => flagged.

    A soft QC signal, not a filter — flagged scans are still scored/stored
    (mirrors :func:`detect_precursor_only`).

    Args:
        mz_array: Fragment m/z values (decoded, not a blob). Unused beyond
            sizing/filtering alongside ``intensity_array``; kept for
            symmetry with :func:`detect_precursor_only` and so a future
            m/z-spread refinement can use it.
        intensity_array: Matching intensities.
        min_peaks: Peaks surviving ``min_rel_intensity`` must be at least
            this many before the test applies; below it CV is too noisy a
            signal, so the scan is left unflagged.
        cv_threshold: Flag when CV ``<=`` this value, in ``[0, 1]``-ish
            (unbounded above, but a flat comb sits well under 1).
        min_rel_intensity: Peaks below this fraction of the base peak are
            dropped before counting and computing CV.
    """
    if mz_array is None or intensity_array is None:
        return False
    inten = np.asarray(intensity_array, dtype=float)
    if inten.size == 0:
        return False
    base = inten.max()
    if base <= 0.0:
        return False
    kept = inten[inten >= base * min_rel_intensity]
    if kept.size < min_peaks:
        return False
    mean = float(kept.mean())
    if mean <= 0.0:
        return False
    cv = float(kept.std()) / mean
    return cv <= cv_threshold


# ---------------------------------------------------------------------------
# result containers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WindowFeature:
    """One master feature lying inside a scan's isolation window."""

    feature_id: int
    feature_mz: float
    ppm_diff: float | None  # signed ppm of the match value vs this feature
    within_tol: bool  # |ppm_diff| <= assoc_ppm
    is_primary: bool  # chosen as the scan's association


@dataclass(frozen=True)
class ScanAssociation:
    """The association of a single MS2 scan (one ``ms2_associations`` row)."""

    scan_id: int
    sample_id: int | None
    feature_id: int | None  # None => unassigned
    feature_mz: float | None
    match_key: str  # "precursor_mz" | "isolation_window_target" | "none"
    precursor_mz: float | None
    isolation_window_target: float | None
    isolation_window_lower: float | None
    isolation_window_upper: float | None
    ppm_offset: float | None  # signed, match value vs chosen feature
    n_features_in_window: int
    nearest_other_feature_ppm: float | None
    precursor_target_delta_ppm: float | None
    rt: float | None
    collision_energy: float | None
    n_peaks: int
    polarity: str | None
    precursor_only: bool
    flat_fragmentation: bool
    window_features: list[WindowFeature] = field(default_factory=list)


@dataclass(frozen=True)
class FeatureMs2Summary:
    """MS2 coverage of one feature (one ``feature_ms2_summary`` row)."""

    feature_id: int
    feature_mz: float
    n_ms2: int
    n_samples: int
    n_precursor_only: int
    n_single_peak: int
    n_chimeric: int
    n_flat_fragmentation: int
    median_n_peaks: float


@dataclass(frozen=True)
class GroupingResult:
    associations: list[ScanAssociation]
    feature_summary: list[FeatureMs2Summary]


# ---------------------------------------------------------------------------
# core association logic
# ---------------------------------------------------------------------------


def _match_value(scan: dict) -> tuple[float | None, str]:
    """Pick the m/z to match on and name the key it came from."""
    prec = scan.get("precursor_mz")
    if prec is not None:
        return float(prec), "precursor_mz"
    tgt = scan.get("isolation_window_target")
    if tgt is not None:
        return float(tgt), "isolation_window_target"
    return None, "none"


def associate_scan(
    scan: dict,
    features: np.ndarray,
    feature_ids: np.ndarray | None = None,
    *,
    assoc_ppm: float,
    default_isolation_half_width: float = 0.5,
    precursor_only_tic_frac: float = 0.8,
    precursor_only_mz_tol_da: float = 2.0,
    flat_fragmentation_min_peaks: int = 3,
    flat_fragmentation_cv_threshold: float = 0.2,
    flat_fragmentation_min_rel_intensity: float = 0.01,
    sample_id: int | None = None,
) -> ScanAssociation:
    """Associate one MS2 scan with a master feature.

    Args:
        scan: A dict with the ``ms2_scans`` fields plus decoded
            ``mz_array`` / ``intensity_array``. Missing keys are tolerated.
        features: Sorted 1-D array of master feature m/z.
        feature_ids: Ids parallel to ``features``. Defaults to
            ``range(len(features))``.
        assoc_ppm: Acceptance tolerance for the precursor<->feature match.
        default_isolation_half_width: Used as ``lower``/``upper`` when the
            scan does not carry isolation offsets.
        precursor_only_tic_frac: Passed to :func:`detect_precursor_only`.
        precursor_only_mz_tol_da: Passed to :func:`detect_precursor_only`.
        flat_fragmentation_min_peaks: Passed to
            :func:`detect_flat_fragmentation`.
        flat_fragmentation_cv_threshold: Passed to
            :func:`detect_flat_fragmentation`.
        flat_fragmentation_min_rel_intensity: Passed to
            :func:`detect_flat_fragmentation`.
        sample_id: Overrides ``scan["sample_id"]`` when given.

    Returns:
        A :class:`ScanAssociation`.
    """
    features = np.asarray(features, dtype=float)
    if feature_ids is None:
        feature_ids = np.arange(features.size)
    feature_ids = np.asarray(feature_ids)

    match_val, match_key = _match_value(scan)
    prec = scan.get("precursor_mz")
    tgt = scan.get("isolation_window_target")
    lower = scan.get("isolation_window_lower")
    upper = scan.get("isolation_window_upper")

    # --- isolation window -> candidate features -------------------------
    center = tgt if tgt is not None else match_val
    lo_off = float(lower) if lower is not None else default_isolation_half_width
    up_off = float(upper) if upper is not None else default_isolation_half_width
    if center is not None:
        in_win = (features >= center - lo_off) & (features <= center + up_off)
    else:
        in_win = np.zeros(features.size, dtype=bool)

    window_features: list[WindowFeature] = []
    for fid, fmz in zip(feature_ids[in_win], features[in_win]):
        d = ppm_between(match_val, float(fmz)) if match_val is not None else None
        window_features.append(
            WindowFeature(
                feature_id=int(fid),
                feature_mz=float(fmz),
                ppm_diff=d,
                within_tol=d is not None and abs(d) <= assoc_ppm,
                is_primary=False,
            )
        )

    # --- primary pick: nearest in-window feature within tolerance -------
    feature_id = feature_mz = ppm_offset = None
    candidates = [w for w in window_features if w.within_tol]
    if candidates:
        best = min(candidates, key=lambda w: abs(w.ppm_diff))
        window_features = [
            replace(w, is_primary=(w.feature_id == best.feature_id))
            for w in window_features
        ]
        feature_id = best.feature_id
        feature_mz = best.feature_mz
        ppm_offset = best.ppm_diff

    # --- flags --------------------------------------------------------
    nearest_other = None
    if match_val is not None and features.size:
        other = features[feature_ids != feature_id] if feature_id is not None else features
        if other.size:
            nearest_other = ppm_between(
                match_val, float(other[np.argmin(np.abs(other - match_val))])
            )

    prec_tgt_delta = (
        ppm_between(float(prec), float(tgt))
        if prec is not None and tgt is not None
        else None
    )

    n_peaks = scan.get("n_peaks")
    if n_peaks is None:
        mz_arr = scan.get("mz_array")
        n_peaks = 0 if mz_arr is None else int(np.asarray(mz_arr).size)

    precursor_only = detect_precursor_only(
        scan.get("mz_array"),
        scan.get("intensity_array"),
        prec,
        tic_frac=precursor_only_tic_frac,
        mz_tol_da=precursor_only_mz_tol_da,
    )

    flat_fragmentation = detect_flat_fragmentation(
        scan.get("mz_array"),
        scan.get("intensity_array"),
        min_peaks=flat_fragmentation_min_peaks,
        cv_threshold=flat_fragmentation_cv_threshold,
        min_rel_intensity=flat_fragmentation_min_rel_intensity,
    )

    return ScanAssociation(
        scan_id=int(scan["scan_id"]),
        sample_id=sample_id if sample_id is not None else scan.get("sample_id"),
        feature_id=feature_id,
        feature_mz=feature_mz,
        match_key=match_key,
        precursor_mz=None if prec is None else float(prec),
        isolation_window_target=None if tgt is None else float(tgt),
        isolation_window_lower=None if lower is None else float(lower),
        isolation_window_upper=None if upper is None else float(upper),
        ppm_offset=ppm_offset,
        n_features_in_window=len(window_features),
        nearest_other_feature_ppm=nearest_other,
        precursor_target_delta_ppm=prec_tgt_delta,
        rt=scan.get("rt"),
        collision_energy=scan.get("collision_energy"),
        n_peaks=int(n_peaks),
        polarity=scan.get("polarity"),
        precursor_only=precursor_only,
        flat_fragmentation=flat_fragmentation,
        window_features=window_features,
    )


@log_call
def summarize_features(
    associations: Iterable[ScanAssociation],
) -> list[FeatureMs2Summary]:
    """Roll ``ms2_associations`` up to one row per feature."""
    by_feat: dict[int, list[ScanAssociation]] = defaultdict(list)
    for a in associations:
        if a.feature_id is not None:
            by_feat[a.feature_id].append(a)

    out: list[FeatureMs2Summary] = []
    for fid, rows in sorted(by_feat.items()):
        n_peaks = [r.n_peaks for r in rows]
        out.append(
            FeatureMs2Summary(
                feature_id=fid,
                feature_mz=rows[0].feature_mz,
                n_ms2=len(rows),
                n_samples=len({r.sample_id for r in rows}),
                n_precursor_only=sum(r.precursor_only for r in rows),
                n_single_peak=sum(r.n_peaks <= 1 for r in rows),
                n_chimeric=sum(r.n_features_in_window > 1 for r in rows),
                n_flat_fragmentation=sum(r.flat_fragmentation for r in rows),
                median_n_peaks=float(np.median(n_peaks)) if n_peaks else 0.0,
            )
        )
    return out


@log_call
def group_ms2(
    scans: Iterable[dict],
    features: np.ndarray,
    feature_ids: np.ndarray | None = None,
    *,
    assoc_ppm: float,
    align_ppm: float | None = None,
    include_unmatched: bool = True,
    default_isolation_half_width: float = 0.5,
    precursor_only_tic_frac: float = 0.8,
    precursor_only_mz_tol_da: float = 2.0,
    flat_fragmentation_min_peaks: int = 3,
    flat_fragmentation_cv_threshold: float = 0.2,
    flat_fragmentation_min_rel_intensity: float = 0.01,
) -> GroupingResult:
    """Associate a batch of MS2 scans and summarise per feature.

    Args:
        scans: Iterable of scan dicts (see :func:`associate_scan`).
        features: Sorted 1-D array of master feature m/z.
        feature_ids: Ids parallel to ``features``; defaults to the range.
        assoc_ppm: Precursor<->feature acceptance tolerance.
        align_ppm: If given and ``assoc_ppm < align_ppm``, a ``UserWarning``
            is emitted — the association tolerance should never be tighter
            than the tolerance the features were aligned with.
        include_unmatched: Keep scans that matched no feature (with
            ``feature_id`` None). When False they are dropped from the
            result.
    """
    features = np.asarray(features, dtype=float)
    if feature_ids is None:
        feature_ids = np.arange(features.size)
    feature_ids = np.asarray(feature_ids)

    if align_ppm is not None and assoc_ppm < align_ppm:
        warnings.warn(
            f"assoc_ppm ({assoc_ppm}) < align_ppm ({align_ppm}): the MS2 "
            "association tolerance is tighter than the feature alignment "
            "tolerance; real precursors may fail to match their feature.",
            UserWarning,
            stacklevel=2,
        )

    associations: list[ScanAssociation] = []
    for scan in scans:
        a = associate_scan(
            scan,
            features,
            feature_ids,
            assoc_ppm=assoc_ppm,
            default_isolation_half_width=default_isolation_half_width,
            precursor_only_tic_frac=precursor_only_tic_frac,
            precursor_only_mz_tol_da=precursor_only_mz_tol_da,
            flat_fragmentation_min_peaks=flat_fragmentation_min_peaks,
            flat_fragmentation_cv_threshold=flat_fragmentation_cv_threshold,
            flat_fragmentation_min_rel_intensity=flat_fragmentation_min_rel_intensity,
        )
        if a.feature_id is None and not include_unmatched:
            continue
        associations.append(a)

    return GroupingResult(
        associations=associations,
        feature_summary=summarize_features(associations),
    )


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------

_ASSOC_COLS = (
    "sample_id",
    "scan_id",
    "feature_id",
    "match_key",
    "precursor_mz",
    "isolation_window_target",
    "isolation_window_lower",
    "isolation_window_upper",
    "ppm_offset",
    "n_features_in_window",
    "nearest_other_feature_ppm",
    "precursor_target_delta_ppm",
    "rt",
    "collision_energy",
    "n_peaks",
    "polarity",
    "precursor_only",
    "flat_fragmentation",
    "command_id",
)


@log_call(source="db_path")
def persist_grouping(
    db_path: Path | str,
    result: GroupingResult,
    *,
    command_id: int | None = None,
    replace_existing: bool = True,
) -> None:
    """Write a :class:`GroupingResult` into the analysis database.

    Args:
        db_path: Path to the analysis database.
        result: The grouping to store.
        command_id: Optional ``commands.id`` stamped on every association.
        replace_existing: Clear the three grouper tables first (a re-run
            replaces the association without touching ``features`` /
            ``samples``).
    """
    with sqlite3.connect(Path(db_path)) as con:
        con.execute("PRAGMA foreign_keys = ON")
        create_analysis_schema(con)  # idempotent; grouper tables live here
        if replace_existing:
            con.execute("DELETE FROM ms2_window_features")
            con.execute("DELETE FROM ms2_associations")
            con.execute("DELETE FROM feature_ms2_summary")

        placeholders = ", ".join("?" * len(_ASSOC_COLS))
        insert_assoc = (
            f"INSERT INTO ms2_associations ({', '.join(_ASSOC_COLS)}) "
            f"VALUES ({placeholders})"
        )
        for a in result.associations:
            cur = safe_execute(
                con,
                insert_assoc,
                (
                    a.sample_id,
                    a.scan_id,
                    a.feature_id,
                    a.match_key,
                    a.precursor_mz,
                    a.isolation_window_target,
                    a.isolation_window_lower,
                    a.isolation_window_upper,
                    a.ppm_offset,
                    a.n_features_in_window,
                    a.nearest_other_feature_ppm,
                    a.precursor_target_delta_ppm,
                    a.rt,
                    a.collision_energy,
                    a.n_peaks,
                    a.polarity,
                    int(a.precursor_only),
                    int(a.flat_fragmentation),
                    command_id,
                ),
                table="ms2_associations",
                logger=logger,
                source=db_path,
            )
            assoc_id = cur.lastrowid
            if a.window_features:
                safe_executemany(
                    con,
                    "INSERT INTO ms2_window_features "
                    "(association_id, feature_id, feature_mz, ppm_diff, "
                    " within_tol, is_primary) VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (
                            assoc_id,
                            w.feature_id,
                            w.feature_mz,
                            w.ppm_diff,
                            int(w.within_tol),
                            int(w.is_primary),
                        )
                        for w in a.window_features
                    ],
                    table="ms2_window_features",
                    logger=logger,
                    source=db_path,
                )

        safe_executemany(
            con,
            "INSERT INTO feature_ms2_summary "
            "(feature_id, feature_mz, n_ms2, n_samples, n_precursor_only, "
            " n_single_peak, n_chimeric, n_flat_fragmentation, median_n_peaks) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    s.feature_id,
                    s.feature_mz,
                    s.n_ms2,
                    s.n_samples,
                    s.n_precursor_only,
                    s.n_single_peak,
                    s.n_chimeric,
                    s.n_flat_fragmentation,
                    s.median_n_peaks,
                )
                for s in result.feature_summary
            ],
            table="feature_ms2_summary",
            logger=logger,
            source=db_path,
        )
        con.commit()


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


@log_call(source="analysis_db_path")
def run_grouper(
    analysis_db_path: Path | str,
    *,
    assoc_ppm: float,
    align_ppm: float | None = None,
    include_unmatched: bool = True,
    command_id: int | None = None,
    **group_kwargs,
) -> GroupingResult:
    """Read features + every sample's raw MS2 scans, group, and persist.

    Expects ``features`` and ``samples`` already populated in the analysis
    database. Raw databases are opened read-only.
    """
    from ..parser.mzml_parser import blob_to_array

    analysis_db_path = Path(analysis_db_path)
    logger.info("grouper: reading features + samples from %s", analysis_db_path)
    with sqlite3.connect(analysis_db_path) as con:
        feats = con.execute(
            "SELECT feature_id, mz FROM features ORDER BY mz"
        ).fetchall()
        samples = con.execute(
            "SELECT sample_id, raw_db_path FROM samples"
        ).fetchall()

    feature_ids = np.array([r[0] for r in feats], dtype=int)
    features = np.array([r[1] for r in feats], dtype=float)

    scans: list[dict] = []
    for sample_id, raw_db_path in samples:
        # opened for reading only — the grouper never writes a raw database
        with sqlite3.connect(str(raw_db_path)) as rcon:
            rows = rcon.execute(
                "SELECT scan_id, precursor_mz, isolation_window_target, "
                "isolation_window_lower, isolation_window_upper, rt, "
                "collision_energy, n_peaks, polarity, mz_array, intensity_array "
                "FROM ms2_scans"
            ).fetchall()
        logger.info(
            "grouper: sample %s — %d MS2 scans",
            sample_id,
            len(rows),
            extra={"source_file": str(raw_db_path)},
        )
        for r in rows:
            scans.append(
                {
                    "sample_id": sample_id,
                    "scan_id": r[0],
                    "precursor_mz": r[1],
                    "isolation_window_target": r[2],
                    "isolation_window_lower": r[3],
                    "isolation_window_upper": r[4],
                    "rt": r[5],
                    "collision_energy": r[6],
                    "n_peaks": r[7],
                    "polarity": r[8],
                    "mz_array": blob_to_array(r[9]),
                    "intensity_array": blob_to_array(r[10]),
                }
            )

    logger.info(
        "grouper: associating %d MS2 scans against %d features",
        len(scans),
        len(features),
    )
    result = group_ms2(
        scans,
        features,
        feature_ids,
        assoc_ppm=assoc_ppm,
        align_ppm=align_ppm,
        include_unmatched=include_unmatched,
        **group_kwargs,
    )
    persist_grouping(analysis_db_path, result, command_id=command_id)
    logger.info(
        "grouper: persisted %d associations, %d feature summaries",
        len(result.associations),
        len(result.feature_summary),
    )
    return result
