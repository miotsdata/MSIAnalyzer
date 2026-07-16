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
  - ms2_groups  : one row per group (group_id, mz_center, mz_min, mz_max)
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
from numpy import argmax

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
class Ms2Group:
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

def group_ms2_by_precursor_ppm(
    ms2_db_path: Path | str,
    groups_db_path: Path | str,
    ppm_tolerance: float = 10.0,
    polarity: Optional[str] = None,
    mz_center_method: MzCenterMethod = MzCenterMethod.MEAN,
):
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
    """
    ms2_db_path  = Path(ms2_db_path)
    groups_db_path = Path(groups_db_path)

    if ppm_tolerance <= 0:
        raise ValueError(f"ppm_tolerance must be positive, got {ppm_tolerance}")

    _initialize_groups_db(ms2_db_path = ms2_db_path, groups_db_path = groups_db_path, 
                          ppm_tolerance = ppm_tolerance, polarity = polarity, 
                          mz_center_method = mz_center_method)

    scan_rows: Iterator[tuple[float, float]] = _fetch_scan_rows(ms2_db_path, polarity)

    with sqlite3.connect(groups_db_path) as con:
        for group in _cluster_ms2_scans(scan_rows, ppm_tolerance, mz_center_method):
            _write_group(group, con)
        con.commit()


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

    # Write into ms2_scans    

    with sqlite3.connect(ms2_db_path) as ms2_con:
        ms2_con.execute("PRAGMA journal_mode=WAL")
        ms2_con.execute("PRAGMA synchronous=NORMAL")
        ms2_con.execute("UPDATE ms2_scans SET group_id = NULL;")
        ms2_con.execute("DROP TABLE IF EXISTS group_ranges;")

        ms2_con.execute(
            "ATTACH DATABASE ? AS groups_db",
            (str(groups_db_path),),
        )

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
            FROM groups_db.ms2_groups;
        """)

        #ms2_con.execute("DETACH DATABASE groups_db")

        ms2_con.execute("""
            UPDATE ms2_scans
            SET group_id = (
                SELECT group_id
                FROM group_ranges
                WHERE precursor_mz BETWEEN observed_min AND observed_max
            )
        """)

        ms2_con.commit()

    # Log the command in ms2.db — include groups DB filename for traceability
    log_command(
        ms2_db_path,
        command_name="assign_ms2_groups",
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
    ppm_tolerance: float,
    mz_center_method: MzCenterMethod,
) -> Iterator[Ms2Group | None]:
    """
    Cluster rows into MS2 groups based on their m/z values and yields one group at a time.

    Parameters
    ----------
    rows : Iterator of (precursor_mz, precursor_intensity) pairs
    ppm_tolerance : float
        The tolerance for grouping scans by m/z in parts per million.
    mz_center_method : MzCenterMethod
        The method to use for calculating the center m/z of each group.
    """
    is_first = True
    mzs_group = []
    intensities_group = []

    for mz, intensity in rows:
        if is_first:
            mzs_group.append(mz)
            intensities_group.append(intensity)
            is_first = False
            continue

        if len(mzs_group) == 1:
            center = mzs_group[0]
            gap_to_last_ok = gap_to_first_ok  = ppm_diff(mz, center) <= ppm_tolerance * 2
        else:
            center = _calculate_mz_center(mzs_group, intensities_group, mz_center_method)
            gap_to_last_ok  = ppm_diff(mz, mzs_group[-1]) <= ppm_tolerance
            gap_to_first_ok = ppm_diff(mz, mzs_group[0]) <= ppm_tolerance * 2

        if gap_to_last_ok:
            logging.debug("Gap to last %.4f ok for mz %.4f", mzs_group[-1], mz)
            mzs_group.append(mz)
            intensities_group.append(intensity)

            if not gap_to_first_ok:
                logging.debug("Gap to first %.4f NOT ok for mz %.4f", mzs_group[0], mz)
                mzs_to_yield, intensities_to_yield, mzs_group, intensities_group = _split_mzs_intensities(mzs_group, intensities_group)
                logging.debug("Created two groups: %s %s", mzs_to_yield, mzs_group)

                group = _prepare_group(mzs_to_yield, intensities_to_yield, mz_center_method, ppm_tolerance)
                yield group

        else:
            min_observed_mz = min(mzs_group)
            arithmetical_min_mz = center - (center * ppm_tolerance / 1e6)
            old_mzs = mzs_group
            old_intensities = intensities_group
            mzs_group = [mz]
            intensities_group = [intensity]

            if min_observed_mz < arithmetical_min_mz:
                mzs_group1, intensities_group1, mzs_group2, intensities_group2 = _split_mzs_intensities(old_mzs, old_intensities)

                yield _prepare_group(mzs_group1, intensities_group1, mz_center_method, ppm_tolerance)

                yield _prepare_group(mzs_group2, intensities_group2, mz_center_method, ppm_tolerance)
            else:
                yield _prepare_group(old_mzs, old_intensities, mz_center_method, ppm_tolerance)

    if len(mzs_group) == 0:
        return None

    yield _prepare_group(mzs_group, intensities_group, mz_center_method, ppm_tolerance)


def _split_mzs_intensities(mzs: list[float], intensities: list[float]) -> tuple[list[float], list[float], list[float], list[float]]:
    ppm_diffs = [
        (mzs[i] - mzs[i - 1]) / mzs[i] * 1e6
        for i in range(1, len(mzs))
    ]
    max_ppm_diff = argmax(ppm_diffs)

    mzs_group1 = mzs[:max_ppm_diff + 1]
    mzs_group2 = mzs[(max_ppm_diff + 1):]
    intensities_group1 = intensities[:max_ppm_diff + 1]
    intensities_group2 = intensities[(max_ppm_diff + 1) :]

    return (
        mzs_group1,
        intensities_group1,
        mzs_group2,
        intensities_group2,
    )


def _prepare_group(mzs_group: list[float], intensities_group: list[float], mz_center_method: MzCenterMethod, ppm_tolerance: float) -> Ms2Group:
    min_observed_mz = min(mzs_group)
    max_observed_mz = max(mzs_group)
    center = _calculate_mz_center(mzs_group, intensities_group, mz_center_method)
    arithmetical_min_mz = center - (center * ppm_tolerance / 1e6)
    arithmetical_max_mz = center + (center * ppm_tolerance / 1e6)

    return Ms2Group(
                group_id=None,
                mz_min=arithmetical_min_mz,
                mz_max=arithmetical_max_mz,
                mz_center=center,
                observed_min=min_observed_mz,
                observed_max=max_observed_mz
            )


def _write_group(group: Ms2Group, con: sqlite3.Connection):
    """
    Write a single Ms2Group to the ms2_groups table in the groups database.

    Parameters
    ----------
    group : Ms2Group
        The group to write to the database.
    con : sqlite3.Connection
        The SQLite connection to the groups database.
    """
    con.execute(
        "INSERT INTO ms2_groups (mz_center, mz_min, mz_max, observed_min, observed_max) VALUES (?, ?, ?, ?, ?)",
        (group.mz_center, group.mz_min, group.mz_max, group.observed_min, group.observed_max),
    )


# ---------------------------------------------------------------------------
# Create groups db
# ---------------------------------------------------------------------------

def _initialize_groups_db(
    ms2_db_path: Path,
    groups_db_path: Path,
    ppm_tolerance: float,
    polarity: Optional[str],
    mz_center_method: MzCenterMethod,
) -> None:
    """
    Create the groups SQLite database from scratch.

    Schema
    ------
    metadata  : key/value store (source db, parameters)
    ms2_groups: one row per group

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
                ("ppm_tolerance",      str(ppm_tolerance)),
                ("polarity",           polarity or ""),
                ("mz_center_method",   mz_center_method),
                ("creation_date",      datetime.now(timezone.utc).isoformat()),
            ],
        )

        # --- ms2_groups ---
        con.execute("""
            CREATE TABLE ms2_groups (
                group_id   INTEGER PRIMARY KEY,
                mz_center  REAL    NOT NULL,
                mz_min     REAL    NOT NULL,
                mz_max     REAL    NOT NULL,
                observed_min REAL   NOT NULL,
                observed_max REAL   NOT NULL
            )
        """)
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_groups_mz_center ON ms2_groups(mz_center)"
        )

        con.commit()

    finally:
        con.close()

# ---------------------------------------------------------------------------
# Source data fetch
# ---------------------------------------------------------------------------

def _fetch_scan_rows(
    ms2_db_path: Path,
    polarity: Optional[str],
) -> Iterator[tuple[float, float]]:
    """
    Yield (precursor_mz, precursor_intensity) pairs sorted ascending
    by precursor_mz. Scans with NULL precursor_mz are excluded.

    The SQLite connection stays open for the lifetime of the generator
    and is closed automatically when the generator is exhausted or
    garbage collected.
    """
    con = sqlite3.connect(f"file:{ms2_db_path}?mode=ro", uri=True)
    try:
        if polarity is not None:
            cursor = con.execute(
                """
                SELECT precursor_mz, precursor_intensity
                FROM ms2_scans
                WHERE precursor_mz IS NOT NULL
                  AND polarity = ?
                ORDER BY precursor_mz ASC
                """,
                (polarity.upper(),),
            )
        else:
            cursor = con.execute(
                """
                SELECT precursor_mz, precursor_intensity
                FROM ms2_scans
                WHERE precursor_mz IS NOT NULL
                ORDER BY precursor_mz ASC
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
        return sum(mzs) / len(mzs)

    if mz_center_method == MzCenterMethod.MEDIAN:
        return statistics.median(mzs)

    if mz_center_method == MzCenterMethod.HIGHEST_PEAK:
        return mzs[argmax(intensities)]

    else:
        raise ValueError(f"Unknown mz_center_method: {mz_center_method}")
