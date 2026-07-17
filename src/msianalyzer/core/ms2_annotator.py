"""
ms2_annotator.py
Orchestrates MS2 annotation against libviz libraries.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from functools import partial
from multiprocessing import Pool
from pathlib import Path
from typing import Optional

import numpy as np

from msianalyzer.core.mzml_parser import blob_to_array, array_to_blob
from msianalyzer.core.ms2_grouper import MzCenterMethod, group_ms2_by_filter, assign_ms2_to_groups
from msianalyzer.core.spectral_matching import reverse_dot_product, MatchResult
from msianalyzer.core.utils.logging_utils import configure_logging

_LIBRARYS = None

def init_worker(config):
    global _LIBRARIES
    _LIBRARIES = [_load_library(p) for p in config.library_paths]

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class AnnotationConfig:
    ms2_db_path: Path
    groups_db_path: Path
    annotations_db_path: Path
    library_paths: list[Path]
    group_precursor_tolerance: float = 1
    group_precursor_tolerance_unit: str = "Da"
    max_group_span_Da: float = 2
    group_n: int | None = None
    library_query_tolerance: float = 1
    library_query_tolerance_unit: str = "Da"
    fragment_ppm_tolerance: float = 5.0
    mz_power: float = 2.0
    int_power: float = 0.5
    noise_threshold: float = 0.01
    min_matched_fraction: float = 0.0
    top_n: int = 1
    polarity: Optional[str] = None
    mz_center_method: str = "mean"


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class Annotation:
    """One stored annotation result."""
    scan_id: int
    library_path: str
    library_spectrum_id: int
    compound_id: int
    compound_name: str
    compound_formula: str
    inchikey: str
    # --- scores ---
    score: float                # dot_product_score × coverage_score  [0,1]
    dot_product_score: float    # pure spectral alignment             [0,1]
    lib_coverage: float         # matched_lib / n_lib                 [0,1]
    emp_coverage: float         # matched_emp / n_emp_filtered        [0,1]
    coverage_score: float       # sqrt(lib_coverage × emp_coverage)   [0,1]
    # --- peak counts ---
    n_matched_peaks: int
    n_lib_peaks: int
    n_emp_peaks_raw: int
    n_emp_peaks_filtered: int
    # --- filtered empirical spectrum (what was actually scored) ---
    filtered_mz_blob: bytes
    filtered_intensity_blob: bytes
    # --- ranking ---
    rank: int        # among candidates for this scan_id
    rank_group: int  # among rank=1 hits in this precursor m/z group


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def annotate_ms2(
    config: AnnotationConfig,
    n_processes: Optional[int] = None,
    progress_callback=None,
) -> Path:

    group_ids = group_ms2_by_filter(
        ms2_db_path=config.ms2_db_path, 
        groups_db_path=config.groups_db_path, 
        tolerance=config.group_precursor_tolerance, 
        tolerance_unit=config.group_precursor_tolerance_unit,
        polarity=config.polarity, 
        mz_center_method=MzCenterMethod(config.mz_center_method),
        return_groups=False,
        max_group_span_Da=config.max_group_span_Da,
        group_n=config.group_n
    )

    assign_ms2_to_groups(config.ms2_db_path, config.groups_db_path)

    con = _init_annotations_db(config.annotations_db_path)
    con.close()

    worker_fn = partial(_process_group, config=config)

    n_total, n_done = len(group_ids), 0 


    with Pool(processes=n_processes, initializer=init_worker, initargs=(config,)) as pool:
        for _ in pool.imap_unordered(worker_fn, group_ids, chunksize=10):
            n_done += 1
            if progress_callback:
                progress_callback(n_done, n_total)

    return config.annotations_db_path


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

def _process_group(group_id, config: AnnotationConfig) -> list[Annotation]:
    queries = _load_query_spectra(config.ms2_db_path, group_id)
    if not queries:
        return []

    global _LIBRARIES
    libs = _LIBRARIES

    results: list[Annotation] = []

    candidates = _gather_candidates(
            libs, precursor_mz = np.mean([query[-1] for query in queries]),
            tolerance = config.library_query_tolerance,
            tolerance_unit = config.library_query_tolerance_unit,
            polarity = config.polarity,
        )

    for scan_id, query_mz, query_intensity, _ in queries:
        
        scored: list[tuple[MatchResult, dict]] = []
        for cand in candidates:
            match = reverse_dot_product(
                query_mz=query_mz,
                query_intensity=query_intensity,
                library_mz=cand["mz"],
                library_intensity=cand["intensity"],
                ppm_tolerance=config.fragment_ppm_tolerance,
                mz_power=config.mz_power,
                int_power=config.int_power,
                noise_threshold=config.noise_threshold,
            )
            if match.lib_coverage >= config.min_matched_fraction:
                scored.append((match, cand))

        scored.sort(key=lambda t: t[0].score, reverse=True)

        for rank, (match, cand) in enumerate(scored[: config.top_n], start=1):
            results.append(Annotation(
                scan_id=scan_id,
                library_path=cand["library_path"],
                library_spectrum_id=cand["spectrum_id"],
                compound_id=cand["compound_id"],
                compound_name=cand["compound_name"],
                compound_formula=cand["compound_formula"],
                inchikey=cand["inchikey"],
                score=match.score,
                dot_product_score=match.dot_product_score,
                lib_coverage=match.lib_coverage,
                emp_coverage=match.emp_coverage,
                coverage_score=match.coverage_score,
                n_matched_peaks=match.n_matched_peaks,
                n_lib_peaks=match.n_lib_peaks,
                n_emp_peaks_raw=match.n_emp_peaks_raw,
                n_emp_peaks_filtered=match.n_emp_peaks_filtered,
                filtered_mz_blob=array_to_blob(match.filtered_mz),
                filtered_intensity_blob=array_to_blob(match.filtered_intensity),
                rank=rank,
                rank_group=0,
            ))

    for lib in libs:
        lib.engine.dispose()

    # assign rank_group
    top_hits = sorted(
        [a for a in results if a.rank == 1],
        key=lambda a: a.score, reverse=True,
    )
    scan_to_rank_group = {a.scan_id: i + 1 for i, a in enumerate(top_hits)}
    for a in results:
        a.rank_group = scan_to_rank_group.get(a.scan_id, 0)
    
    _write_annotations(config.annotations_db_path, results)

    return len(results)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _load_query_spectra(ms2_db_path, group_id):
    con = sqlite3.connect(f"file:{ms2_db_path}?mode=ro", uri=True)
    try:
        rows = con.execute("""
            SELECT scan_id, mz_array, intensity_array, isolation_window_target 
            FROM ms2_scans WHERE group_id = ?;
            """,
            group_id,
        ).fetchall()
    finally:
        con.close()
    return [
        (sid, blob_to_array(mz), blob_to_array(i), pmz)
        for sid, mz, i, pmz in rows
    ]


def _load_library(path):
    from libviz.core.library import Library
    return Library.load_from_db(path)


def _gather_candidates(
    libs: list,
    precursor_mz: float,
    tolerance: float,
    tolerance_unit: str,
    polarity: Optional[str],
) -> list[dict]:
    """
    Query each library for spectra whose precursor_mz falls within
    ppm_tolerance of the query's precursor_mz.

    Uses `Library.get_spectra_in_mz_range(mz_min, mz_max, polarity)`,
    a public libviz method that returns a list of dicts with at least:
        spectrum_id, compound_id, compound_name, compound_formula,
        inchikey, mz (array-like), intensity (array-like)
    """
    if tolerance_unit == "ppm":
        tol_da = precursor_mz * tolerance * 1e-6
    else:
        tol_da = tolerance
    lo, hi = precursor_mz - tol_da, precursor_mz + tol_da

    candidates: list[dict] = []
    for lib in libs:
        for s in lib.get_spectra_in_mz_range(mz_min=lo, mz_max=hi, polarity=polarity):
            candidates.append({
                "library_path":     str(lib.path),
                "spectrum_id":      s["spectrum_id"],
                "compound_id":      s["compound_id"],
                "compound_name":    s["compound_name"],
                "compound_formula": s["compound_formula"],
                "inchikey":         s["inchikey"],
                "mz":               np.asarray(s["mz"],        dtype=np.float64),
                "intensity":        np.asarray(s["intensity"],  dtype=np.float64),
            })
    return candidates


def _init_annotations_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("""
        CREATE TABLE IF NOT EXISTS annotations (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id               INTEGER NOT NULL,
            library_path          TEXT    NOT NULL,
            library_spectrum_id   INTEGER NOT NULL,
            compound_id           INTEGER NOT NULL,
            compound_name         TEXT,
            compound_formula      TEXT,
            inchikey              TEXT,
            score                 REAL    NOT NULL,
            dot_product_score     REAL    NOT NULL,
            lib_coverage          REAL    NOT NULL,
            emp_coverage          REAL    NOT NULL,
            coverage_score        REAL    NOT NULL,
            n_matched_peaks       INTEGER NOT NULL,
            n_lib_peaks           INTEGER NOT NULL,
            n_emp_peaks_raw       INTEGER NOT NULL,
            n_emp_peaks_filtered  INTEGER NOT NULL,
            filtered_mz_blob      BLOB    NOT NULL,
            filtered_intensity_blob BLOB  NOT NULL,
            rank                  INTEGER NOT NULL,
            rank_group            INTEGER NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_ann_scan_id    ON annotations(scan_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ann_score      ON annotations(score)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ann_rank_group ON annotations(rank_group)")
    con.commit()
    return con


def _write_annotations(path: Path, annotations: list[Annotation]) -> None:
    if not annotations:
        return
    con = sqlite3.connect(path)
    try:
        con.executemany(
            """
            INSERT INTO annotations (
                scan_id, library_path, library_spectrum_id, compound_id,
                compound_name, compound_formula, inchikey,
                score, dot_product_score, lib_coverage, emp_coverage, coverage_score,
                n_matched_peaks, n_lib_peaks, n_emp_peaks_raw, n_emp_peaks_filtered,
                filtered_mz_blob, filtered_intensity_blob,
                rank, rank_group
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [(
                a.scan_id, a.library_path, a.library_spectrum_id, a.compound_id,
                a.compound_name, a.compound_formula, a.inchikey,
                a.score, a.dot_product_score, a.lib_coverage,
                a.emp_coverage, a.coverage_score,
                a.n_matched_peaks, a.n_lib_peaks,
                a.n_emp_peaks_raw, a.n_emp_peaks_filtered,
                a.filtered_mz_blob, a.filtered_intensity_blob,
                a.rank, a.rank_group,
            ) for a in annotations],
        )
        con.commit()
    finally:
        con.close()
