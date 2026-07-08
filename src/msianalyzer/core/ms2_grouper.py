"""
ms2_grouper.py
Sequential ppm-based clustering of MS2 spectra by precursor m/z.

No pixel/spatial logic, no library/matching logic — this module only
answers: "which scan_ids belong to the same precursor m/z cluster?"

Mirrors the LAG()-based SQL pattern: sort by precursor_mz, start a new
group whenever the gap to the previous entry exceeds the ppm tolerance.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


@dataclass
class Ms2Group:
    """
    A cluster of MS2 scans whose precursor m/z values are sequentially
    within `ppm_tolerance` of their neighbours.

    Attributes
    ----------
    group_id : int
        0-indexed group number, assigned in m/z ascending order.
    scan_ids : list[int]
        MS2 scan_ids belonging to this group, in m/z ascending order.
    mz_min, mz_max : float
        Precursor m/z range spanned by the group.
    mz_center : float
        Mean precursor m/z of the group — useful as the representative
        value for library candidate filtering.
    """

    group_id: int
    scan_ids: list[int]
    mz_min: float
    mz_max: float
    mz_center: float

    @property
    def size(self) -> int:
        return len(self.scan_ids)


def ppm_diff(a: float, b: float) -> float:
    """ppm difference of *a* relative to *b* (the 'expected'/previous value)."""
    if b == 0:
        return float("inf")
    return abs(a - b) / b * 1e6


def group_ms2_by_precursor_ppm(
    ms2_db_path: Path | str,
    ppm_tolerance: float = 10.0,
) -> list[Ms2Group]:
    """
    Sequentially cluster all MS2 scans in *ms2_db_path* by precursor m/z.

    Parameters
    ----------
    ms2_db_path : Path | str
        Path to the MS2 SQLite database produced by ``MzmlParser``.
    ppm_tolerance : float
        Maximum ppm gap between consecutive (sorted) precursor m/z values
        to be considered part of the same group.

    Returns
    -------
    list[Ms2Group]
        Groups in ascending precursor m/z order. Scans with a NULL
        precursor_mz are skipped (cannot be grouped or matched).
    """

    logger.debug("Grouping MS2 with a PPM tolerance of %d", ppm_tolerance)

    con = sqlite3.connect(f"file:{Path(ms2_db_path)}?mode=ro", uri=True)
    try:
        rows = con.execute(
            """
            SELECT scan_id, precursor_mz
            FROM ms2_scans
            WHERE precursor_mz IS NOT NULL
            ORDER BY precursor_mz ASC
            """
        ).fetchall()
        print(len(rows), "MS2 scans read from", ms2_db_path)
    finally:
        con.close()

    return _cluster_rows(rows, ppm_tolerance)


def _cluster_rows(
    rows: list[tuple[int, float]],
    ppm_tolerance: float,
) -> list[Ms2Group]:
    """Pure clustering logic, separated for easy unit testing."""
    if not rows:
        return []

    groups: list[Ms2Group] = []
    current_scan_ids: list[int] = [rows[0][0]]
    current_mzs: list[float] = [rows[0][1]]
    prev_mz = rows[0][1]

    for scan_id, mz in rows[1:]:
        if ppm_diff(mz, prev_mz) <= ppm_tolerance:
            current_scan_ids.append(scan_id)
            current_mzs.append(mz)
        else:
            groups.append(_finalize_group(len(groups), current_scan_ids, current_mzs))
            current_scan_ids = [scan_id]
            current_mzs = [mz]
        prev_mz = mz

    groups.append(_finalize_group(len(groups), current_scan_ids, current_mzs))
    print(len(groups), "MS2 groups formed with total of", sum(g.size for g in groups), "scans")
    return groups


def _finalize_group(group_id: int, scan_ids: list[int], mzs: list[float]) -> Ms2Group:
    return Ms2Group(
        group_id=group_id,
        scan_ids=list(scan_ids),
        mz_min=min(mzs),
        mz_max=max(mzs),
        mz_center=sum(mzs) / len(mzs),
    )
