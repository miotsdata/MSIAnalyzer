"""
ms2_grouper.py
Sequential ppm-based clustering of MS2 spectra by precursor m/z.

Algorithm
---------
Scans are sorted by precursor_mz ascending. A new group is started
whenever the ppm gap between the current scan and the group's representative m/z exceeds ppm_tolerance.
The representative m/z is computed according to mz_center_method.

Output
------
Results are written to a separate groups DB file containing:
  - mz_groups  : one row per group (group_id, mz_center, mz_min, mz_max)
  - metadata    : source ms2 db path, ppm_tolerance

The original ms2.db is never modified by grouping.
To apply a grouping back to ms2_scans.group_id, call assign_ms2_to_groups().
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterator, Optional
import statistics
import numpy as np

from msianalyzer.core.mzml_parser import log_command

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class MzCenterMethod(str, Enum):
    """How to compute the representative m/z for a group."""
    MEAN               = "mean"
    MEDIAN             = "median"
    HIGHEST_PEAK       = "highest_peak"  # m/z of the scan with the highest precursor intensity


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class MzGroup:
    """
    A cluster of MS2 scans whose precursor m/z values are within
    ppm_tolerance of their sequential neighbours, and whose total span
    does not exceed max_group_span_ppm.

    Attributes
    ----------
    group_id : int | None
        1-indexed, assigned in precursor m/z ascending order.
    mz_min, mz_max : float
        Precursor m/z range calculated based on mz_center and ppm_tolerance.
    mz_center : float
        Representative m/z (mean / median / highest_peak).
    """

    group_id: int | None
    mz_min: float
    mz_max: float
    mz_center: float
    observed_min: float
    observed_max: float

# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def group_ms2_by_filter(
    ms2_db_path: Path | str,
    groups_db_path: Path | str,
    tolerance: float = 10.0,
    tolerance_unit: str = "ppm",
    max_group_span_Da: float = 2,
    group_n: int | None = None,
    polarity: Optional[str] = None,
    mz_center_method: MzCenterMethod = MzCenterMethod.MEAN,
    return_groups: bool = True
) -> int:
    """
    Cluster MS2 scans by precursor m/z.

    Parameters
    ----------
    ms2_db_path : Path | str
        Source MS2 SQLite database (read-only — never modified).
    groups_db_path : Path | str
        Output path for the groups SQLite database. Created fresh;
        if it already exists it will be overwritten.
    ppm_tolerance : float
        Maximum ppm gap between consecutive sorted precursor m/z values
        to be placed in the same group.
    polarity : str | None
        'POSITIVE', 'NEGATIVE', or None (no filtering).
    mz_center_method : MzCenterMethod
        How to compute the representative m/z for each group.
    
        
    Returns
    -------
    Number of groups
    """
    logger.debug("Init group_ms2_by_filter.")
    ms2_db_path  = Path(ms2_db_path)
    groups_db_path = Path(groups_db_path)

    if tolerance < 0:
        raise ValueError(f"tolerance must be positive, got {tolerance}")
    
    _initialize_groups_db(ms2_db_path = ms2_db_path, groups_db_path = groups_db_path, 
                          tolerance = tolerance, tolerance_unit=tolerance_unit, polarity = polarity, 
                          mz_center_method = mz_center_method)

    scan_rows: Iterator[tuple[float, float]] = _fetch_scan_rows(ms2_db_path, polarity)

    if return_groups:
        groups = []

    with sqlite3.connect(groups_db_path) as con:
        for group in _cluster_ms2_scans(rows = scan_rows, tolerance=tolerance, tolerance_unit=tolerance_unit, 
                                        mz_center_method=mz_center_method, max_group_span_Da=max_group_span_Da,
                                        group_n=group_n):
            logger.debug("Writing group to file.")
            if return_groups:
                groups.append(group)
            _write_group(group, con)
        con.commit()
        group_ids = con.execute("SELECT group_id from mz_groups;").fetchall()


    logger.debug("Finished group_ms2_by_filter.")
    return groups if return_groups else group_ids


def assign_ms2_to_groups(
    ms2_db_path: Path | str,
    groups_db_path: Path | str,
) -> None:
    """
    Apply a groups DB to ms2_scans.group_id in the original ms2 database.

    Reads the scan_id → group_id mapping from *groups_db_path* and
    writes it into ms2_scans.group_id in *ms2_db_path*. Logs the
    assignment (including the groups DB filename) in ms2_db's commands
    table.

    This is the only operation that modifies the ms2.db.

    Parameters
    ----------
    ms2_db_path : Path | str
        The MS2 database whose ms2_scans.group_id column will be updated.
    groups_db_path : Path | str
        The groups database produced by group_ms2_by_precursor_ppm().
    """
    ms2_db_path  = Path(ms2_db_path)
    groups_db_path = Path(groups_db_path)

    logger.debug("Init assign_ms2_to_groups.")

    # Write into ms2_scans    

    with sqlite3.connect(ms2_db_path) as ms2_con:
        ms2_con.execute("PRAGMA journal_mode=WAL")
        ms2_con.execute("PRAGMA synchronous=NORMAL")
        ms2_con.execute("UPDATE ms2_scans SET group_id = NULL;")
        ms2_con.execute("DROP TABLE IF EXISTS group_ranges;")

        logger.debug("Attaching group database.")
        ms2_con.execute(
            "ATTACH DATABASE ? AS groups_db",
            (str(groups_db_path),),
        )

        logger.debug("Creating group_ranges table.")
        ms2_con.execute("""
            CREATE TABLE IF NOT EXISTS group_ranges (
                group_id  INTEGER PRIMARY KEY,
                mz_center REAL NOT NULL,
                mz_min    REAL NOT NULL,
                mz_max    REAL NOT NULL,
                observed_min REAL   NOT NULL,
                observed_max REAL   NOT NULL
            );
        """)
        ms2_con.execute("CREATE INDEX IF NOT EXISTS idx_group_ranges_range ON group_ranges(observed_min, observed_max);")

        logger.debug("Inserting groups into group_ranges table.")
        ms2_con.execute("""
            INSERT OR REPLACE INTO group_ranges (
                group_id,
                mz_center,
                mz_min,
                mz_max,
                observed_min,
                observed_max
            )
            SELECT
                group_id,
                mz_center,
                mz_min,
                mz_max,
                observed_min,
                observed_max
            FROM groups_db.mz_groups;
        """)

        #ms2_con.execute("DETACH DATABASE groups_db")
        logger.debug("Updating group_id in ms2 scans.")
        ms2_con.execute("""
            UPDATE ms2_scans
            SET group_id = group_ranges.group_id
            FROM group_ranges
            WHERE ms2_scans.isolation_window_target
                BETWEEN group_ranges.observed_min
                    AND group_ranges.observed_max;
        """)

        ms2_con.commit()

    # Log the command in ms2.db — include groups DB filename for traceability
    logger.debug("Adding command to command table.")
    log_command(
        ms2_db_path,
        command_name="assign_mz_groups",
        arguments={
            "groups_db":        groups_db_path.name,
            "groups_db_path":   str(groups_db_path.resolve()),
        },
    )

    logger.info("Assignment complete.")


# ---------------------------------------------------------------------------
# Core clustering (pure — no I/O, fully unit-testable)
# ---------------------------------------------------------------------------

def _cluster_ms2_scans(
    rows: Iterator[tuple[float, float]],
    tolerance: float,
    tolerance_unit: str,
    mz_center_method: MzCenterMethod,
    max_group_span_Da: float,
    group_n: int
) -> Iterator[MzGroup | None]:
    """
    Cluster rows into MS2 groups based on their m/z values and yields one group at a time.

    Parameters
    ----------
    rows : Iterator of (precursor_mz, precursor_intensity) pairs
    tolerance : float
        The tolerance for grouping scans (either in ppm or mz window).
    mz_center_method : MzCenterMethod
        The method to use for calculating the center m/z of each group.
    """
    logger.debug("Clustering ms scans.")
    is_first = True
    mzs_group = []
    intensities_group = []

    for mz, intensity in rows:
        if is_first:
            mzs_group.append(mz)
            intensities_group.append(intensity)
            is_first = False
            continue
        
        # Use fixed number of elements in group
        if group_n is not None:
            if len(mzs_group) <= group_n:
                logging.debug("Number of mzs in group (%d) less than limit (%d).", len(mzs_group), group_n)
                mzs_group.append(mz)
                intensities_group.append(intensity)
            else:
                logging.debug("Number of mzs in group reached the limit (%d).", group_n)
                old_mzs = mzs_group
                old_intensities = intensities_group
                mzs_group = [mz]
                intensities_group = [intensity]
                yield _prepare_group(mzs_group=old_mzs, intensities_group=old_intensities, mz_center_method=mz_center_method, 
                                        tolerance=tolerance, tolerance_unit=tolerance_unit)
        # Use specified window to group
        else:
            if tolerance_unit == "ppm":
                gap_to_last_ok  = ppm_diff(mz, mzs_group[-1]) <= tolerance * 2
            elif tolerance_unit == "Da":
                gap_to_last_ok = (mz - mzs_group[-1]) <= tolerance * 2
            else:
                raise ValueError(f"Invalid tolerance unit. Possible settings are 'ppm' or 'Da', found {tolerance_unit}")
            
            group_span_ok = (mz - mzs_group[0]) <= max_group_span_Da

            if gap_to_last_ok and group_span_ok:
                logging.debug("Gap to last %.4f ok for mz %.4f", mzs_group[-1], mz)
                mzs_group.append(mz)
                intensities_group.append(intensity)
            else:
                if not gap_to_last_ok:
                    logging.debug("Gap to last %.4f NOT ok for mz %.4f", mzs_group[-1], mz)
                elif not group_span_ok:
                    logging.debug("Gap to first %.4f NOT ok for mz %.4f", mzs_group[-1], mz)
                old_mzs = mzs_group
                old_intensities = intensities_group
                mzs_group = [mz]
                intensities_group = [intensity]

                if len(old_mzs) == 1 or _is_group_valid(mzs=old_mzs, intensities=old_intensities, 
                                mz_center_method=mz_center_method,
                                tolerance=tolerance, tolerance_unit=tolerance_unit):
                    logging.debug("Valid group")
                    yield _prepare_group(mzs_group=old_mzs, intensities_group=old_intensities, mz_center_method=mz_center_method, 
                                        tolerance=tolerance, tolerance_unit=tolerance_unit)
                else:
                    logging.debug("splitting group: %s", old_mzs)
                    groups = _split_group(mzs=old_mzs, intensities=old_intensities,mz_center_method=mz_center_method, 
                                        tolerance=tolerance, tolerance_unit=tolerance_unit)
                    yield from groups

    if len(mzs_group) == 0:
        return None

    yield _prepare_group(mzs_group, intensities_group, mz_center_method, tolerance, tolerance_unit)


def _is_group_valid(mzs: list[float], intensities: list[float],
                    mz_center_method: MzCenterMethod, tolerance: float,
                    tolerance_unit: str) -> bool:
    min_observed_mz = min(mzs)
    max_observed_mz = max(mzs)
    center = _calculate_mz_center(mzs, intensities, mz_center_method)
    if tolerance_unit == "ppm":
        arithmetical_min_mz = center - (center * tolerance / 1e6)
        arithmetical_max_mz = center + (center * tolerance / 1e6)
    elif tolerance_unit == "Da":
        arithmetical_min_mz = center - tolerance
        arithmetical_max_mz = center + tolerance
    else:
        raise ValueError(f"Invalid tolerance unit. Possible settings are 'ppm' or 'Da', found {tolerance_unit}")

    return (min_observed_mz >= arithmetical_min_mz) and (max_observed_mz <= arithmetical_max_mz)



def _split_group(mzs: list[float], intensities: list[float], 
                 mz_center_method: MzCenterMethod, 
                 tolerance: float,
                 tolerance_unit: str) -> list[MzGroup]:
    n_split = 2
    mzs = np.array(mzs)
    intensities = np.array(intensities)

    while True:
        centroids = np.linspace(mzs[0], mzs[-1], n_split + 2)[1:-1]

        while True:
            # Assign each mz to its nearest centroid
            distances = np.abs(mzs[:, None] - centroids[None, :])
            labels = np.argmin(distances, axis=1)

            # Compute new centroids
            new_centroids = centroids.copy()

            for i in range(n_split):
                mzs_cluster = mzs[labels == i]
                intensities_cluster = intensities[labels == i]
                if len(mzs_cluster):
                    new_centroids[i] = _calculate_mz_center(mzs_cluster, intensities_cluster,
                                                            mz_center_method)

            # Stop if converged
            if np.allclose(new_centroids, centroids):
                break

            centroids = new_centroids

        valid = True

        for i in range(n_split):
            mask = labels == i
            if sum(mask) == 0:
                continue

            if not _is_group_valid(mzs=mzs[mask].tolist(), intensities=intensities[mask].tolist(),
                                    mz_center_method=mz_center_method, 
                                    tolerance=tolerance, tolerance_unit=tolerance_unit):
                valid = False
                break

        # If all valid, exit the while loop
        if valid:
            break

        # Otherwise increase k and retry
        n_split += 1
        logging.debug("Increasing number of split to %d", n_split)

    logging.debug("Reached good group splitting: %d", n_split)
    groups = []
    for i in range(n_split):
        mask = labels == i
        if sum(mask) == 0:
            continue
        groups.append(
            _prepare_group(mzs_group=mzs[mask].tolist(), intensities_group=intensities[mask].tolist(),
                           mz_center_method=mz_center_method, tolerance=tolerance,
                           tolerance_unit=tolerance_unit)
        )

    return groups


def _prepare_group(mzs_group: list[float], intensities_group: list[float], mz_center_method: MzCenterMethod, 
                   tolerance: float,
                   tolerance_unit: str) -> MzGroup:
    min_observed_mz = min(mzs_group)
    max_observed_mz = max(mzs_group)
    center = _calculate_mz_center(mzs_group, intensities_group, mz_center_method)
    if tolerance_unit == "ppm":
        arithmetical_min_mz = center - (center * tolerance / 1e6)
        arithmetical_max_mz = center + (center * tolerance / 1e6)
    elif tolerance_unit == "Da":
        arithmetical_min_mz = center - tolerance
        arithmetical_max_mz = center + tolerance
    else:
        raise ValueError(f"Invalid tolerance unit. Possible settings are 'ppm' or 'Da', found {tolerance_unit}")

    return MzGroup(
                group_id=None,
                mz_min=arithmetical_min_mz,
                mz_max=arithmetical_max_mz,
                mz_center=center,
                observed_min=min_observed_mz,
                observed_max=max_observed_mz
            )


def _write_group(group: MzGroup, con: sqlite3.Connection):
    """
    Write a single MzGroup to the mz_groups table in the groups database.

    Parameters
    ----------
    group : MzGroup
        The group to write to the database.
    con : sqlite3.Connection
        The SQLite connection to the groups database.
    """
    con.execute(
        "INSERT OR IGNORE INTO mz_groups (mz_center, mz_min, mz_max, observed_min, observed_max) VALUES (?, ?, ?, ?, ?)",
        (group.mz_center, group.mz_min, group.mz_max, group.observed_min, group.observed_max),
    )


# ---------------------------------------------------------------------------
# Create groups db
# ---------------------------------------------------------------------------

def _initialize_groups_db(
    ms2_db_path: Path,
    groups_db_path: Path,
    tolerance: float,
    tolerance_unit: str,
    polarity: Optional[str],
    mz_center_method: MzCenterMethod,
) -> None:
    """
    Create the groups SQLite database from scratch.

    Schema
    ------
    metadata  : key/value store (source db, parameters)
    mz_groups: one row per group

    Raises
    ------
    FileExistsError
        If the groups DB already exists (to avoid accidental overwrites).
    """
    # Stop if existing groups DB is present (to avoid accidental overwrites)
    if groups_db_path.exists():
        raise FileExistsError(
            f"Groups DB already exists: {groups_db_path}. "
            "Delete it first if you want to re-run grouping."
        )
    
    logger.debug("Initializing db.")

    con = sqlite3.connect(groups_db_path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")

    try:
        # --- metadata ---
        con.execute("""
            CREATE TABLE metadata (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        con.executemany(
            "INSERT INTO metadata (key, value) VALUES (?, ?)",
            [
                ("source_ms2_db",      str(ms2_db_path.resolve())),
                ("tolerance",          str(tolerance)),
                ("tolerance_unit",    tolerance_unit),
                ("polarity",           polarity or ""),
                ("mz_center_method",   mz_center_method),
                ("creation_date",      datetime.now(timezone.utc).isoformat()),
            ],
        )

        # --- mz_groups ---
        con.execute("""
            CREATE TABLE mz_groups (
                group_id   INTEGER PRIMARY KEY,
                mz_center  REAL    NOT NULL,
                mz_min     REAL    NOT NULL,
                mz_max     REAL    NOT NULL,
                observed_min REAL   NOT NULL,
                observed_max REAL   NOT NULL
            )
        """)
        con.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_groups_mz_center ON mz_groups(mz_center)"
        )

        con.commit()

    finally:
        con.close()
        logger.debug("Succesfully initialized db.")

# ---------------------------------------------------------------------------
# Source data fetch
# ---------------------------------------------------------------------------

def _fetch_scan_rows(
    ms2_db_path: Path,
    polarity: Optional[str],
) -> Iterator[tuple[float, float]]:
    """
    Yield (isolation_window_target, precursor_intensity) pairs sorted ascending
    by isolation_window_target. Scans with NULL isolation_window_target are excluded.

    The SQLite connection stays open for the lifetime of the generator
    and is closed automatically when the generator is exhausted or
    garbage collected.
    """
    con = sqlite3.connect(f"file:{ms2_db_path}?mode=ro", uri=True)
    try:
        if polarity is not None:
            cursor = con.execute(
                """
                SELECT isolation_window_target, precursor_intensity
                FROM ms2_scans
                WHERE isolation_window_target IS NOT NULL
                  AND polarity = ?
                ORDER BY isolation_window_target ASC
                """,
                (polarity.upper(),),
            )
        else:
            cursor = con.execute(
                """
                SELECT isolation_window_target, precursor_intensity
                FROM ms2_scans
                WHERE isolation_window_target IS NOT NULL
                ORDER BY isolation_window_target ASC
                """
            )
        for row in cursor:
            yield row[0], row[1]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def ppm_diff(a: float, b: float) -> float:
    """Absolute ppm difference of *a* relative to *b*."""
    if b == 0:
        return float("inf")
    return abs(a - b) / b * 1e6


def _calculate_mz_center(
        mzs: list[float],
        intensities: list[float],
        mz_center_method: MzCenterMethod,
    ) -> float:

    if mz_center_method == MzCenterMethod.MEAN:
        return round(sum(mzs) / len(mzs), 4)

    if mz_center_method == MzCenterMethod.MEDIAN:
        return statistics.median(mzs)

    if mz_center_method == MzCenterMethod.HIGHEST_PEAK:
        return mzs[np.argmax(intensities)]

    else:
        raise ValueError(f"Unknown mz_center_method: {mz_center_method}")
