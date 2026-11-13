from __future__ import annotations

import anndata as ad
import numpy as np
import pandas as pd
import pytest
from scipy.sparse import csr_matrix

from msianalyzer.core.utils.fill_blanks import (
    add_filled_layer,
    fill_blanks,
    filled_layer_name,
    neighbor_matrix,
    parse_filled_layer_name,
    remove_filled_layer,
    required_neighbors,
)
from msianalyzer.core.utils.tic_normalization import run_normalization


def _grid_indices(width, height):
    y_index, x_index = np.divmod(np.arange(width * height), width)
    return x_index, y_index


def test_center_hole_is_filled_with_the_mean_of_its_neighbors():
    # 3x3 grid, one feature, every pixel detected except the center.
    values = np.arange(1.0, 10.0)
    values[4] = 0.0
    x_index, y_index = _grid_indices(3, 3)

    filled, n_filled = fill_blanks(
        csr_matrix(values.reshape(-1, 1)), neighbor_matrix(x_index, y_index, 1), 0.6
    )

    assert n_filled == 1
    expected = values.copy()
    expected[4] = np.mean([1, 2, 3, 4, 6, 7, 8, 9])
    np.testing.assert_allclose(filled.toarray().ravel(), expected)


def test_edge_of_a_region_does_not_grow():
    # Left column detected, the rest 0: each middle-column pixel has at
    # most 3 of 5-8 neighbors detected, below the 60% guard.
    values = np.zeros((3, 3))
    values[:, 0] = 5.0
    x_index, y_index = _grid_indices(3, 3)

    filled, n_filled = fill_blanks(
        csr_matrix(values.reshape(-1, 1)), neighbor_matrix(x_index, y_index, 1), 0.6
    )

    assert n_filled == 0
    np.testing.assert_allclose(filled.toarray().ravel(), values.ravel())


def test_guard_counts_only_neighbors_that_have_a_spectrum():
    # A corner pixel has 3 neighbors; 2 of 3 detected (67%) passes 0.6.
    x_index, y_index = _grid_indices(2, 2)
    values = np.array([0.0, 4.0, 2.0, 0.0])

    filled, n_filled = fill_blanks(
        csr_matrix(values.reshape(-1, 1)), neighbor_matrix(x_index, y_index, 1), 0.6
    )

    # Pixel 0's neighbors: 1 (4), 2 (2), 3 (0) -> 2 of 3, mean 3.
    assert filled.toarray()[0, 0] == pytest.approx(3.0)
    assert filled.toarray()[3, 0] == pytest.approx(3.0)
    assert n_filled == 2


def test_columns_are_filled_independently_across_blocks(monkeypatch):
    import msianalyzer.core.utils.fill_blanks as module

    monkeypatch.setattr(module, "_COLUMN_BLOCK", 1)
    x_index, y_index = _grid_indices(3, 3)
    hole = np.full(9, 2.0)
    hole[4] = 0.0
    edge = np.zeros(9)
    edge[0] = 1.0
    layer = csr_matrix(np.column_stack([hole, edge]))

    filled, n_filled = fill_blanks(layer, neighbor_matrix(x_index, y_index, 1), 0.6)

    assert n_filled == 1
    np.testing.assert_allclose(filled.toarray()[:, 0], np.full(9, 2.0))
    np.testing.assert_allclose(filled.toarray()[:, 1], edge)


def test_layer_names_round_trip_and_required_neighbors():
    assert filled_layer_name("TIC", 2) == "TIC_filled_r2"
    assert parse_filled_layer_name("feature_12_filled_r1") == {"source": "feature_12", "radius": 1}
    assert parse_filled_layer_name("TIC") is None
    assert required_neighbors(1, 0.6) == 5
    assert required_neighbors(2, 0.6) == 15


def _sample(values_by_pixel, width):
    values = np.asarray(values_by_pixel, dtype=np.float32).reshape(-1, 1)
    x_index, y_index = _grid_indices(width, values.shape[0] // width)
    adata = ad.AnnData(
        X=csr_matrix(values),
        obs=pd.DataFrame({"tic": np.ones(values.shape[0])},
                         index=[str(i) for i in range(values.shape[0])]),
        var=pd.DataFrame({"mz": [100.0]}, index=["mz_100.0000"]),
    )
    adata.obsm["spatial"] = np.column_stack([x_index, y_index]).astype(float)
    return adata


def test_add_filled_layer_writes_samples_and_merged_then_removes(tmp_path):
    with_hole = [3, 3, 3, 3, 0, 3, 3, 3, 3]
    full = [1, 1, 1, 1, 1, 1, 1, 1, 1]
    paths = {}
    for name, values in (("a", with_hole), ("b", full)):
        paths[name] = tmp_path / f"{name}.h5ad"
        _sample(values, 3).write_h5ad(paths[name])
    run_normalization({"b": paths["b"], "a": paths["a"]}, out_dir=tmp_path)
    merged_path = tmp_path / "merged.h5ad"

    result = add_filled_layer(paths, merged_path, "raw", 1, 0.6, n_workers=1)

    assert result.layer_name == "raw_filled_r1"
    assert (result.n_samples, result.n_filled, result.merged_written) == (2, 1, True)
    sample_a = ad.read_h5ad(paths["a"])
    np.testing.assert_allclose(sample_a.layers["raw_filled_r1"].toarray().ravel(), [3] * 9)
    np.testing.assert_allclose(sample_a.X.toarray().ravel(), with_hole)  # source untouched
    merged = ad.read_h5ad(merged_path)
    merged_a = merged[merged.obs["sample"] == "a"]
    np.testing.assert_allclose(merged_a.layers["raw_filled_r1"].toarray().ravel(), [3] * 9)

    assert remove_filled_layer(paths, merged_path, "raw_filled_r1") == 3
    assert "raw_filled_r1" not in ad.read_h5ad(paths["a"]).layers
    with pytest.raises(ValueError):
        remove_filled_layer(paths, merged_path, "TIC")
