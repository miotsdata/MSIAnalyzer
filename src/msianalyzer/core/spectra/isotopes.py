"""Isotope detection across the aligned features (ADR 54).

Adapted from LCAnalyzer's ``core/peaks/isotopes.py``: the same mass and
chain rules, a tighter M+1 intensity bound, and co-elution replaced by
co-localization of the ion images. Runs right after the ``.h5ad`` files are
built and before MS2 grouping; isotopes move from ``features`` to
``feature_isotopes`` and their columns leave the ``.h5ad``, so no later
stage sees them (ADR 55).
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse

from msianalyzer.core import analysis_db
from msianalyzer.core.config.config import IsotopesConfig
from msianalyzer.core.utils.lazy_import import lazy_module
from msianalyzer.core.utils.logging_utils import log_call

ad = lazy_module("anndata")  # GUI startup must not import it (ADR 52)

logger = logging.getLogger(__name__)

C13_DELTA = 1.0033548  # 13C - 12C (Da)
_C13_PER_CARBON = 0.0107  # natural 13C abundance
# The most carbon-dense common organic ions carry about one carbon per 14 Da
# (CH2 chains; DAN itself is 15.8). LCAnalyzer's all-carbon m/12 allowed
# physically impossible links on MSI data (ADR 54).
_MIN_DA_PER_CARBON = 14.0


@dataclass(frozen=True)
class IsotopeLink:
    """One isotope found in one sample, as column indices of that sample's images."""

    mono: int
    isotope: int
    k: int
    z: int
    ratio: float
    corr: float


@dataclass(frozen=True)
class FeatureLink:
    """An `IsotopeLink` translated to feature ids, the unit of the vote."""

    mono_id: int
    isotope_id: int
    k: int
    z: int
    ratio: float
    corr: float


@dataclass(frozen=True)
class AcceptedIsotope:
    """An isotope feature the cross-sample vote kept (a `feature_isotopes` row)."""

    feature_id: int
    mono_feature_id: int
    isotope_index: int
    charge: int
    n_samples_linked: int
    n_samples_both: int
    intensity_ratio: float
    spatial_corr: float


@dataclass(frozen=True)
class IsotopeRunSummary:
    n_features_before: int
    n_candidates: int
    n_isotopes: int


def max_m1_ratio(mz: float, charge: int) -> float:
    """Upper bound of intensity(M+1) / intensity(M): one carbon per
    `_MIN_DA_PER_CARBON` of the ion's mass, +10 % and +0.02 slack for
    intensity noise. A higher ratio means another ion overlaps the M+1
    position, so the feature there is not (only) an isotope."""
    return 1.1 * _C13_PER_CARBON * (mz * charge / _MIN_DA_PER_CARBON) + 0.02


def _in_window(smz: np.ndarray, target, ppm: float):
    lo = np.searchsorted(smz, np.asarray(target) * (1 - ppm * 1e-6))
    hi = np.searchsorted(smz, np.asarray(target) * (1 + ppm * 1e-6), side="right")
    return lo, hi


def candidate_indices(mz: np.ndarray, cfg: IsotopesConfig) -> np.ndarray:
    """Indices of the features that can take part in an isotope chain: those
    with a partner at M+1 for some charge, and the partners at M+1 … M+n of
    those. Only these need ion images."""
    mz = np.asarray(mz, dtype=float)
    order = np.argsort(mz)
    smz = mz[order]
    keep = np.zeros(mz.size, dtype=bool)
    for z in range(1, cfg.max_charge + 1):
        lo, hi = _in_window(smz, mz + C13_DELTA / z, cfg.ppm)
        monos = np.flatnonzero(hi > lo)
        keep[monos] = True
        for k in range(1, cfg.max_isotopes + 1):
            lo, hi = _in_window(smz, mz[monos] + k * C13_DELTA / z, cfg.ppm)
            for a, b in zip(lo, hi):
                keep[order[a:b]] = True
    return np.flatnonzero(keep)


def spatial_corr(mono: np.ndarray, other: np.ndarray) -> float:
    """Pearson r of two ion images over the pixels where `mono` is non-zero.

    Pixels where the mono is absent are left out: otherwise any two
    on-tissue ions correlate through the shared off-tissue zeros. -1 when
    fewer than 3 such pixels, or either image is flat on them.
    """
    mono = np.asarray(mono, dtype=float)
    other = np.asarray(other, dtype=float)
    on = mono > 0
    if on.sum() < 3:
        return -1.0
    a, b = mono[on], other[on]
    if np.ptp(a) == 0 or np.ptp(b) == 0:
        return -1.0
    return float(np.corrcoef(a, b)[0, 1])


def link_isotopes(mz: np.ndarray, images, cfg: IsotopesConfig) -> list[IsotopeLink]:
    """Link isotopes to their monoisotopic feature within one sample.

    Features are visited from the most intense (mean image intensity) down;
    each not yet claimed is a mono candidate. For each charge 1…`max_charge`
    it collects M+1, M+2, … while a feature exists that is

    * within `cfg.ppm` of ``mono + k × 1.0033548 / z``,
    * less intense than the mono (and, for M+1, not above `max_m1_ratio`),
    * co-localized: `spatial_corr` ≥ `cfg.min_spatial_corr` (best wins).

    The charge with the longest chain wins (ties: lower charge).

    Args:
        mz: One m/z per image column.
        images: Pixels × features intensities, dense or scipy sparse.
    """
    mz = np.asarray(mz, dtype=float)
    is_sparse = sparse.issparse(images)
    X = images.tocsc() if is_sparse else np.asarray(images, dtype=float)
    h = np.asarray(X.mean(axis=0)).ravel()
    columns: dict[int, np.ndarray] = {}

    def col(j: int) -> np.ndarray:
        if j not in columns:
            columns[j] = X[:, j].toarray().ravel() if is_sparse else X[:, j]
        return columns[j]

    by_mz = np.argsort(mz)
    smz = mz[by_mz]
    claimed = np.zeros(mz.size, dtype=bool)
    links: list[IsotopeLink] = []

    for i in np.argsort(-h):
        if claimed[i] or h[i] <= 0:
            continue
        best: list[tuple[int, int, float]] = []
        best_z = 0
        for z in range(1, cfg.max_charge + 1):
            chain: list[tuple[int, int, float]] = []
            for k in range(1, cfg.max_isotopes + 1):
                a, b = _in_window(smz, mz[i] + k * C13_DELTA / z, cfg.ppm)
                found, best_r = None, cfg.min_spatial_corr
                for j in by_mz[a:b]:
                    if claimed[j] or j == i or h[j] >= h[i]:
                        continue
                    if k == 1 and h[j] / h[i] > max_m1_ratio(mz[i], z):
                        continue
                    r = spatial_corr(col(i), col(j))
                    if r >= best_r:
                        found, best_r = int(j), r
                if found is None:
                    break
                chain.append((found, k, best_r))
            if len(chain) > len(best):
                best, best_z = chain, z
        if best:
            for j, k, r in best:
                links.append(IsotopeLink(int(i), j, k, best_z, float(h[j] / h[i]), r))
                claimed[j] = True
            claimed[i] = True
    return links


def vote_isotope_links(
    links_by_sample: dict[str, list[FeatureLink]],
    present: pd.DataFrame,
    protected: set[int],
) -> list[AcceptedIsotope]:
    """One decision per feature from the per-sample links.

    A link (mono, isotope, k, z) is kept when it was found in more than
    half of the samples where both features are present. An isotope won by
    several monos keeps the most voted (ties: lower k). A feature voted as
    an isotope can't be a mono, M+k needs M+(k−1) of the same mono, and
    `protected` features are never isotopes.

    Args:
        present: Bool, feature ids × sample names: whether the feature was
            detected in that sample.
    """
    votes: dict[tuple[int, int, int, int], list[FeatureLink]] = defaultdict(list)
    for links in links_by_sample.values():
        for link in links:
            votes[(link.mono_id, link.isotope_id, link.k, link.z)].append(link)

    passed = []
    for key, ls in votes.items():
        mono, iso, _, _ = key
        if iso in protected:
            continue
        n_both = int((present.loc[mono] & present.loc[iso]).sum())
        if 2 * len(ls) > n_both:
            passed.append((key, ls, n_both))

    winner: dict[int, tuple] = {}
    for item in sorted(passed, key=lambda c: (-len(c[1]), c[0][2])):
        winner.setdefault(item[0][1], item)

    accepted: dict[tuple[int, int, int], AcceptedIsotope] = {}
    for (mono, iso, k, z), ls, n_both in sorted(winner.values(), key=lambda c: c[0][2]):
        if mono in winner or (k > 1 and (mono, k - 1, z) not in accepted):
            continue
        accepted[(mono, k, z)] = AcceptedIsotope(
            feature_id=iso,
            mono_feature_id=mono,
            isotope_index=k,
            charge=z,
            n_samples_linked=len(ls),
            n_samples_both=n_both,
            intensity_ratio=float(np.median([l.ratio for l in ls])),
            spatial_corr=float(np.median([l.corr for l in ls])),
        )
    return sorted(accepted.values(), key=lambda a: (a.mono_feature_id, a.isotope_index))


def _column_keys(mz) -> np.ndarray:
    """m/z as matching keys: `.h5ad` columns carry the feature m/z verbatim."""
    return np.round(np.asarray(mz, dtype=float), 6)


@log_call(source="analysis_db_path")
def run_detect_isotopes(
    analysis_db_path: Path | str,
    h5ad_paths: dict[str, Path | str],
    cfg: IsotopesConfig,
    *,
    command_id: int | None = None,
    keep_mzs: set[float] = frozenset(),
) -> IsotopeRunSummary:
    """Find isotope features, move them from `features` to `feature_isotopes`
    and drop their columns from every `.h5ad` (ADR 55).

    The ion images are the `.h5ad` matrices, read one sample at a time. A
    file is rewritten (to a temporary file, then `os.replace`) only when it
    has isotope columns to drop.

    Args:
        h5ad_paths: Sample name (the `features.members_json` key) -> that
            sample's `.h5ad`.
        keep_mzs: Columns never dropped, e.g. the normalization reference m/z.
    """
    with analysis_db.connect(analysis_db_path) as con:
        rows = con.execute(
            "SELECT feature_id, mz, members_json FROM features ORDER BY mz"
        ).fetchall()
    ids = np.array([r[0] for r in rows], dtype=int)
    mz = np.array([r[1] for r in rows], dtype=float)
    present = pd.DataFrame(
        [{s: v is not None for s, v in json.loads(r[2]).items()} for r in rows],
        index=ids,
    ).reindex(columns=list(h5ad_paths), fill_value=False)

    cand = candidate_indices(mz, cfg)
    links_by_sample: dict[str, list[FeatureLink]] = {}
    for sample, path in h5ad_paths.items():
        here = cand[present[sample].to_numpy()[cand]]
        if here.size < 2:
            continue
        adata = ad.read_h5ad(path)
        column = {k: i for i, k in enumerate(_column_keys(adata.var["mz"]))}
        here = np.array([i for i in here if _column_keys(mz[i]) in column])
        images = adata.X[:, [column[_column_keys(mz[i])] for i in here]]
        links_by_sample[sample] = [
            FeatureLink(int(ids[here[l.mono]]), int(ids[here[l.isotope]]), l.k, l.z, l.ratio, l.corr)
            for l in link_isotopes(mz[here], images, cfg)
        ]
        logger.debug(
            "%s: %d isotope link(s) among %d candidate features",
            sample, len(links_by_sample[sample]), here.size,
        )

    accepted = vote_isotope_links(
        links_by_sample, present, analysis_db.load_protected_feature_ids(analysis_db_path)
    )
    analysis_db.move_features_to_isotopes(analysis_db_path, accepted, command_id)

    mz_of = dict(zip(ids, mz))
    drop = set(_column_keys([mz_of[a.feature_id] for a in accepted])) - set(_column_keys(list(keep_mzs)))
    if drop:
        for path in h5ad_paths.values():
            _drop_columns(Path(path), drop)
    return IsotopeRunSummary(
        n_features_before=len(ids), n_candidates=int(cand.size), n_isotopes=len(accepted)
    )


def _drop_columns(path: Path, drop: set[float]) -> None:
    """Rewrite `path` without the columns whose m/z key is in `drop`."""
    adata = ad.read_h5ad(path)
    keep = ~np.isin(_column_keys(adata.var["mz"]), list(drop))
    if keep.all():
        return
    tmp = path.with_name(path.stem + ".tmp.h5ad")
    adata[:, keep].copy().write_h5ad(tmp)
    os.replace(tmp, path)
    logger.debug("%s: dropped %d isotope column(s)", path, int((~keep).sum()))
