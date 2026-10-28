"""MS2 cleaning (ADR 0064): drop background fragments and fragments above
the precursor from every MS2 spectrum before it's used.

Raw databases are never changed: a spectrum is cleaned each time it is
read (grouping, annotation, the plotted "filtered" spectrum), from its raw
peaks plus what the grouping stage stored in the analysis database:

* ``ms2_background_fragments`` — fragments shared by many *precursors*
  (one row per fragment cluster found in at least
  ``TABLE_MIN_FRACTION`` of a polarity's precursors, or removed).
* ``ms2_cleaning`` — the one settings row the cleaning was run with.

Background fragments are counted per precursor, not per scan: an imaging
run fragments the same precursor in hundreds of pixels, so per scan one
abundant ion's own real fragments would look like background. A
precursor is the feature a scan's precursor m/z is nearest to (within
``assoc_ppm``), or for a scan matching no feature, its precursor m/z
binned to ``assoc_ppm``. It counts a fragment once if any of its scans
has it. The table is always computed; ``background_enabled`` only decides
whether the fragments at or above ``background_min_percent`` are removed.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..utils.logging_utils import log_call

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


#: A fragment cluster is listed in ``ms2_background_fragments`` when it
#: is in at least this fraction of its polarity's precursors (or removed).
TABLE_MIN_FRACTION = 0.05


def _log_bins(mz: np.ndarray, ppm: float) -> np.ndarray:
    """m/z as integer bins of `ppm` width on a log scale."""
    return np.floor(np.log(mz) / np.log1p(ppm * 1e-6)).astype(np.int64)


def precursor_keys(
    precursor_mz: np.ndarray, features: np.ndarray, feature_ids: np.ndarray, assoc_ppm: float
) -> tuple[np.ndarray, np.ndarray]:
    """For each scan precursor m/z: a precursor key (the nearest feature's id
    within `assoc_ppm`, else a negative m/z bin) and the m/z to treat as the
    precursor (that feature's m/z, else the scan's own)."""
    precursor_mz = np.asarray(precursor_mz, dtype=float)
    features = np.asarray(features, dtype=float)
    keys = -1 - _log_bins(np.where(precursor_mz > 0, precursor_mz, 1.0), assoc_ppm)
    ref = precursor_mz.copy()
    if features.size:
        order = np.argsort(features)
        sorted_mz = features[order]
        idx = np.clip(np.searchsorted(sorted_mz, precursor_mz), 1, sorted_mz.size) - 1
        right = np.clip(idx + 1, 0, sorted_mz.size - 1)
        nearer = np.abs(sorted_mz[right] - precursor_mz) < np.abs(sorted_mz[idx] - precursor_mz)
        idx = np.where(nearer, right, idx)
        near_mz = sorted_mz[idx]
        hit = np.abs(near_mz - precursor_mz) <= precursor_mz * assoc_ppm * 1e-6
        keys = np.where(hit, np.asarray(feature_ids)[order][idx], keys)
        ref = np.where(hit, near_mz, ref)
    return keys, ref


@dataclass
class BackgroundFragments:
    """One polarity's fragment clusters: centre m/z, how many precursors
    have it, and the polarity's precursor count."""

    mz: np.ndarray
    n_precursors: np.ndarray
    total: int


def count_background(
    mz_arrays: list[np.ndarray], keys: np.ndarray, ppm: float
) -> BackgroundFragments:
    """Cluster every fragment within `ppm` and count, per cluster, the
    distinct precursor `keys` (one per spectrum) that have it.

    Fragments are sorted and split where two neighbours are more than
    `ppm` apart; a cluster wider than twice `ppm` (a chain of noise peaks)
    is cut into pieces twice `ppm` wide so it can't swallow a whole m/z range.
    """
    keys = np.asarray(keys)
    total = int(np.unique(keys).size) if keys.size else 0
    sizes = np.array([a.size for a in mz_arrays], dtype=np.int64)
    if not sizes.sum():
        return BackgroundFragments(np.empty(0), np.empty(0, dtype=np.int64), total)
    mz = np.concatenate([np.asarray(a, dtype=float) for a in mz_arrays])
    _, key_idx = np.unique(keys, return_inverse=True)  # 0..n_keys-1, per spectrum
    owner = np.repeat(key_idx.ravel(), sizes)
    keep = mz > 0
    mz, owner = mz[keep], owner[keep]
    order = np.argsort(mz, kind="stable")
    mz, owner = mz[order], owner[order]
    logs = np.log(mz)
    step = np.log1p(ppm * 1e-6)
    cluster = np.concatenate([[0], np.cumsum(np.diff(logs) > step)])
    starts = logs[np.searchsorted(cluster, cluster)]  # first log m/z of each cluster
    piece = np.floor((logs - starts) / step / 2).astype(np.int64)
    # (cluster, piece) is already sorted: number the distinct pairs in order.
    split = cluster * (int(piece.max()) + 1) + piece
    cluster = np.concatenate([[0], np.cumsum(np.diff(split) != 0)])
    n_clusters = int(cluster[-1]) + 1
    centre = np.bincount(cluster, weights=mz, minlength=n_clusters) / np.bincount(
        cluster, minlength=n_clusters
    )
    n_keys = int(owner.max()) + 1
    # Distinct (cluster, precursor) pairs. Sort + diff: np.unique on
    # int64 is ~10x slower here (numpy 2.4).
    pairs = np.sort(cluster.astype(np.int64) * n_keys + owner)
    pairs = pairs[np.concatenate([[True], np.diff(pairs) != 0])]
    counts = np.bincount(pairs // n_keys, minlength=n_clusters)
    return BackgroundFragments(centre, counts, total)


@dataclass
class Ms2Cleaner:
    """Cleans spectra with the stored settings and removed fragments."""

    background_enabled: bool = False
    background_ppm: float = 10.0
    above_precursor_enabled: bool = False
    above_precursor_ppm: float = 10.0
    removed: dict = field(default_factory=dict)  # polarity -> sorted m/z

    @property
    def active(self) -> bool:
        return self.above_precursor_enabled or (self.background_enabled and bool(self.removed))

    def keep_mask(
        self, mz: np.ndarray, polarity: str | None, precursor_mz: float | None,
        charge: int | None = None,
    ) -> np.ndarray:
        mz = np.asarray(mz, dtype=float)
        keep = np.ones(mz.size, dtype=bool)
        if (
            self.above_precursor_enabled and precursor_mz
            # A fragment of a 2+ precursor can lie above its m/z.
            and (charge is None or abs(int(charge)) <= 1)
        ):
            keep &= mz <= precursor_mz * (1 + self.above_precursor_ppm * 1e-6)
        removed = self.removed.get(polarity) if self.background_enabled else None
        if removed is not None and removed.size and mz.size:
            idx = np.clip(np.searchsorted(removed, mz), 1, removed.size) - 1
            nearest = np.minimum(
                np.abs(mz - removed[idx]),
                np.abs(mz - removed[np.clip(idx + 1, 0, removed.size - 1)]),
            )
            keep &= nearest > mz * self.background_ppm * 1e-6
        return keep

    def clean(self, mz, intensity, polarity, precursor_mz, charge=None):
        """`(mz, intensity)` without the removed peaks."""
        if mz is None or not self.active:
            return mz, intensity
        keep = self.keep_mask(mz, polarity, precursor_mz, charge)
        return np.asarray(mz)[keep], np.asarray(intensity)[keep]


@log_call
def clean_scans(
    scans: list[dict],
    features: np.ndarray,
    feature_ids: np.ndarray,
    *,
    assoc_ppm: float,
    settings,
) -> tuple[Ms2Cleaner, list[tuple]]:
    """Count background fragments over `scans` (the grouper's scan dicts)
    and clean each scan's arrays in place (`n_peaks` too).

    Returns the cleaner and the ``ms2_background_fragments`` rows
    ``(polarity, mz, n_precursors, fraction, removed)``.
    """
    precursor = np.array(
        [s.get("precursor_mz") or s.get("isolation_window_target") or 0.0 for s in scans],
        dtype=float,
    )
    keys, ref_mz = precursor_keys(precursor, features, feature_ids, assoc_ppm)
    polarities = [normalize_polarity(s.get("polarity")) for s in scans]

    cleaner = Ms2Cleaner(
        background_enabled=settings.background_enabled,
        background_ppm=settings.background_ppm,
        above_precursor_enabled=settings.above_precursor_enabled,
        above_precursor_ppm=settings.above_precursor_ppm,
    )
    rows: list[tuple] = []
    for pol in sorted(set(polarities), key=str):
        idx = [i for i, p in enumerate(polarities) if p == pol]
        arrays = [
            np.asarray(scans[i]["mz_array"]) if scans[i].get("mz_array") is not None
            else np.empty(0)
            for i in idx
        ]
        bg = count_background(arrays, keys[idx], settings.background_ppm)
        if not bg.total:
            continue
        fraction = bg.n_precursors / bg.total
        removed = fraction * 100 >= settings.background_min_percent
        cleaner.removed[pol] = np.sort(bg.mz[removed])
        listed = removed | (fraction >= TABLE_MIN_FRACTION)
        rows.extend(
            (pol, float(m), int(n), float(f), bool(r))
            for m, n, f, r in zip(bg.mz[listed], bg.n_precursors[listed], fraction[listed], removed[listed])
        )

    if cleaner.active:
        for scan, pol, ref in zip(scans, polarities, ref_mz):
            if scan.get("mz_array") is None:
                continue
            scan["mz_array"], scan["intensity_array"] = cleaner.clean(
                scan["mz_array"], scan["intensity_array"], pol,
                float(ref) if ref > 0 else None, scan.get("precursor_charge"),
            )
            scan["n_peaks"] = int(np.asarray(scan["mz_array"]).size)
    return cleaner, rows


def persist_cleaning(con: sqlite3.Connection, settings, rows: list[tuple]) -> None:
    """Replace ``ms2_cleaning`` / ``ms2_background_fragments``."""
    con.execute("DELETE FROM ms2_background_fragments")
    con.execute("DELETE FROM ms2_cleaning")
    con.execute(
        "INSERT INTO ms2_cleaning (background_enabled, background_ppm, "
        "background_min_percent, above_precursor_enabled, above_precursor_ppm) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            int(settings.background_enabled), settings.background_ppm,
            settings.background_min_percent, int(settings.above_precursor_enabled),
            settings.above_precursor_ppm,
        ),
    )
    con.executemany(
        "INSERT INTO ms2_background_fragments (polarity, mz, n_precursors, fraction, removed) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )


def load_cleaner(analysis_db_path: Path | str) -> Ms2Cleaner:
    """The cleaner the grouping stage stored; a no-op one for an analysis
    without MS2 cleaning (older, or not run yet)."""
    try:
        con = sqlite3.connect(f"file:{analysis_db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return Ms2Cleaner()
    try:
        row = con.execute(
            "SELECT background_enabled, background_ppm, above_precursor_enabled, "
            "above_precursor_ppm FROM ms2_cleaning"
        ).fetchone()
        if row is None:
            return Ms2Cleaner()
        removed: dict = {}
        for pol, mz in con.execute(
            "SELECT polarity, mz FROM ms2_background_fragments WHERE removed = 1 ORDER BY mz"
        ):
            removed.setdefault(pol, []).append(mz)
    except sqlite3.OperationalError:
        return Ms2Cleaner()
    finally:
        con.close()
    return Ms2Cleaner(
        background_enabled=bool(row[0]), background_ppm=float(row[1]),
        above_precursor_enabled=bool(row[2]), above_precursor_ppm=float(row[3]),
        removed={p: np.asarray(v, dtype=float) for p, v in removed.items()},
    )
