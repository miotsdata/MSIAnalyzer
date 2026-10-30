from __future__ import annotations

import anndata as ad
import numpy as np
import pandas as pd
import pytest
from scipy import stats
from scipy.sparse import csr_matrix

from msianalyzer.core.spatial.feature_correlation import (
    UNS_KEY,
    VARP_KEY,
    compute_feature_correlation,
    correlation_matrix,
    read_correlation_row,
)


def _sparse_log_like(n_pix: int = 120, n_var: int = 6, seed: int = 0) -> np.ndarray:
    """Zero-inflated, log1p-like intensities (the shape of a `TIC` layer):
    about half the pixels empty per feature, detected ones around 8-10,
    with ties among the detected values too."""
    rng = np.random.default_rng(seed)
    X = np.where(rng.random((n_pix, n_var)) < 0.5, np.round(rng.normal(9, 1, (n_pix, n_var)), 1), 0.0)
    X[:, 1] = np.where(X[:, 0] > 0, X[:, 0] * 0.5 + 1, 0.0)  # tied to feature 0
    return np.clip(X, 0, None)


@pytest.mark.parametrize("chunk_rows", [7, 1000])
def test_pearson_matches_numpy(chunk_rows):
    X = _sparse_log_like()
    got = correlation_matrix(csr_matrix(X), "pearson", chunk_rows=chunk_rows)
    np.testing.assert_allclose(got, np.corrcoef(X, rowvar=False), atol=1e-5)
    assert got.dtype == np.float32


@pytest.mark.parametrize("chunk_rows", [7, 1000])
def test_spearman_on_sparse_matches_scipy(chunk_rows):
    X = _sparse_log_like(seed=1)
    got = correlation_matrix(csr_matrix(X), "spearman", chunk_rows=chunk_rows)
    np.testing.assert_allclose(got, stats.spearmanr(X).statistic, atol=1e-5)


def test_spearman_on_dense_with_negative_values_matches_scipy():
    # e.g. a z-scored layer of an imported dataset: dense, signed
    X = np.random.default_rng(2).normal(0, 1, (50, 4))
    got = correlation_matrix(X, "spearman")
    np.testing.assert_allclose(got, stats.spearmanr(X).statistic, atol=1e-5)


def test_spearman_on_sparse_with_negative_values_matches_scipy():
    X = _sparse_log_like(seed=3)
    X[X > 9.5] *= -1
    got = correlation_matrix(csr_matrix(X), "spearman")
    np.testing.assert_allclose(got, stats.spearmanr(X).statistic, atol=1e-5)


def test_constant_feature_gives_nan_but_others_are_unaffected():
    X = _sparse_log_like()
    X[:, 3] = 0.0
    got = correlation_matrix(csr_matrix(X), "pearson")
    assert np.isnan(got[3]).all() and np.isnan(got[:, 3]).all()
    keep = [0, 1, 2, 4, 5]
    np.testing.assert_allclose(got[np.ix_(keep, keep)], np.corrcoef(X[:, keep], rowvar=False), atol=1e-5)


def test_unknown_method_raises():
    with pytest.raises(ValueError, match="method"):
        correlation_matrix(np.zeros((3, 2)), "kendall")


def _write_sample(path, X, *, layer="TIC"):
    var = pd.DataFrame({"mz": 100.0 + np.arange(X.shape[1])}, index=[f"mz_{100 + i}.0000" for i in range(X.shape[1])])
    adata = ad.AnnData(X=csr_matrix(X * 10), var=var)
    if layer:
        adata.layers[layer] = csr_matrix(X)
    adata.uns["moranI"] = pd.DataFrame({"I": np.zeros(X.shape[1])}, index=var.index)
    adata.write_h5ad(path)
    return adata


def test_compute_writes_varp_and_uns_in_place(tmp_path):
    X = _sparse_log_like()
    path = tmp_path / "s.h5ad"
    before = _write_sample(path, X)

    compute_feature_correlation(path, layer="TIC", method="spearman")

    out = ad.read_h5ad(path)
    np.testing.assert_allclose(out.varp[VARP_KEY], stats.spearmanr(X).statistic, atol=1e-5)
    assert out.uns[UNS_KEY] == {"method": "spearman", "layer": "TIC"}
    # nothing else touched
    np.testing.assert_array_equal(out.X.toarray(), before.X.toarray())
    assert "moranI" in out.uns


def test_compute_twice_overwrites(tmp_path):
    X = _sparse_log_like()
    path = tmp_path / "s.h5ad"
    _write_sample(path, X)
    compute_feature_correlation(path, layer="TIC", method="spearman")
    compute_feature_correlation(path, layer="TIC", method="pearson")
    out = ad.read_h5ad(path)
    np.testing.assert_allclose(out.varp[VARP_KEY], np.corrcoef(X, rowvar=False), atol=1e-5)
    assert out.uns[UNS_KEY]["method"] == "pearson"


def test_compute_falls_back_to_x_when_layer_missing(tmp_path, caplog):
    X = _sparse_log_like()
    path = tmp_path / "s.h5ad"
    _write_sample(path, X, layer=None)
    compute_feature_correlation(path, layer="TIC")
    out = ad.read_h5ad(path)
    np.testing.assert_allclose(out.varp[VARP_KEY], np.corrcoef(X * 10, rowvar=False), atol=1e-4)
    assert out.uns[UNS_KEY]["layer"] == "X"
    assert "not found, falling back" in caplog.text


def test_read_correlation_row(tmp_path):
    X = _sparse_log_like()
    path = tmp_path / "s.h5ad"
    _write_sample(path, X)
    assert read_correlation_row(path, "mz_100.0000") is None  # not computed yet

    compute_feature_correlation(path, layer="TIC")
    row = read_correlation_row(path, "mz_101.0000")
    assert list(row.index) == [f"mz_{100 + i}.0000" for i in range(6)]
    np.testing.assert_allclose(row.to_numpy(), np.corrcoef(X, rowvar=False)[1], atol=1e-5)
    assert read_correlation_row(path, "mz_999.0000") is None  # not a feature of this file

