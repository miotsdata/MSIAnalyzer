"""Stage B of MS2 annotation: match associated MS2 scans to spectral libraries.

The grouper (:mod:`msianalyzer.core.annotation.group_ms2`) has already
snapped every MS2 scan to a master ``feature``. This stage takes each
feature that carries MS2, pulls the library spectra whose precursor m/z is
near the feature m/z (``candidate_ppm``), and scores every scan of that
feature against every candidate with a coverage-aware reverse dot product
(:mod:`msianalyzer.core.annotation.spectral_match`).

``config.library_path`` may name a single library or a list of them; the
candidates from every configured library are pooled per scan, so ``rank``
orders the best hit across all of them and each stored row carries its own
``library_id``.

Design (see ADR 0007):

* **Batch unit = one feature.** Candidates are gathered *once* per feature
  (from every library) and reused for all its scans; features are chunked
  across worker processes.
* **Store every candidate** that shares at least ``min_matched_peaks``
  fragments with the (noise-filtered) empirical spectrum, each with a
  ``rank`` within its scan. ``rank_feature`` then orders the scans of a
  feature by their best hit.
* **Chimeric scans** (isolation window held >1 feature) are scored against
  their primary feature and flagged ``is_chimeric``; ``annotate_chimeric``
  can drop them entirely.
* **Filtered spectra** — the noise-filtered, max-normalised empirical and
  library peak lists that were actually scored — are persisted on every row
  (``store_filtered_spectra``) so downstream can draw mirror plots without
  re-running the matcher.
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
from typing import TYPE_CHECKING, Sequence

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
    "assign_rank_feature",
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

    Mutable on purpose: ``rank`` is stamped after a scan's candidates are
    scored, ``rank_feature`` after every batch has returned.
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
    is_chimeric: bool
    n_features_in_window: int | None
    precursor_only: bool
    emp_filtered_mz: np.ndarray
    emp_filtered_intensity: np.ndarray
    lib_filtered_mz: np.ndarray
    lib_filtered_intensity: np.ndarray
    rank: int = 0
    rank_feature: int | None = None
    purity: float | None = None
    runner_up_rel_int: float | None = None


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
        )
        if m.n_matched_peaks >= min_matched_peaks:
            scored.append((cand, m))

    scored.sort(key=lambda t: t[1].score, reverse=True)
    return scored


def rank_scan_rows(rows: list[AnnotationRow]) -> list[AnnotationRow]:
    """Stamp ``rank`` 1..n (by ``score`` desc) on every row of one scan."""
    ordered = sorted(rows, key=lambda r: r.score, reverse=True)
    for i, r in enumerate(ordered, start=1):
        r.rank = i
    return ordered


def assign_rank_feature(rows: list[AnnotationRow]) -> list[AnnotationRow]:
    """Stamp ``rank_feature`` on every row.

    Within one feature, scans are ordered by their best (highest) score;
    every row of the best-scoring scan gets ``rank_feature = 1``, and so on.
    """
    by_feature: dict[int | None, list[AnnotationRow]] = defaultdict(list)
    for r in rows:
        by_feature[r.feature_id].append(r)

    for frows in by_feature.values():
        best_by_scan: dict[tuple[int | None, int], float] = {}
        for r in frows:
            key = (r.sample_id, r.scan_id)
            if key not in best_by_scan or r.score > best_by_scan[key]:
                best_by_scan[key] = r.score
        order = sorted(best_by_scan, key=lambda k: best_by_scan[k], reverse=True)
        rank_of = {k: i for i, k in enumerate(order, start=1)}
        for r in frows:
            r.rank_feature = rank_of[(r.sample_id, r.scan_id)]
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
        is_chimeric=bool(scan.get("is_chimeric")),
        n_features_in_window=scan.get("n_features_in_window"),
        precursor_only=bool(scan.get("precursor_only")),
        purity=scan.get("purity"),
        runner_up_rel_int=scan.get("runner_up_rel_int"),
        emp_filtered_mz=m.filtered_mz,
        emp_filtered_intensity=m.filtered_intensity,
        lib_filtered_mz=m.lib_filtered_mz,
        lib_filtered_intensity=m.lib_filtered_intensity,
    )


@log_call
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
    annotate_chimeric: bool = True,
    min_purity: float | None = None,
) -> list[AnnotationRow]:
    """Score every scan of one feature against a shared candidate set.

    Args:
        feature_id: The master feature id stamped on every produced row.
        feature_mz: The feature's m/z (candidates were gathered around it).
        scans: Dicts with ``scan_id``, ``sample_id``, ``emp_mz``,
            ``emp_int``, ``is_chimeric``, ``n_features_in_window``,
            ``precursor_only`` and (optional) ``purity`` /
            ``runner_up_rel_int``.
        candidates: Library spectra to score against (shared by all scans).
        annotate_chimeric: When False, scans flagged ``is_chimeric`` are
            skipped.
        min_purity: When set, scans with a known ``purity`` below this are
            skipped.

    Returns:
        All rows for the feature, each with ``rank`` filled (per scan) but
        ``rank_feature`` still None.
    """
    logger.debug(
        "Annotating feature %s (m/z %.4f): %d scan(s), %d candidate(s)",
        feature_id,
        feature_mz,
        len(scans),
        len(candidates),
    )
    rows: list[AnnotationRow] = []
    for scan in scans:
        if scan.get("is_chimeric") and not annotate_chimeric:
            continue
        if min_purity is not None:
            p = scan.get("purity")
            if p is not None and p < min_purity:
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
    records reach the main process.
    """
    global _WORKER_LIBRARIES
    if log_queue is not None:
        root = logging.getLogger()
        root.handlers.clear()
        root.addHandler(QueueHandler(log_queue))
        root.setLevel(logging.DEBUG)
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
    annotate_chimeric: bool,
    min_purity: float | None = None,
) -> list[AnnotationRow]:
    """Annotate one chunk of features (a worker task).

    Candidates from every configured library are pooled per scan, so
    ``rank`` orders the best hit across all of them. ``precursor_purity`` is
    left-joined so every row can carry the scan's ``purity`` /
    ``runner_up_rel_int`` and ``min_purity`` can drop low-purity scans.
    """
    libraries = _WORKER_LIBRARIES
    rows: list[AnnotationRow] = []
    raw_cons: dict = {}
    adb = sqlite3.connect(f"file:{analysis_db_path}?mode=ro", uri=True)
    try:
        for feature_id, feature_mz in batch:
            assoc = adb.execute(
                "SELECT a.scan_id, a.sample_id, a.n_features_in_window, "
                "a.precursor_only, a.polarity, p.purity, p.runner_up_rel_int "
                "FROM ms2_associations a "
                "LEFT JOIN precursor_purity p "
                "  ON p.sample_id = a.sample_id AND p.ms2_scan_id = a.scan_id "
                "WHERE a.feature_id = ?",
                (feature_id,),
            ).fetchall()
            if not assoc:
                continue

            scans_by_pol: dict[str | None, list[dict]] = defaultdict(list)
            for scan_id, sample_id, n_in_win, prec_only, pol, purity, runner_up in assoc:
                is_chimeric = (n_in_win or 0) > 1
                if is_chimeric and not annotate_chimeric:
                    continue
                if min_purity is not None and purity is not None and purity < min_purity:
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
                        "n_features_in_window": n_in_win,
                        "is_chimeric": is_chimeric,
                        "precursor_only": bool(prec_only),
                        "purity": purity,
                        "runner_up_rel_int": runner_up,
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
                        annotate_chimeric=annotate_chimeric,
                        min_purity=min_purity,
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
    "rank",
    "rank_feature",
    "is_chimeric",
    "n_features_in_window",
    "purity",
    "runner_up_rel_int",
    "precursor_only",
    "emp_filtered_mz",
    "emp_filtered_intensity",
    "lib_filtered_mz",
    "lib_filtered_intensity",
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
    store_filtered_spectra: bool = True,
) -> None:
    """Write annotation rows into the analysis database.

    Args:
        db_path: Path to the analysis database.
        rows: Rows to store (``rank`` / ``rank_feature`` already stamped).
            Each row carries its own ``library_id``.
        library_ids: The ``annotation_libraries.id``(s) this call owns —
            their existing ``ms2_annotations`` rows are cleared first when
            ``replace_existing``.
        command_id: Optional ``commands.id`` stamped on every row.
        replace_existing: Delete those libraries' existing
            ``ms2_annotations`` rows first.
        store_filtered_spectra: When False the four ``*_filtered_*`` blob
            columns are written NULL.
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
                        r.rank,
                        r.rank_feature,
                        int(r.is_chimeric),
                        r.n_features_in_window,
                        r.purity,
                        r.runner_up_rel_int,
                        int(r.precursor_only),
                        _blob_or_none(r.emp_filtered_mz, store_filtered_spectra),
                        _blob_or_none(
                            r.emp_filtered_intensity, store_filtered_spectra
                        ),
                        _blob_or_none(r.lib_filtered_mz, store_filtered_spectra),
                        _blob_or_none(
                            r.lib_filtered_intensity, store_filtered_spectra
                        ),
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
            store_filtered_spectra=config.store_filtered_spectra,
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
        annotate_chimeric=config.annotate_chimeric,
        min_purity=getattr(config, "min_purity", None),
    )

    rows: list[AnnotationRow] = []
    n_workers = config.n_workers
    if n_workers is not None and n_workers <= 1:
        _init_worker(lib_specs)
        for b in batches:
            rows.extend(worker(b))
    else:
        # `_init_worker` handles both library loading and log forwarding;
        # `worker_logging` just runs the listener over the parent handlers.
        with worker_logging() as (log_queue, _):
            with ProcessPoolExecutor(
                max_workers=n_workers,
                initializer=_init_worker,
                initargs=(lib_specs, log_queue),
            ) as executor:
                for part in executor.map(worker, batches):
                    rows.extend(part)

    assign_rank_feature(rows)
    persist_annotations(
        analysis_db_path,
        rows,
        library_ids,
        command_id=command_id,
        store_filtered_spectra=config.store_filtered_spectra,
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
