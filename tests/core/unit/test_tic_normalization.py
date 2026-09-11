from __future__ import annotations

from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
import pytest
from scipy.sparse import csr_matrix

from msianalyzer.core.utils.tic_normalization import (
    TicNormalizationResult,
    run_tic_normalization,
)


def _make_sample(tic, X, pixel_ids=None):
    X = np.asarray(X, dtype=np.float32)
    n_obs, n_vars = X.shape
    if pixel_ids is None:
        pixel_ids = list(range(n_obs))
    obs = pd.DataFrame({"tic": tic}, index=[str(p) for p in pixel_ids])
    var = pd.DataFrame(
        {"mz": [100.0 + 100.0 * i for i in range(n_vars)]},
        index=[f"mz_{100.0 + 100.0 * i:.4f}" for i in range(n_vars)],
    )
    return ad.AnnData(X=csr_matrix(X), obs=obs, var=var)


def _write_samples(tmp_path: Path, samples: dict[str, ad.AnnData]) -> dict[str, Path]:
    paths = {}
    for name, adata in samples.items():
        path = tmp_path / f"{name}.h5ad"
        adata.write_h5ad(path)
        paths[name] = path
    return paths


def test_run_tic_normalization_computes_expected_values(tmp_path):
    sample_a = _make_sample(tic=[10.0, 20.0], X=[[100.0, 0.0], [50.0, 200.0]])
    sample_b = _make_sample(tic=[30.0, 0.0], X=[[10.0, 10.0], [5.0, 5.0]])
    paths = _write_samples(tmp_path, {"a": sample_a, "b": sample_b})

    result = run_tic_normalization(paths, out_dir=tmp_path)

    assert isinstance(result, TicNormalizationResult)
    assert result.n_samples == 2
    assert result.n_pixels == 4
    # all tic values [10, 20, 30, 0] -> median of the sorted array
    assert result.median_tic == pytest.approx(15.0)
    assert result.merged_path == tmp_path / "merged.h5ad"
    assert result.merged_path.exists()

    a = ad.read_h5ad(paths["a"])
    b = ad.read_h5ad(paths["b"])

    # raw layer is an untouched copy of X
    np.testing.assert_allclose(a.layers["raw"].toarray(), [[100.0, 0.0], [50.0, 200.0]])
    np.testing.assert_allclose(b.layers["raw"].toarray(), [[10.0, 10.0], [5.0, 5.0]])

    # pixel 0 of sample a: tic=10, norm_factor=10/15, inv=1.5
    expected_a0 = np.log1p(np.array([100.0, 0.0]) * 1.5)
    # pixel 1 of sample a: tic=20, norm_factor=20/15, inv=0.75
    expected_a1 = np.log1p(np.array([50.0, 200.0]) * 0.75)
    np.testing.assert_allclose(
        a.layers["TIC"].toarray(), [expected_a0, expected_a1], rtol=1e-5
    )

    # pixel 0 of sample b: tic=30, norm_factor=2.0, inv=0.5
    expected_b0 = np.log1p(np.array([10.0, 10.0]) * 0.5)
    # pixel 1 of sample b: tic=0 -> normalized to all zero, no division by zero
    expected_b1 = np.array([0.0, 0.0])
    np.testing.assert_allclose(
        b.layers["TIC"].toarray(), [expected_b0, expected_b1], rtol=1e-5
    )


def test_run_tic_normalization_merged_has_sample_column_and_layers(tmp_path):
    sample_a = _make_sample(tic=[10.0], X=[[1.0, 2.0]])
    sample_b = _make_sample(tic=[10.0], X=[[3.0, 4.0]])
    paths = _write_samples(tmp_path, {"a": sample_a, "b": sample_b})

    run_tic_normalization(paths, out_dir=tmp_path)

    merged = ad.read_h5ad(tmp_path / "merged.h5ad")
    assert merged.n_obs == 2
    assert set(merged.obs["sample"]) == {"a", "b"}
    assert "raw" in merged.layers
    assert "TIC" in merged.layers


def test_run_tic_normalization_all_zero_tic_normalizes_to_zero(tmp_path):
    sample_a = _make_sample(tic=[0.0, 0.0], X=[[5.0, 6.0], [7.0, 8.0]])
    paths = _write_samples(tmp_path, {"a": sample_a})

    result = run_tic_normalization(paths, out_dir=tmp_path)

    assert result.median_tic == 0.0
    a = ad.read_h5ad(paths["a"])
    np.testing.assert_allclose(a.layers["TIC"].toarray(), np.zeros((2, 2)))
    # raw is untouched even though TIC collapses to zero
    np.testing.assert_allclose(a.layers["raw"].toarray(), [[5.0, 6.0], [7.0, 8.0]])


def test_run_tic_normalization_preserves_original_x(tmp_path):
    sample_a = _make_sample(tic=[10.0], X=[[42.0, 7.0]])
    paths = _write_samples(tmp_path, {"a": sample_a})

    run_tic_normalization(paths, out_dir=tmp_path)

    a = ad.read_h5ad(paths["a"])
    np.testing.assert_allclose(a.X.toarray(), [[42.0, 7.0]])
