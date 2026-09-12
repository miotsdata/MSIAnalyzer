"""Stage A'' of MS2 annotation: pick one representative MS2 scan per feature.

A feature is often fragmented several times — across samples, across pixels,
with varying isolation cleanliness and fragment richness. Downstream work
(a library submission, a figure, a manual check) usually wants **one**
spectrum per feature. This stage folds three per-scan signals into a single
``consensus_score`` and records the winner:

* the best library score for the scan (``ms2_annotations.score`` at
  ``rank_ms2 = 1``) when Stage B ran — otherwise treated as ``1.0`` so the
  pick still works library-free;
* the precursor purity (``precursor_frac``) from the purity stage
  (Stage A′), or ``config.neutral_precursor_frac`` when the scan could not
  be scored;
* a peak-richness term ``min(1, n_peaks / target_peaks)`` on the scan's
  fragment count.

``consensus_score = best_score x precursor_frac_term x peak_term``. The
stage never drops a feature's scans from the counts, only from the *pick*
(via ``min_precursor_frac``). One row per MS2-bearing feature in
``feature_ms2_consensus`` (schema in
:func:`msianalyzer.core.analysis_db.create_analysis_schema`); a re-run
replaces every row.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Sequence

from ..analysis_db import create_analysis_schema
from ..utils.db import safe_executemany
from ..utils.logging_utils import log_call

if TYPE_CHECKING:  # avoid importing the config package at module load
    from ..config.config import ConsensusConfig

logger = logging.getLogger(__name__)

__all__ = [
    "ScanStat",
    "ConsensusRow",
    "ConsensusResult",
    "peak_term",
    "precursor_frac_term",
    "consensus_score",
    "pick_feature",
    "build_consensus",
    "persist_consensus",
    "run_consensus",
]


# ---------------------------------------------------------------------------
# containers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScanStat:
    """Per-scan inputs to the consensus pick for one feature.

    Attributes:
        sample_id / scan_id: the MS2 scan.
        precursor_frac: precursor-ion purity, or ``None`` when unscored.
        n_peaks: fragment-peak count of the scan.
        best_score: best ``ms2_annotations.score`` for the scan, or ``None``
            when annotation did not run / produced nothing.
        best_compound_name / best_inchikey: the ``rank_ms2 = 1`` candidate's
            identity, when available.
    """

    sample_id: int | None
    scan_id: int
    precursor_frac: float | None
    n_peaks: int
    best_score: float | None
    best_compound_name: str | None = None
    best_inchikey: str | None = None


@dataclass(frozen=True)
class ConsensusRow:
    """One ``feature_ms2_consensus`` row."""

    feature_id: int
    feature_mz: float
    best_sample_id: int | None
    best_scan_id: int
    n_ms2: int
    n_ms2_considered: int
    n_ms2_scored: int
    consensus_score: float
    precursor_frac: float | None
    n_peaks: int
    best_annotation_score: float | None
    best_compound_name: str | None
    best_inchikey: str | None


@dataclass(frozen=True)
class ConsensusResult:
    """Return value of :func:`run_consensus`."""

    rows: list[ConsensusRow]
    n_features: int
    n_features_scored: int


# ---------------------------------------------------------------------------
# pure scoring
# ---------------------------------------------------------------------------


def peak_term(n_peaks: int, target_peaks: int) -> float:
    """Fragment-richness term: ``min(1, n_peaks / target_peaks)`` in ``[0, 1]``."""
    if target_peaks <= 0:
        return 1.0
    return float(min(1.0, max(0.0, n_peaks / target_peaks)))


def precursor_frac_term(precursor_frac: float | None, neutral: float) -> float:
    """Precursor-purity term, clamped to ``[0, 1]``; ``neutral`` stands in for ``None``."""
    v = neutral if precursor_frac is None else precursor_frac
    return float(min(1.0, max(0.0, v)))


def consensus_score(
    stat: ScanStat, *, target_peaks: int, neutral_precursor_frac: float
) -> float:
    """``best_score x precursor_frac_term x peak_term`` (best_score ``1.0`` when absent)."""
    base = 1.0 if stat.best_score is None else float(stat.best_score)
    return (
        base
        * precursor_frac_term(stat.precursor_frac, neutral_precursor_frac)
        * peak_term(stat.n_peaks, target_peaks)
    )


def pick_feature(
    feature_id: int,
    feature_mz: float,
    stats: Sequence[ScanStat],
    *,
    target_peaks: int,
    neutral_precursor_frac: float,
    min_precursor_frac: float | None,
) -> ConsensusRow | None:
    """Choose the best scan for one feature, or ``None`` when none qualifies.

    ``min_precursor_frac`` (when set) drops scans with a *known*
    ``precursor_frac`` below it from the pick; they still count in
    ``n_ms2``. Ties break on the annotation score, then the peak count,
    then the lower ``scan_id``.
    """
    stats = list(stats)
    if not stats:
        return None
    considered = [
        s
        for s in stats
        if min_precursor_frac is None
        or s.precursor_frac is None
        or s.precursor_frac >= min_precursor_frac
    ]
    if not considered:
        return None

    def key(s: ScanStat) -> tuple:
        return (
            consensus_score(
                s, target_peaks=target_peaks, neutral_precursor_frac=neutral_precursor_frac
            ),
            -1.0 if s.best_score is None else s.best_score,
            s.n_peaks,
            -s.scan_id,
        )

    best = max(considered, key=key)
    return ConsensusRow(
        feature_id=feature_id,
        feature_mz=float(feature_mz),
        best_sample_id=best.sample_id,
        best_scan_id=best.scan_id,
        n_ms2=len(stats),
        n_ms2_considered=len(considered),
        n_ms2_scored=sum(s.best_score is not None for s in considered),
        consensus_score=consensus_score(
            best, target_peaks=target_peaks, neutral_precursor_frac=neutral_precursor_frac
        ),
        precursor_frac=best.precursor_frac,
        n_peaks=best.n_peaks,
        best_annotation_score=best.best_score,
        best_compound_name=best.best_compound_name,
        best_inchikey=best.best_inchikey,
    )


@log_call
def build_consensus(
    features: Iterable[tuple[int, float]],
    scans_by_feature: dict[int, Sequence[ScanStat]],
    *,
    target_peaks: int,
    neutral_precursor_frac: float,
    min_precursor_frac: float | None,
) -> ConsensusResult:
    """Run :func:`pick_feature` over every feature and tally the result."""
    rows: list[ConsensusRow] = []
    for feature_id, feature_mz in features:
        row = pick_feature(
            feature_id,
            feature_mz,
            scans_by_feature.get(feature_id, ()),
            target_peaks=target_peaks,
            neutral_precursor_frac=neutral_precursor_frac,
            min_precursor_frac=min_precursor_frac,
        )
        if row is not None:
            rows.append(row)
    return ConsensusResult(
        rows=rows,
        n_features=len(rows),
        n_features_scored=sum(r.n_ms2_scored > 0 for r in rows),
    )


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------

_CONSENSUS_COLS = (
    "feature_id",
    "feature_mz",
    "best_sample_id",
    "best_scan_id",
    "n_ms2",
    "n_ms2_considered",
    "n_ms2_scored",
    "consensus_score",
    "precursor_frac",
    "n_peaks",
    "best_annotation_score",
    "best_compound_name",
    "best_inchikey",
    "command_id",
)


@log_call(source="db_path")
def persist_consensus(
    db_path: Path | str,
    result: ConsensusResult,
    *,
    command_id: int | None = None,
    replace_existing: bool = True,
) -> None:
    """Write a :class:`ConsensusResult` into ``feature_ms2_consensus``."""
    with sqlite3.connect(Path(db_path)) as con:
        con.execute("PRAGMA foreign_keys = ON")
        create_analysis_schema(con)
        if replace_existing:
            con.execute("DELETE FROM feature_ms2_consensus")
        placeholders = ", ".join("?" * len(_CONSENSUS_COLS))
        safe_executemany(
            con,
            f"INSERT INTO feature_ms2_consensus ({', '.join(_CONSENSUS_COLS)}) "
            f"VALUES ({placeholders})",
            [
                (
                    r.feature_id,
                    r.feature_mz,
                    r.best_sample_id,
                    r.best_scan_id,
                    r.n_ms2,
                    r.n_ms2_considered,
                    r.n_ms2_scored,
                    r.consensus_score,
                    r.precursor_frac,
                    r.n_peaks,
                    r.best_annotation_score,
                    r.best_compound_name,
                    r.best_inchikey,
                    command_id,
                )
                for r in result.rows
            ],
            table="feature_ms2_consensus",
            logger=logger,
            source=db_path,
        )
        con.commit()


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


@log_call(source="analysis_db_path")
def run_consensus(
    analysis_db_path: Path | str,
    config: "ConsensusConfig",
    *,
    command_id: int | None = None,
) -> ConsensusResult:
    """Read associations + purity + annotations, pick one scan per feature.

    Expects ``ms2_associations`` populated (grouper has run); ``precursor_purity``
    and ``ms2_annotations`` are used when present but not required.
    """
    analysis_db_path = Path(analysis_db_path)
    target_peaks = int(config.target_peaks)
    neutral_precursor_frac = float(config.neutral_precursor_frac)
    min_precursor_frac = config.min_precursor_frac

    with sqlite3.connect(analysis_db_path) as con:
        features = con.execute(
            "SELECT DISTINCT f.feature_id, f.mz FROM features f "
            "JOIN ms2_associations a ON a.feature_id = f.feature_id "
            "ORDER BY f.mz"
        ).fetchall()
        assoc = con.execute(
            "SELECT feature_id, sample_id, scan_id, n_peaks "
            "FROM ms2_associations WHERE feature_id IS NOT NULL"
        ).fetchall()
        purity_rows = con.execute(
            "SELECT sample_id, ms2_scan_id, precursor_frac FROM precursor_purity"
        ).fetchall()
        ann_rows = con.execute(
            "SELECT sample_id, scan_id, score, compound_name, inchikey "
            "FROM ms2_annotations WHERE rank_ms2 = 1"
        ).fetchall()

    frac_by_scan = {(s, sc): f for s, sc, f in purity_rows}
    ann_by_scan = {
        (s, sc): (score, name, key) for s, sc, score, name, key in ann_rows
    }

    scans_by_feature: dict[int, list[ScanStat]] = {}
    for feature_id, sample_id, scan_id, n_peaks in assoc:
        score, name, key = ann_by_scan.get((sample_id, scan_id), (None, None, None))
        scans_by_feature.setdefault(feature_id, []).append(
            ScanStat(
                sample_id=sample_id,
                scan_id=scan_id,
                precursor_frac=frac_by_scan.get((sample_id, scan_id)),
                n_peaks=int(n_peaks or 0),
                best_score=score,
                best_compound_name=name,
                best_inchikey=key,
            )
        )

    result = build_consensus(
        features,
        scans_by_feature,
        target_peaks=target_peaks,
        neutral_precursor_frac=neutral_precursor_frac,
        min_precursor_frac=min_precursor_frac,
    )
    persist_consensus(analysis_db_path, result, command_id=command_id)
    logger.info(
        "consensus: picked a scan for %d/%d MS2-bearing feature(s) "
        "(%d backed by a library hit)",
        result.n_features,
        len(features),
        result.n_features_scored,
    )
    return result
