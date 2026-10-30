"""
feature_correlation.py
Feature x feature correlation across one sample's pixels (ADR 66).

For every pair of features, how similarly their intensities vary from
pixel to pixel in one sample: Pearson on the layer's values, or Spearman
on their ranks. Samples are never pooled, the same rule as Moran's I.

The result is one `n_var x n_var` float32 matrix per sample, stored in
`adata.varp["featureCorrelation"]` (AnnData's slot for var x var
matrices), with `uns["featureCorrelation"] = {"method", "layer"}`. Only
those two elements are written; the rest of the file is untouched.

The matrix comes from pixel blocks: each block is densified and its
`block.T @ block` added to a running sum. Numpy's matrix product is
multithreaded, so that is the parallelism; memory stays at one block plus
the `n_var x n_var` sums. The full matrix is `n_var**2` floats per sample
(4 MB at 1,000 features, 400 MB at 10,000).
"""

from __future__ import annotations

import logging
from pathlib import Path

import h5py
import numpy as np
import pandas as pd
from scipy import sparse

from msianalyzer.core.utils import h5ad_io
from msianalyzer.core.utils.lazy_import import lazy_module
from msianalyzer.core.utils.logging_utils import log_call

ad = lazy_module("anndata")

logger = logging.getLogger(__name__)

VARP_KEY = "featureCorrelation"
UNS_KEY = "featureCorrelation"
METHODS = ("pearson", "spearman")

#: Values per densified pixel block (~160 MB as float64).
_CHUNK_ELEMENTS = 20_000_000


def _rank_columns(X):
    """Each column replaced by its ranks (ties averaged), for Spearman.

    A sparse, non-negative column keeps its sparsity: all its zeros share
    one rank, `(z + 1) / 2` for `z` zeros, and shifting the whole column by
    that rank (which leaves every correlation unchanged) puts them back at
    exactly 0. Anything else (dense, or with negative values) is ranked
    dense.
    """
    # Here, not at module level: scipy.stats costs GUI startup time, and
    # the GUI imports this module (ADR 52's startup guard).
    from scipy import stats

    if not sparse.issparse(X):
        return stats.rankdata(np.asarray(X, dtype=np.float64), axis=0)
    X = sparse.csc_matrix(X, dtype=np.float64, copy=True)
    X.eliminate_zeros()
    if X.nnz and X.data.min() < 0:
        return stats.rankdata(X.toarray(), axis=0)
    n = X.shape[0]
    for j in range(X.shape[1]):
        start, stop = X.indptr[j], X.indptr[j + 1]
        if start == stop:
            continue
        z = n - (stop - start)
        X.data[start:stop] = z + stats.rankdata(X.data[start:stop]) - (z + 1) / 2
    return X.tocsr()


def correlation_matrix(X, method: str = "pearson", *, chunk_rows: int | None = None) -> np.ndarray:
    """Correlation between every pair of columns of `X` (pixels x features).

    Args:
        X: Sparse or dense, `n_obs x n_var`.
        method: `"pearson"` or `"spearman"`.
        chunk_rows: Pixels per densified block; by default as many as fit
            in `_CHUNK_ELEMENTS` values.

    Returns:
        `n_var x n_var` float32, 1 on the diagonal. A feature with no
        variance (e.g. never detected) has a NaN row and column.
    """
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS}, got {method!r}")
    if method == "spearman":
        X = _rank_columns(X)
    if sparse.issparse(X):
        X = X.tocsr()
    n, p = X.shape
    chunk = chunk_rows or max(256, _CHUNK_ELEMENTS // max(p, 1))

    gram = np.zeros((p, p))
    sums = np.zeros(p)
    for start in range(0, n, chunk):
        block = X[start:start + chunk]
        block = block.toarray() if sparse.issparse(block) else np.asarray(block)
        block = block.astype(np.float64, copy=False)
        gram += block.T @ block
        sums += block.sum(axis=0)

    mean = sums / max(n, 1)
    cov = gram / max(n, 1) - np.outer(mean, mean)
    var = np.diag(cov).copy()
    # A constant feature leaves only rounding error behind after the
    # subtraction above; relative to its own mean square, that is zero.
    flat = var <= 1e-12 * np.maximum(np.diag(gram) / max(n, 1), 1e-300)
    std = np.sqrt(np.where(flat, 1.0, var))
    corr = cov / np.outer(std, std)
    np.clip(corr, -1.0, 1.0, out=corr)
    np.fill_diagonal(corr, 1.0)
    corr[flat, :] = np.nan
    corr[:, flat] = np.nan
    return corr.astype(np.float32)


def _read_matrix(f: h5py.File, layer: str, h5ad_path: Path):
    """The layer to correlate and the name actually used (`"X"` when it
    falls back)."""
    if layer != "X" and "layers" in f and layer in f["layers"]:
        return ad.io.read_elem(f["layers"][layer]), layer
    if layer != "X":
        logger.warning(
            "%s: layer %r not found, falling back to raw .X for feature correlation",
            h5ad_path, layer,
        )
    return ad.io.read_elem(f["X"]), "X"


@log_call(source="h5ad_path")
def compute_feature_correlation(
    h5ad_path: str | Path, layer: str = "TIC", method: str = "pearson"
) -> None:
    """Correlate every pair of features of one sample and write the matrix
    into its `.h5ad` (`varp["featureCorrelation"]`, plus
    `uns["featureCorrelation"]`), replacing any earlier result.

    Args:
        h5ad_path: One sample's `.h5ad`; only those two elements change.
        layer: The `.layers` key to correlate; `"X"`, or a missing layer,
            uses `.X`.
        method: `"pearson"` or `"spearman"`.
    """
    h5ad_path = Path(h5ad_path)
    with h5py.File(h5ad_path, "r") as f:
        X, used_layer = _read_matrix(f, layer, h5ad_path)
    corr = correlation_matrix(X, method)

    with h5ad_io.open_for_write(h5ad_path) as f:
        varp = f.require_group("varp")
        if "encoding-type" not in varp.attrs:
            varp.attrs["encoding-type"] = "dict"
            varp.attrs["encoding-version"] = "0.1.0"
        if VARP_KEY in varp:
            del varp[VARP_KEY]
        ad.io.write_elem(varp, VARP_KEY, corr)
        h5ad_io.write_uns(f, UNS_KEY, {"method": method, "layer": used_layer})


def _read_var_names(f: h5py.File) -> pd.Index:
    var = f["var"]
    index_key = var.attrs.get("_index", "_index")
    if isinstance(index_key, bytes):
        index_key = index_key.decode()
    return pd.Index(ad.io.read_elem(var[index_key]))


def read_correlation_row(h5ad_path: str | Path, var_name: str) -> pd.Series | None:
    """One feature's correlations with every feature of this sample, read
    as a single row of the stored matrix (not the whole matrix).

    Returns:
        Indexed by `var_names`; `None` when the file has no stored
        correlation or no such feature.
    """
    with h5py.File(h5ad_path, "r") as f:
        if "varp" not in f or VARP_KEY not in f["varp"]:
            return None
        names = _read_var_names(f)
        if var_name not in names:
            return None
        row = f["varp"][VARP_KEY][int(names.get_loc(var_name))]
    return pd.Series(np.asarray(row, dtype=np.float32), index=names)


def read_correlation_info(h5ad_path: str | Path) -> dict | None:
    """`uns["featureCorrelation"]` (`method`, `layer`), or `None` when the
    file has none."""
    with h5py.File(h5ad_path, "r") as f:
        return h5ad_io.read_uns(f, UNS_KEY)
