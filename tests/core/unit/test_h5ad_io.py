"""Tests for core/utils/h5ad_io.py's nFeatures backfill."""

import anndata as ad
import h5py
import numpy as np
import pandas as pd
import pytest
from scipy.sparse import csr_matrix

from msianalyzer.core.utils import h5ad_io

_X = np.array([[1.0, 0.0, 2.0], [0.0, 0.0, 0.0], [3.0, 4.0, 5.0], [0.0, 6.0, 0.0]])


@pytest.mark.parametrize("sparse", [True, False])
def test_ensure_n_features_counts_detected_values_in_row_chunks(tmp_path, sparse):
    path = tmp_path / "s.h5ad"
    X = csr_matrix(_X) if sparse else _X
    ad.AnnData(X=X, obs=pd.DataFrame({"tic": [1.0, 2, 3, 4]}, index=list("abcd"))).write_h5ad(path)

    assert h5ad_io.ensure_n_features(path, chunk_rows=3) is True
    adata = ad.read_h5ad(path)
    assert adata.obs["nFeatures"].tolist() == [2, 0, 3, 1]
    assert list(adata.obs.columns) == ["tic", "nFeatures"]
    # Already there: nothing rewritten.
    assert h5ad_io.ensure_n_features(path) is False


def test_ensure_n_features_counts_explicit_zeros_as_not_detected(tmp_path):
    path = tmp_path / "s.h5ad"
    X = csr_matrix((np.array([0.0, 7.0]), np.array([0, 1]), np.array([0, 2])), shape=(1, 2))
    ad.AnnData(X=X).write_h5ad(path)
    h5ad_io.ensure_n_features(path)
    with h5py.File(path) as f:
        assert h5ad_io.read_obs_column(f, "nFeatures").tolist() == [1]
