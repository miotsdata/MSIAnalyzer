"""Stage B of MS2 annotation: match associated MS2 scans to spectral libraries.

The grouper (:mod:`msianalyzer.core.annotation.group_ms2`) has already
snapped every MS2 scan to a master ``feature``. This stage takes each
feature that carries MS2, pulls the library spectra whose precursor m/z is
near the feature m/z (``candidate_ppm``), and scores every scan of that
feature against every candidate with a coverage-aware reverse dot product
(:mod:`msianalyzer.core.annotation.spectral_match`).

``config.library_path`` may name a single library or a list of them; the
candidates from every configured library are pooled per scan, so ``rank_ms2``
orders the best hit across all of them and each stored row carries its own
``library_id``.

Design (see ADR 0007):

* **Batch unit = one feature.** Candidates are gathered *once* per feature
  (from every library) and reused for all its scans; features are chunked
  across worker processes.
* **Store every candidate** that shares at least ``min_matched_peaks``
  fragments with the (noise-filtered) empirical spectrum, each with a
  ``rank_ms2`` within its scan. ``rank_feature`` /
  ``rank_feature_sample`` then rank the feature's rows outright (``1`` = its
  single best hit), and ``rank_scan_feature`` /
  ``rank_scan_feature_sample`` rank its *scans* by their best hit.
* **Every associated MS2 scan is scored, unconditionally.** There is no
  longer a "chimeric" gate keyed on how many *other* aligned features
  happen to share a scan's isolation window — that count says nothing
  about what actually co-fragmented into any one scan's own spectrum, and
  over-flagged badly once a feature list grew dense (see ADR 0019).
  ``min_precursor_frac`` is the real, per-scan signal to filter on instead,
  if you want to skip scoring scans with poor precursor purity.
* **Raw spectra, not filtered ones.** The *untouched* empirical and
  library peak lists — not the noise-filtered/max-normalised copies
  actually scored — are persisted on every row (``store_raw_spectra``),
  so a mirror plot never needs to re-open a raw per-sample database or
  the library file itself (either of which may be a slow or remote
  mount — see ADR 0016). The filtered view is reconstructed on demand
  from the raw one plus the run's own ``noise_threshold`` — a pure,
  deterministic function
  (:func:`~msianalyzer.core.annotation.spectral_match.normalize_and_filter_spectrum`)
  — rather than also persisted, since it's fully recoverable and storing
  both would just be redundant (see ADR 0018).
* An empty ``library_path`` (``None`` or ``[]``) disables the whole stage.

Outputs (schema in
:func:`msianalyzer.core.analysis_db.create_analysis_schema`):

* ``annotation_libraries`` — one row per library actually used.
* ``ms2_annotations`` — one row per (scan, library candidate) comparison.

Only the per-analysis database is written; raw per-sample databases are
opened read-only.
"""

from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from functools import partial
from logging.handlers import QueueHandler
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Sequence

import numpy as np

from ..analysis_db import create_analysis_schema
from ..parser.mzml_parser import array_to_blob, blob_to_array
from ..utils.db import safe_execute, safe_executemany
from ..utils.logging_utils import log_call, worker_logging
from .spectral_match import MatchResult, reverse_dot_product

if TYPE_CHECKING:  # avoid a runtime import cycle / hard libviz dependency
    from ..config.config import AnnotateConfig

logger = logging.getLogger(__name__)

__all__ = [
    "Candidate",
    "LibraryInfo",
    "AnnotationRow",
    "AnnotationResult",
    "normalize_polarity",
    "normalize_library_paths",
    "score_scan_against_candidates",
    "rank_scan_rows",
    "assign_feature_ranks",
    "annotate_feature",
    "load_library",
    "persist_annotations",
    "run_annotation",
]

# Decimal places used when (de)serialising the filtered-spectrum blobs.
# Matches the parser's default for raw scan arrays.
_BLOB_DECIMALS = 4


# ---------------------------------------------------------------------------
# containers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """One library spectrum in contention for a feature."""

    library_id: int
    spectrum_id: int
    compound_id: int | None
    compound_name: str | None
    compound_formula: str | None
    inchikey: str | None
    mz: np.ndarray
    intensity: np.ndarray


@dataclass(frozen=True)
class LibraryInfo:
    """A spectral library that was used to annotate (one row of
    ``annotation_libraries``)."""

    library_id: int
    path: str
    name: str | None
    n_spectra: int
    n_compounds: int


@dataclass
class AnnotationRow:
    """One (MS2 scan, library candidate) comparison (one ``ms2_annotations`` row).

    Mutable on purpose: ``rank_ms2`` is stamped after a scan's candidates are
    scored; the four feature-level ranks (``rank_feature`` /
    ``rank_feature_sample`` / ``rank_scan_feature`` /
    ``rank_scan_feature_sample``) after every batch has returned.
    """

    sample_id: int | None
    scan_id: int
    feature_id: int | None
    library_id: int
    library_spectrum_id: int
    compound_id: int | None
    compound_name: str | None
    compound_formula: str | None
    inchikey: str | None
    score: float
    dot_product_score: float
    lib_coverage: float
    emp_coverage: float
    coverage_score: float
    n_matched_peaks: int
    n_lib_peaks: int
    n_emp_peaks_raw: int
    n_emp_peaks_filtered: int
    precursor_only: bool
    flat_fragmentation: bool
    # The untouched (pre-noise-filtering) spectra on both sides — not the
    # filtered/normalised copies. `spectral_match.normalize_and_filter_spectrum`
    # reconstructs the filtered view on demand from these plus the run's
    # own `noise_threshold`, so that no longer needs to be persisted too
    # (see ADR 0018). emp_raw_* are `scan["emp_mz"]`/`scan["emp_int"]` —
    # identical across every candidate row for one scan (same scan, many
    # candidates); lib_raw_* is `Candidate.mz`/`.intensity`, unmutated by
    # scoring.
    emp_raw_mz: np.ndarray
    emp_raw_intensity: np.ndarray
    lib_raw_mz: np.ndarray
    lib_raw_intensity: np.ndarray
    rank_ms2: int = 0
    rank_feature: int | None = None
    rank_feature_sample: int | None = None
    rank_scan_feature: int | None = None
    rank_scan_feature_sample: int | None = None
    precursor_confirmed: bool | None = None
    precursor_frac: float | None = None


@dataclass(frozen=True)
class AnnotationResult:
    """Return value of :func:`run_annotation`."""

    rows: list[AnnotationRow]
    libraries: list[LibraryInfo]
    n_scans_annotated: int
    n_features_annotated: int


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------

_POS_TOKENS = {"+", "pos", "positive", "p"}
_NEG_TOKENS = {"-", "neg", "negative", "n"}


def normalize_polarity(value: str | None) -> str | None:
    """Map a free-form polarity string to ``"POSITIVE"`` / ``"NEGATIVE"`` / None.

    Accepts ``"+"``, ``"pos"``, ``"positive"`` (any case), the negative
    equivalents, and anything that merely *starts* with ``pos`` / ``neg``.
    Everything else (including ``None`` and the empty string) becomes
    ``None`` — "do not filter the library by polarity".
    """
    if value is None:
        return None
    v = str(value).strip().lower()
    if not v:
        return None
    if v in _POS_TOKENS or v.startswith("pos"):
        return "POSITIVE"
    if v in _NEG_TOKENS or v.startswith("neg"):
        return "NEGATIVE"
    return None


def score_scan_against_candidates(
    emp_mz: Sequence[float] | np.ndarray,
    emp_int: Sequence[float] | np.ndarray,
    candidates: Sequence[Candidate],
    *,
    fragment_ppm: float,
    noise_threshold: float,
    mz_power: float,
    int_power: float,
    min_matched_peaks: int,
    weight_dot: float = 1.0,
    weight_lib_coverage: float = 0.5,
    weight_emp_coverage: float = 0.5,
) -> list[tuple[Candidate, MatchResult]]:
    """Score one empirical spectrum against every candidate.

    Returns the ``(candidate, MatchResult)`` pairs that shared at least
    ``min_matched_peaks`` fragment peaks, sorted by ``score`` descending.
    """
    emp_mz = np.asarray(emp_mz, dtype=float)
    emp_int = np.asarray(emp_int, dtype=float)

    scored: list[tuple[Candidate, MatchResult]] = []
    for cand in candidates:
        m = reverse_dot_product(
            query_mz=emp_mz,
            query_intensity=emp_int,
            library_mz=np.asarray(cand.mz, dtype=float),
            library_intensity=np.asarray(cand.intensity, dtype=float),
            ppm_tolerance=fragment_ppm,
            mz_power=mz_power,
            int_power=int_power,
            noise_threshold=noise_threshold,
            weight_dot=weight_dot,
            weight_lib_coverage=weight_lib_coverage,
            weight_emp_coverage=weight_emp_coverage,
        )
        if m.n_matched_peaks >= min_matched_peaks:
            scored.append((cand, m))

    scored.sort(key=lambda t: t[1].score, reverse=True)
    return scored


def rank_scan_rows(rows: list[AnnotationRow]) -> list[AnnotationRow]:
    """Stamp ``rank_ms2`` 1..n (by ``score`` desc) on every row of one scan."""
    ordered = sorted(rows, key=lambda r: r.score, reverse=True)
    for i, r in enumerate(ordered, start=1):
        r.rank_ms2 = i
    return ordered


def _rank_rows_by_score(
    rows: list[AnnotationRow],
    group_key: Callable[[AnnotationRow], object],
    attr: str,
    *,
    representative_score_tolerance: float = 0.0,
) -> None:
    """Rank every row of a group 1..n by ``score`` desc; write onto ``attr``.

    A per-row rank (like :func:`rank_scan_rows`, but over a wider group), so
    ``attr == 1`` marks the single best (scan, candidate) row of the group —
    the *representative* one, e.g. the compound name a feature is labelled
    with everywhere in the GUI/report. Strictly increasing — ties do not
    share a rank.

    ``representative_score_tolerance`` only changes *which row lands at
    rank 1* — every other rank (2, 3, ...) is always plain ``score`` desc,
    unaffected. Among rows within that tolerance of the group's top score,
    the one with the most ``n_matched_peaks`` wins rank 1 instead of
    whichever merely scored highest. This addresses a real case: two
    candidates can score within a hair of each other while one matched a
    single library fragment and the other matched several — the trivial
    single-peak match isn't a more confident identification just because
    a small scoring-formula quirk (e.g. a busier scan depressing
    ``emp_coverage``) nudged its score a few points higher. `score` itself
    is never touched by this — only representative-row *selection* is.
    """
    by_group: dict[object, list[AnnotationRow]] = defaultdict(list)
    for r in rows:
        by_group[group_key(r)].append(r)

    for grows in by_group.values():
        by_score = sorted(grows, key=lambda r: r.score, reverse=True)
        if representative_score_tolerance > 0.0 and len(by_score) > 1:
            top_score = by_score[0].score
            pool = [
                r for r in by_score
                if r.score >= top_score - representative_score_tolerance
            ]
            winner = max(pool, key=lambda r: (r.n_matched_peaks, r.score))
            ordered = [winner] + [r for r in by_score if r is not winner]
        else:
            ordered = by_score
        for i, r in enumerate(ordered, start=1):
            setattr(r, attr, i)


def _rank_scans_by_best_score(
    rows: list[AnnotationRow],
    group_key: Callable[[AnnotationRow], object],
    attr: str,
) -> None:
    """Rank each group's MS2 scans by their best score; broadcast onto ``attr``.

    Scans are ordered by their highest-scoring candidate; every row of the
    best scan gets ``1``, the next scan ``2``, and so on (strictly
    increasing — ties do not share a rank). The rank is written to
    ``attr`` on every row of the scan it ranks.
    """
    by_group: dict[object, list[AnnotationRow]] = defaultdict(list)
    for r in rows:
        by_group[group_key(r)].append(r)

    for grows in by_group.values():
        best_by_scan: dict[tuple[int | None, int], float] = {}
        for r in grows:
            key = (r.sample_id, r.scan_id)
            if key not in best_by_scan or r.score > best_by_scan[key]:
                best_by_scan[key] = r.score
        order = sorted(best_by_scan, key=lambda k: best_by_scan[k], reverse=True)
        rank_of = {k: i for i, k in enumerate(order, start=1)}
        for r in grows:
            setattr(r, attr, rank_of[(r.sample_id, r.scan_id)])


def assign_feature_ranks(
    rows: list[AnnotationRow], *, representative_score_tolerance: float = 0.0
) -> list[AnnotationRow]:
    """Stamp the four feature-level ranks on every row.

    Row-level (``attr == 1`` is the single best (scan, candidate) row):

    * ``rank_feature`` — over every row of the feature, all samples.
    * ``rank_feature_sample`` — over every row of one ``(feature, sample)``.

    Scan-level — the feature's MS2 *scans* ordered by their best hit, the
    rank broadcast onto every row of the scan (use with ``rank_ms2`` to walk
    that scan's candidates):

    * ``rank_scan_feature`` — over the feature's scans, all samples.
    * ``rank_scan_feature_sample`` — over the feature's scans in one sample.

    Each MS2 scan belongs to a single feature, so ``(feature_id, sample_id)``
    groups are well defined.

    Args:
        representative_score_tolerance: See
            :func:`_rank_rows_by_score` — passed through to the two
            row-level (compound-identity) ranks only; scan-level ranking
            is a different question ("which scan", not "which compound")
            and is unaffected.
    """
    _rank_rows_by_score(
        rows, lambda r: r.feature_id, "rank_feature",
        representative_score_tolerance=representative_score_tolerance,
    )
    _rank_rows_by_score(
        rows, lambda r: (r.feature_id, r.sample_id), "rank_feature_sample",
        representative_score_tolerance=representative_score_tolerance,
    )
    _rank_scans_by_best_score(rows, lambda r: r.feature_id, "rank_scan_feature")
    _rank_scans_by_best_score(
        rows, lambda r: (r.feature_id, r.sample_id), "rank_scan_feature_sample"
    )
    return rows


def _row_from_match(
    scan: dict, feature_id: int | None, cand: Candidate, m: MatchResult
) -> AnnotationRow:
    return AnnotationRow(
        sample_id=scan.get("sample_id"),
        scan_id=int(scan["scan_id"]),
        feature_id=feature_id,
        library_id=cand.library_id,
        library_spectrum_id=cand.spectrum_id,
        compound_id=cand.compound_id,
        compound_name=cand.compound_name,
        compound_formula=cand.compound_formula,
        inchikey=cand.inchikey,
        score=m.score,
        dot_product_score=m.dot_product_score,
        lib_coverage=m.lib_coverage,
        emp_coverage=m.emp_coverage,
        coverage_score=m.coverage_score,
        n_matched_peaks=m.n_matched_peaks,
        n_lib_peaks=m.n_lib_peaks,
        n_emp_peaks_raw=m.n_emp_peaks_raw,
        n_emp_peaks_filtered=m.n_emp_peaks_filtered,
        precursor_only=bool(scan.get("precursor_only")),
        flat_fragmentation=bool(scan.get("flat_fragmentation")),
        precursor_confirmed=scan.get("precursor_confirmed"),
        precursor_frac=scan.get("precursor_frac"),
        # The untouched spectra — `scan["emp_mz"]`/`["emp_int"]` (this
        # scan's own raw arrays, already read once by `_read_fragments`
        # and reused for every candidate) and `cand.mz`/`.intensity`
        # (never mutated by scoring, `reverse_dot_product` only reads
        # them) — neither is the filtered/normalised copy `m.filtered_*`/
        # `m.lib_filtered_*` carry; those exist for scoring's own
        # internal use and are no longer threaded into persisted storage.
        emp_raw_mz=scan["emp_mz"],
        emp_raw_intensity=scan["emp_int"],
        lib_raw_mz=cand.mz,
        lib_raw_intensity=cand.intensity,
    )


def annotate_feature(
    feature_id: int,
    feature_mz: float,
    scans: Sequence[dict],
    candidates: Sequence[Candidate],
    *,
    fragment_ppm: float,
    noise_threshold: float,
    mz_power: float,
    int_power: float,
    min_matched_peaks: int,
    weight_dot: float = 1.0,
    weight_lib_coverage: float = 0.5,
    weight_emp_coverage: float = 0.5,
    min_precursor_frac: float | None = None,
) -> list[AnnotationRow]:
    """Score every scan of one feature against a shared candidate set.

    Args:
        feature_id: The master feature id stamped on every produced row.
        feature_mz: The feature's m/z (candidates were gathered around it).
        scans: Dicts with ``scan_id``, ``sample_id``, ``emp_mz``,
            ``emp_int``, ``precursor_only`` and (optional)
            ``precursor_frac`` / ``precursor_confirmed``.
        candidates: Library spectra to score against (shared by all scans).
        min_precursor_frac: When set, scans with a known ``precursor_frac``
            below this are skipped.

    Returns:
        All rows for the feature, each with ``rank_ms2`` filled (per scan)
        but the four feature-level ranks still None.

    Not ``@log_call``-decorated and issues no per-feature logging: it runs
    thousands of times inside forked pool workers, and streaming that
    through the multiprocessing log queue deadlocks the pool at shutdown.
    """
    rows: list[AnnotationRow] = []
    for scan in scans:
        if min_precursor_frac is not None:
            frac = scan.get("precursor_frac")
            if frac is not None and frac < min_precursor_frac:
                continue
        scored = score_scan_against_candidates(
            scan["emp_mz"],
            scan["emp_int"],
            candidates,
            fragment_ppm=fragment_ppm,
            noise_threshold=noise_threshold,
            mz_power=mz_power,
            int_power=int_power,
            min_matched_peaks=min_matched_peaks,
            weight_dot=weight_dot,
            weight_lib_coverage=weight_lib_coverage,
            weight_emp_coverage=weight_emp_coverage,
        )
        scan_rows = [_row_from_match(scan, feature_id, c, m) for c, m in scored]
        rank_scan_rows(scan_rows)
        rows.extend(scan_rows)
    return rows


# ---------------------------------------------------------------------------
# library I/O
# ---------------------------------------------------------------------------


@log_call(source="path")
def load_library(path: Path | str):
    """Open a libviz library database read-only.

    Imported lazily so the rest of the module (and its tests) do not need
    ``libviz`` unless annotation actually runs.
    """
    from libviz.core.library import Library

    return Library.load_from_db(Path(path))


@log_call
def normalize_library_paths(library_path) -> list[str]:
    """Coerce ``config.library_path`` to a de-duplicated list of path strings.

    ``None`` / ``""`` / an empty list -> ``[]`` (annotation disabled). A bare
    string -> a one-element list. A list/tuple is kept, order preserved,
    blanks dropped, duplicates removed.
    """
    if not library_path:
        return []
    if isinstance(library_path, (str, Path)):
        library_path = [library_path]
    seen: set[str] = set()
    out: list[str] = []
    for p in library_path:
        if not p:
            continue
        s = str(p)
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _gather_candidates(
    library, library_id: int, mz: float, ppm: float, polarity: str | None
) -> list[Candidate]:
    """Library spectra whose precursor m/z is within ``ppm`` of ``mz``."""
    lo = mz * (1.0 - ppm / 1e6)
    hi = mz * (1.0 + ppm / 1e6)
    out: list[Candidate] = []
    for s in library.get_spectra_in_mz_range(lo, hi, polarity, convert_arrays=False):
        out.append(
            Candidate(
                library_id=library_id,
                spectrum_id=int(s["spectrum_id"]),
                compound_id=s.get("compound_id"),
                compound_name=s.get("compound_name"),
                compound_formula=s.get("compound_formula"),
                inchikey=s.get("inchikey"),
                mz=np.asarray(s["mz"], dtype=float),
                intensity=np.asarray(s["intensity"], dtype=float),
            )
        )
    return out


# ---------------------------------------------------------------------------
# worker
# ---------------------------------------------------------------------------

_WORKER_LIBRARIES: list[tuple[int, object]] = []


def _init_worker(lib_specs: Sequence[tuple[int, str]], log_queue=None) -> None:
    """ProcessPool initializer — forward logs, then load every library.

    ``lib_specs`` is ``[(library_id, path), ...]``; the loaded handles are
    kept in module state as ``[(library_id, Library), ...]``. When
    ``log_queue`` is given the worker's root logger is pointed at it so
    records reach the main process — but only ``WARNING`` and above:
    forwarding every ``DEBUG`` record from many forked workers through one
    ``multiprocessing.Queue`` backs the queue up and deadlocks the pool at
    shutdown (the feeder thread cannot flush a full pipe, so workers never
    exit and ``ProcessPoolExecutor.shutdown`` blocks forever).
    """
    global _WORKER_LIBRARIES
    if log_queue is not None:
        root = logging.getLogger()
        root.handlers.clear()
        root.addHandler(QueueHandler(log_queue))
        root.setLevel(logging.WARNING)
    _WORKER_LIBRARIES = [(int(lid), load_library(path)) for lid, path in lib_specs]


def _read_fragments(
    raw_cons: dict, sample_raw_db: dict, sample_id: int, scan_id: int
):
    con = raw_cons.get(sample_id)
    if con is None:
        path = sample_raw_db.get(sample_id)
        if path is None:
            return None, None
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        raw_cons[sample_id] = con
    row = con.execute(
        "SELECT mz_array, intensity_array FROM ms2_scans WHERE scan_id = ?",
        (scan_id,),
    ).fetchone()
    if row is None:
        return None, None
    return blob_to_array(row[0]), blob_to_array(row[1])


@log_call
def _annotate_feature_batch(
    batch: Sequence[tuple[int, float]],
    *,
    analysis_db_path: str,
    sample_raw_db: dict,
    candidate_ppm: float,
    fragment_ppm: float,
    noise_threshold: float,
    mz_power: float,
    int_power: float,
    min_matched_peaks: int,
    min_precursor_frac: float | None = None,
    weight_dot: float = 1.0,
    weight_lib_coverage: float = 0.5,
    weight_emp_coverage: float = 0.5,
) -> list[AnnotationRow]:
    """Annotate one chunk of features (a worker task).

    Candidates from every configured library are pooled per scan, so
    ``rank_ms2`` orders the best hit across all of them. Every MS2 scan
    associated with the feature is scored unconditionally — ``precursor_purity``
    is left-joined so every row can carry the scan's ``precursor_frac`` /
    ``precursor_confirmed`` and ``min_precursor_frac`` can drop low-purity
    scans (see ADR 0019).
    """
    libraries = _WORKER_LIBRARIES
    rows: list[AnnotationRow] = []
    raw_cons: dict = {}
    adb = sqlite3.connect(f"file:{analysis_db_path}?mode=ro", uri=True)
    try:
        for feature_id, feature_mz in batch:
            assoc = adb.execute(
                "SELECT a.scan_id, a.sample_id, "
                "a.precursor_only, a.flat_fragmentation, a.polarity, "
                "p.precursor_confirmed, p.precursor_frac "
                "FROM ms2_associations a "
                "LEFT JOIN precursor_purity p "
                "  ON p.sample_id = a.sample_id AND p.ms2_scan_id = a.scan_id "
                "WHERE a.feature_id = ?",
                (feature_id,),
            ).fetchall()
            if not assoc:
                continue

            scans_by_pol: dict[str | None, list[dict]] = defaultdict(list)
            for (
                scan_id, sample_id, prec_only, flat_frag, pol,
                confirmed, frac,
            ) in assoc:
                if (
                    min_precursor_frac is not None
                    and frac is not None
                    and frac < min_precursor_frac
                ):
                    continue
                emp_mz, emp_int = _read_fragments(
                    raw_cons, sample_raw_db, sample_id, scan_id
                )
                if emp_mz is None:
                    continue
                scans_by_pol[normalize_polarity(pol)].append(
                    {
                        "scan_id": scan_id,
                        "sample_id": sample_id,
                        "precursor_only": bool(prec_only),
                        "flat_fragmentation": bool(flat_frag),
                        "precursor_confirmed": (
                            None if confirmed is None else bool(confirmed)
                        ),
                        "precursor_frac": frac,
                        "emp_mz": emp_mz,
                        "emp_int": emp_int,
                    }
                )

            for polarity, scans in scans_by_pol.items():
                candidates: list[Candidate] = []
                for lib_id, library in libraries:
                    candidates.extend(
                        _gather_candidates(
                            library,
                            lib_id,
                            float(feature_mz),
                            candidate_ppm,
                            polarity,
                        )
                    )
                if not candidates:
                    continue
                rows.extend(
                    annotate_feature(
                        int(feature_id),
                        float(feature_mz),
                        scans,
                        candidates,
                        fragment_ppm=fragment_ppm,
                        noise_threshold=noise_threshold,
                        mz_power=mz_power,
                        int_power=int_power,
                        min_matched_peaks=min_matched_peaks,
                        weight_dot=weight_dot,
                        weight_lib_coverage=weight_lib_coverage,
                        weight_emp_coverage=weight_emp_coverage,
                        min_precursor_frac=min_precursor_frac,
                    )
                )
    finally:
        adb.close()
        for c in raw_cons.values():
            c.close()
    return rows


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------

_ANN_COLS = (
    "sample_id",
    "scan_id",
    "feature_id",
    "library_id",
    "library_spectrum_id",
    "compound_id",
    "compound_name",
    "compound_formula",
    "inchikey",
    "score",
    "dot_product_score",
    "lib_coverage",
    "emp_coverage",
    "coverage_score",
    "n_matched_peaks",
    "n_lib_peaks",
    "n_emp_peaks_raw",
    "n_emp_peaks_filtered",
    "rank_ms2",
    "rank_feature",
    "rank_feature_sample",
    "rank_scan_feature",
    "rank_scan_feature_sample",
    "precursor_confirmed",
    "precursor_frac",
    "precursor_only",
    "flat_fragmentation",
    "emp_raw_mz",
    "emp_raw_intensity",
    "lib_raw_mz",
    "lib_raw_intensity",
    "command_id",
)


def _blob_or_none(arr, store: bool):
    if not store or arr is None:
        return None
    arr = np.asarray(arr, dtype=float)
    if arr.size == 0:
        return None
    return array_to_blob(arr, _BLOB_DECIMALS, True)


@log_call(source="db_path")
def persist_annotations(
    db_path: Path | str,
    rows: Sequence[AnnotationRow],
    library_ids: int | Sequence[int],
    *,
    command_id: int | None = None,
    replace_existing: bool = True,
    store_raw_spectra: bool = True,
) -> None:
    """Write annotation rows into the analysis database.

    Args:
        db_path: Path to the analysis database.
        rows: Rows to store (``rank_ms2`` and the four feature-level ranks
            already stamped).
            Each row carries its own ``library_id``.
        library_ids: The ``annotation_libraries.id``(s) this call owns —
            their existing ``ms2_annotations`` rows are cleared first when
            ``replace_existing``.
        command_id: Optional ``commands.id`` stamped on every row.
        replace_existing: Delete those libraries' existing
            ``ms2_annotations`` rows first.
        store_raw_spectra: When False the ``emp_raw_*``/``lib_raw_*`` blob
            columns are all written NULL — the GUI mirror plot then has
            nothing to reconstruct a filtered or raw view from at all for
            these rows (see ADR 0018).
    """
    if isinstance(library_ids, int):
        library_ids = [library_ids]
    library_ids = [int(x) for x in library_ids]

    with sqlite3.connect(Path(db_path)) as con:
        con.execute("PRAGMA foreign_keys = ON")
        create_analysis_schema(con)
        if replace_existing and library_ids:
            id_placeholders = ", ".join("?" * len(library_ids))
            con.execute(
                f"DELETE FROM ms2_annotations WHERE library_id IN ({id_placeholders})",
                library_ids,
            )
        if rows:
            placeholders = ", ".join("?" * len(_ANN_COLS))
            safe_executemany(
                con,
                f"INSERT INTO ms2_annotations ({', '.join(_ANN_COLS)}) "
                f"VALUES ({placeholders})",
                [
                    (
                        r.sample_id,
                        r.scan_id,
                        r.feature_id,
                        r.library_id,
                        r.library_spectrum_id,
                        r.compound_id,
                        r.compound_name,
                        r.compound_formula,
                        r.inchikey,
                        r.score,
                        r.dot_product_score,
                        r.lib_coverage,
                        r.emp_coverage,
                        r.coverage_score,
                        r.n_matched_peaks,
                        r.n_lib_peaks,
                        r.n_emp_peaks_raw,
                        r.n_emp_peaks_filtered,
                        r.rank_ms2,
                        r.rank_feature,
                        r.rank_feature_sample,
                        r.rank_scan_feature,
                        r.rank_scan_feature_sample,
                        None if r.precursor_confirmed is None else int(r.precursor_confirmed),
                        r.precursor_frac,
                        int(r.precursor_only),
                        int(r.flat_fragmentation),
                        _blob_or_none(r.emp_raw_mz, store_raw_spectra),
                        _blob_or_none(r.emp_raw_intensity, store_raw_spectra),
                        _blob_or_none(r.lib_raw_mz, store_raw_spectra),
                        _blob_or_none(r.lib_raw_intensity, store_raw_spectra),
                        command_id,
                    )
                    for r in rows
                ],
                table="ms2_annotations",
                logger=logger,
                source=db_path,
            )
        con.commit()


@log_call(source="db_path")
def _register_library(
    db_path: Path | str, library, *, command_id: int | None = None
) -> int:
    """Insert/refresh the ``annotation_libraries`` row and return its id."""
    path = str(library.path)
    name = getattr(library, "name", None)
    n_spectra = int(getattr(library, "n_spectra", 0) or 0)
    n_compounds = int(getattr(library, "n_compounds", 0) or 0)
    with sqlite3.connect(Path(db_path)) as con:
        con.execute("PRAGMA foreign_keys = ON")
        create_analysis_schema(con)
        row = con.execute(
            "SELECT id FROM annotation_libraries WHERE path = ?", (path,)
        ).fetchone()
        if row is not None:
            safe_execute(
                con,
                "UPDATE annotation_libraries "
                "SET name = ?, n_spectra = ?, n_compounds = ?, command_id = ? "
                "WHERE id = ?",
                (name, n_spectra, n_compounds, command_id, row[0]),
                table="annotation_libraries",
                logger=logger,
                source=db_path,
            )
            con.commit()
            return int(row[0])
        cur = safe_execute(
            con,
            "INSERT INTO annotation_libraries "
            "(path, name, n_spectra, n_compounds, command_id) VALUES (?, ?, ?, ?, ?)",
            (path, name, n_spectra, n_compounds, command_id),
            table="annotation_libraries",
            logger=logger,
            source=db_path,
        )
        con.commit()
        return int(cur.lastrowid)


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


@log_call(source="analysis_db_path")
def run_annotation(
    analysis_db_path: Path | str,
    config: "AnnotateConfig",
    *,
    command_id: int | None = None,
) -> AnnotationResult:
    """Annotate every MS2-bearing feature against the configured library.

    Expects ``features`` and ``ms2_associations`` already populated (align +
    grouper have run). Reads raw databases read-only; writes only
    ``annotation_libraries`` and ``ms2_annotations``.

    Args:
        analysis_db_path: Path to the analysis database.
        config: An ``AnnotateConfig`` (or any object exposing the same
            attributes).
        command_id: Optional ``commands.id`` stamped on the library row and
            every annotation.

    Returns:
        An :class:`AnnotationResult`. When ``config.library_path`` is empty
        the result is empty and nothing is written. ``config.library_path``
        may be a single path or a list of paths; candidates from every
        library are pooled per scan before ranking.
    """
    analysis_db_path = Path(analysis_db_path)
    library_paths = normalize_library_paths(getattr(config, "library_path", None))
    if not library_paths:
        logger.info("No annotation library configured; skipping MS2 annotation")
        return AnnotationResult(
            rows=[], libraries=[], n_scans_annotated=0, n_features_annotated=0
        )

    lib_specs: list[tuple[int, str]] = []
    lib_infos: list[LibraryInfo] = []
    for path in library_paths:
        library = load_library(path)
        library_id = _register_library(
            analysis_db_path, library, command_id=command_id
        )
        lib_specs.append((library_id, path))
        lib_infos.append(
            LibraryInfo(
                library_id=library_id,
                path=str(library.path),
                name=getattr(library, "name", None),
                n_spectra=int(getattr(library, "n_spectra", 0) or 0),
                n_compounds=int(getattr(library, "n_compounds", 0) or 0),
            )
        )
    library_ids = [lid for lid, _ in lib_specs]

    with sqlite3.connect(analysis_db_path) as con:
        feats = con.execute(
            "SELECT DISTINCT f.feature_id, f.mz FROM features f "
            "JOIN ms2_associations a ON a.feature_id = f.feature_id "
            "ORDER BY f.mz"
        ).fetchall()
        samples = con.execute(
            "SELECT sample_id, raw_db_path FROM samples"
        ).fetchall()

    if not feats:
        persist_annotations(
            analysis_db_path,
            [],
            library_ids,
            command_id=command_id,
            store_raw_spectra=config.store_raw_spectra,
        )
        logger.info("No MS2-associated features to annotate")
        return AnnotationResult(
            rows=[], libraries=lib_infos, n_scans_annotated=0, n_features_annotated=0
        )

    sample_raw_db = {int(sid): str(rp) for sid, rp in samples}
    batch_size = max(1, int(config.batch_size))
    batches = [
        feats[i : i + batch_size] for i in range(0, len(feats), batch_size)
    ]
    logger.info(
        "annotation: %d MS2-bearing features in %d batch(es) against %d librar%s",
        len(feats),
        len(batches),
        len(lib_infos),
        "y" if len(lib_infos) == 1 else "ies",
    )

    worker = partial(
        _annotate_feature_batch,
        analysis_db_path=str(analysis_db_path),
        sample_raw_db=sample_raw_db,
        candidate_ppm=config.candidate_ppm,
        fragment_ppm=config.fragment_ppm,
        noise_threshold=config.noise_threshold,
        mz_power=config.mz_power,
        int_power=config.int_power,
        min_matched_peaks=config.min_matched_peaks,
        weight_dot=getattr(config, "score_weight_dot", 1.0),
        weight_lib_coverage=getattr(config, "score_weight_lib_coverage", 0.5),
        weight_emp_coverage=getattr(config, "score_weight_emp_coverage", 0.5),
        min_precursor_frac=getattr(config, "min_precursor_frac", None),
    )

    rows: list[AnnotationRow] = []
    n_batches = len(batches)
    n_workers = config.n_workers
    if n_workers is not None and n_workers <= 1:
        _init_worker(lib_specs)
        for i, b in enumerate(batches, start=1):
            rows.extend(worker(b))
            if i % 20 == 0 or i == n_batches:
                logger.info("annotation: %d/%d batches done", i, n_batches)
    else:
        # `_init_worker` handles both library loading and log forwarding;
        # `worker_logging` just runs the listener over the parent handlers.
        # Progress is logged here, in the parent — workers forward WARNING+
        # only (see `_init_worker`).
        with worker_logging() as (log_queue, _):
            with ProcessPoolExecutor(
                max_workers=n_workers,
                initializer=_init_worker,
                initargs=(lib_specs, log_queue),
            ) as executor:
                for i, part in enumerate(executor.map(worker, batches), start=1):
                    rows.extend(part)
                    if i % 20 == 0 or i == n_batches:
                        logger.info(
                            "annotation: %d/%d batches done", i, n_batches
                        )

    assign_feature_ranks(
        rows,
        representative_score_tolerance=getattr(
            config, "representative_score_tolerance", 0.0
        ),
    )
    persist_annotations(
        analysis_db_path,
        rows,
        library_ids,
        command_id=command_id,
        store_raw_spectra=config.store_raw_spectra,
    )

    scans_done = {(r.sample_id, r.scan_id) for r in rows}
    feats_done = {r.feature_id for r in rows}
    logger.info(
        "Annotated %d MS2 scan(s) over %d feature(s) against %d librar%s: "
        "%d candidate row(s)",
        len(scans_done),
        len(feats_done),
        len(lib_infos),
        "y" if len(lib_infos) == 1 else "ies",
        len(rows),
    )
    return AnnotationResult(
        rows=rows,
        libraries=lib_infos,
        n_scans_annotated=len(scans_done),
        n_features_annotated=len(feats_done),
    )
