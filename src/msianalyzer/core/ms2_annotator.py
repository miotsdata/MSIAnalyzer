"""
ms2_annotator.py
Orchestrates MS2 annotation against libviz libraries:

  1. Group MS2 scans by precursor m/z (sequential ppm clustering)
  2. For each group, filter library candidates by precursor m/z window
  3. Compute reverse dot product for every (query, candidate) pair
  4. Keep the best match(es) per query scan
  5. Persist results to an annotations SQLite database

Parallelized across groups (multiprocessing.Pool). Each worker opens its
own SQLite connections and its own libviz Library instances — nothing is
shared across process boundaries.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from functools import partial
from multiprocessing import Pool
from pathlib import Path
from typing import Optional
import logging

import numpy as np

from msianalyzer.core.mzml_parser import blob_to_array
from msianalyzer.core.ms2_grouper import Ms2Group, group_ms2_by_precursor_ppm
from msianalyzer.core.spectral_matching import reverse_dot_product, MatchResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public config / result types
# ---------------------------------------------------------------------------

@dataclass
class AnnotationConfig:
    """
    All tunable parameters for an annotation run, bundled so they can be
    pickled cleanly across multiprocessing worker boundaries.

    Attributes
    ----------
    ms2_db_path : Path
        Path to the MS2 SQLite database (from MzmlParser).
    library_paths : list[Path]
        Paths to one or more libviz `.db` library files.
    precursor_ppm_tolerance : float
        Used both for sequential grouping AND for filtering library
        candidates by precursor m/z window.
    fragment_ppm_tolerance : float
        Used inside the reverse dot product for fragment peak alignment.
    mz_power, int_power : float
        Reverse dot product peak-weighting exponents.
    min_matched_fraction : float
        Minimum fraction of library peaks that must be matched for a
        candidate to be considered (filters out spurious high-score-but-
        low-coverage matches). 0.0 disables this filter.
    top_n : int
        Number of best matches to keep per query scan (1 = best only).
    polarity : str | None
        If set ('POSITIVE' / 'NEGATIVE'), restricts library candidates
        to that polarity. None = no polarity filtering.
    """

    ms2_db_path: Path
    library_paths: list[Path]
    precursor_ppm_tolerance: float = 5
    fragment_ppm_tolerance: float = 5
    mz_power: float = 2.0
    int_power: float = 0.5
    min_matched_fraction: float = 0.0
    top_n: int = 1
    polarity: Optional[str] = None


@dataclass
class Annotation:
    """One stored annotation result — a query scan matched to a library spectrum."""

    scan_id: int
    library_path: str
    library_spectrum_id: int
    compound_id: int
    compound_name: str
    compound_formula: str
    inchikey: str
    score: float
    n_matched_peaks: int
    n_library_peaks: int
    matched_fraction: float
    rank: int       # 1 = best match for this scan_id within its candidates
    rank_group: int # 1 = best score across ALL scan_ids in this precursor m/z group


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def annotate_ms2(
    config: AnnotationConfig,
    annotations_db_path: Path | str,
    n_processes: Optional[int] = None,
    progress_callback=None,
) -> Path:
    """
    Run full MS2 annotation: group, match, persist.

    Parameters
    ----------
    config : AnnotationConfig
    annotations_db_path : Path | str
        Output SQLite database for annotation results.
    n_processes : int | None
        Worker count for multiprocessing.Pool. None = os.cpu_count().
    progress_callback : callable | None
        Optional fn(n_groups_done: int, n_groups_total: int). Called from
        the main process as results stream back — safe to wrap in a Qt
        signal at the GUI layer.

    Returns
    -------
    Path
        The annotations_db_path, for chaining.
    """
    annotations_db_path = Path(annotations_db_path)

    groups = group_ms2_by_precursor_ppm(
        config.ms2_db_path, ppm_tolerance=config.precursor_ppm_tolerance
    )

    con = _init_annotations_db(annotations_db_path)
    con.close()  # main process doesn't hold it open during the pool run

    worker_fn = partial(_process_group, config=config)

    n_total = len(groups)
    n_done = 0

    with Pool(processes=n_processes) as pool:
        for group_annotations in pool.imap_unordered(worker_fn, groups):
            _write_annotations(annotations_db_path, group_annotations)
            n_done += 1
            if progress_callback:
                progress_callback(n_done, n_total)

    return annotations_db_path


# ---------------------------------------------------------------------------
# Worker-side processing (runs in a separate process)
# ---------------------------------------------------------------------------

def _process_group(group: Ms2Group, config: AnnotationConfig) -> list[Annotation]:
    """
    Process a single MS2 group: load query spectra, filter library
    candidates, score them, return the best match(es) per query scan.

    rank        — position of this hit among all candidates for this scan_id
    rank_group  — position of this hit among all rank=1 hits in the group,
                  ranked by score descending. Lets you find the single
                  best-explained scan in a precursor m/z cluster.

    This function (and everything it calls) must be safe to run in a
    worker process: it opens its own DB / library connections and shares
    no state with the parent.
    """
    queries = _load_query_spectra(config.ms2_db_path, group.scan_ids)
    if not queries:
        return []

    libs = [_load_library(p) for p in config.library_paths]

    # Collect rank=1 annotations first so we can assign rank_group after
    results: list[Annotation] = []

    for scan_id, query_mz, query_intensity, precursor_mz in queries:
        candidates = _gather_candidates(
            libs,
            precursor_mz=precursor_mz,
            ppm_tolerance=config.precursor_ppm_tolerance,
            polarity=config.polarity,
        )

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
            )
            if match.matched_fraction >= config.min_matched_fraction:
                scored.append((match, cand))

        scored.sort(key=lambda t: t[0].score, reverse=True)

        for rank, (match, cand) in enumerate(scored[: config.top_n], start=1):
            results.append(
                Annotation(
                    scan_id=scan_id,
                    library_path=cand["library_path"],
                    library_spectrum_id=cand["spectrum_id"],
                    compound_id=cand["compound_id"],
                    compound_name=cand["compound_name"],
                    compound_formula=cand["compound_formula"],
                    inchikey=cand["inchikey"],
                    score=match.score,
                    n_matched_peaks=match.n_matched_peaks,
                    n_library_peaks=match.n_library_peaks,
                    matched_fraction=match.matched_fraction,
                    rank=rank,
                    rank_group=0,   # placeholder — filled below
                )
            )

    for lib in libs:
        lib.engine.dispose()

    # ------------------------------------------------------------------
    # Assign rank_group: rank all rank=1 hits by score across the group,
    # then propagate the same rank_group to their rank>1 siblings so
    # every annotation row for a scan shares the same rank_group as its
    # best hit.
    # ------------------------------------------------------------------
    top_hits = sorted(
        [a for a in results if a.rank == 1],
        key=lambda a: a.score,
        reverse=True,
    )
    scan_to_rank_group = {a.scan_id: i + 1 for i, a in enumerate(top_hits)}

    # Scans with no annotation (no candidates passed filters) get no row,
    # so the dict is complete as-is.
    for a in results:
        a.rank_group = scan_to_rank_group.get(a.scan_id, 0)

    return results


def _load_query_spectra(
    ms2_db_path: Path, scan_ids: list[int]
) -> list[tuple[int, np.ndarray, np.ndarray, float]]:
    """Fetch (scan_id, mz_array, intensity_array, precursor_mz) for given scan_ids."""
    con = sqlite3.connect(f"file:{ms2_db_path}?mode=ro", uri=True)
    try:
        placeholders = ",".join("?" * len(scan_ids))
        rows = con.execute(
            f"""
            SELECT scan_id, mz_array, intensity_array, precursor_mz
            FROM ms2_scans
            WHERE scan_id IN ({placeholders})
            """,
            scan_ids,
        ).fetchall()
    finally:
        con.close()

    return [
        (scan_id, blob_to_array(mz_blob), blob_to_array(int_blob), precursor_mz)
        for scan_id, mz_blob, int_blob, precursor_mz in rows
    ]


def _load_library(path: Path):
    """Open a fresh libviz Library instance — one per worker, never shared."""
    from libviz.core.library import Library
    return Library.load_from_db(path)


def _gather_candidates(
    libs: list,
    precursor_mz: float,
    ppm_tolerance: float,
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
    tol_da = precursor_mz * ppm_tolerance * 1e-6
    lo, hi = precursor_mz - tol_da, precursor_mz + tol_da

    candidates: list[dict] = []
    for lib in libs:
        spectra = lib.get_spectra_in_mz_range(
            mz_min=lo, mz_max=hi, polarity=polarity
        )
        for s in spectra:
            candidates.append(
                {
                    "library_path": str(lib.path),
                    "spectrum_id": s["spectrum_id"],
                    "compound_id": s["compound_id"],
                    "compound_name": s["compound_name"],
                    "compound_formula": s["compound_formula"],
                    "inchikey": s["inchikey"],
                    "mz": np.asarray(s["mz"], dtype=np.float64),
                    "intensity": np.asarray(s["intensity"], dtype=np.float64),
                }
            )
    return candidates


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _init_annotations_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("""
        CREATE TABLE IF NOT EXISTS annotations (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id              INTEGER NOT NULL,
            library_path         TEXT NOT NULL,
            library_spectrum_id  INTEGER NOT NULL,
            compound_id          INTEGER NOT NULL,
            compound_name        TEXT,
            compound_formula     TEXT,
            inchikey             TEXT,
            score                REAL NOT NULL,
            n_matched_peaks      INTEGER NOT NULL,
            n_library_peaks      INTEGER NOT NULL,
            matched_fraction     REAL NOT NULL,
            rank                 INTEGER NOT NULL,
            rank_group           INTEGER NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_ann_scan_id   ON annotations(scan_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ann_score     ON annotations(score)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ann_rank_group ON annotations(rank_group)")
    con.commit()
    return con


def _write_annotations(path: Path, annotations: list[Annotation]) -> None:
    """Append a batch of annotations. Opened/closed per call — safe for
    the main process to call repeatedly as worker results stream in."""
    if not annotations:
        return
    con = sqlite3.connect(path)
    try:
        con.executemany(
            """
            INSERT INTO annotations
              (scan_id, library_path, library_spectrum_id, compound_id,
               compound_name, compound_formula, inchikey,
               score, n_matched_peaks, n_library_peaks, matched_fraction,
               rank, rank_group)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    a.scan_id, a.library_path, a.library_spectrum_id, a.compound_id,
                    a.compound_name, a.compound_formula, a.inchikey,
                    a.score, a.n_matched_peaks, a.n_library_peaks, a.matched_fraction,
                    a.rank, a.rank_group,
                )
                for a in annotations
            ],
        )
        con.commit()
    finally:
        con.close()