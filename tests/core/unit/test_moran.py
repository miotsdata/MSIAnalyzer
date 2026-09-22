from __future__ import annotations

import anndata as ad
import numpy as np
import pandas as pd
import pytest
from scipy.sparse import csr_matrix

from msianalyzer.core.spatial.moran import compute_spatial_autocorr


def _make_grid_sample(n: int = 10, seed: int = 0) -> ad.AnnData:
    """A square pixel grid with one spatially-clustered feature (a blob
    centered on the grid) and one spatially-random feature, so Moran's I
    should clearly separate the two.
    """
    rng = np.random.default_rng(seed)
    xs, ys = np.meshgrid(np.arange(n), np.arange(n))
    xs, ys = xs.ravel(), ys.ravel()
    n_pix = xs.size

    center = n / 2
    clustered = np.exp(-((xs - center) ** 2 + (ys - center) ** 2) / 8.0) * 1000.0
    random_feat = rng.random(n_pix) * 1000.0

    X = np.stack([clustered, random_feat], axis=1).astype(np.float32)
    obs = pd.DataFrame({"x": xs, "y": ys, "tic": np.full(n_pix, 1000.0)})
    var = pd.DataFrame(
        {"mz": [100.0, 200.0]}, index=["mz_100.0000", "mz_200.0000"]
    )

    adata = ad.AnnData(X=csr_matrix(X), obs=obs, var=var)
    adata.obsm["spatial"] = np.stack([xs, ys], axis=1).astype(float)
    adata.layers["TIC"] = adata.X.copy()
    return adata


def test_compute_spatial_autocorr_separates_clustered_from_random(tmp_path):
    path = tmp_path / "sample.h5ad"
    _make_grid_sample().write_h5ad(path)

    compute_spatial_autocorr(path, layer="TIC")

    out = ad.read_h5ad(path)
    assert "moranI" in out.uns
    moran = out.uns["moranI"]
    assert set(moran.index) == {"mz_100.0000", "mz_200.0000"}
    assert "I" in moran.columns

    clustered_i = moran.loc["mz_100.0000", "I"]
    random_i = moran.loc["mz_200.0000", "I"]
    assert clustered_i > 0.5
    assert clustered_i > random_i


def test_compute_spatial_autocorr_falls_back_when_layer_missing(tmp_path, caplog):
    adata = _make_grid_sample()
    del adata.layers["TIC"]
    path = tmp_path / "sample.h5ad"
    adata.write_h5ad(path)

    compute_spatial_autocorr(path, layer="TIC")

    out = ad.read_h5ad(path)
    assert "moranI" in out.uns
    assert "not found, falling back" in caplog.text
