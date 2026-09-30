"""`core/plotting/light_sample.py` (ADR 50): a render-only, lazily-filled
AnnData per sample that renders exactly like the full `ad.read_h5ad` one."""

from __future__ import annotations

import anndata as ad
import numpy as np
import pandas as pd
import pytest
from scipy.sparse import csr_matrix, issparse

from msianalyzer.core.plotting.heatmap import (
    GRID_UNS_KEY,
    feature_value_range,
    list_obs_columns,
    obs_categories,
    obs_value_range,
    render_heatmap_by_target,
)
from msianalyzer.core.plotting.light_sample import LightSample


def _write(path, *, grid_axes=False, dense=False):
    coords = [(10 + 2 * x, 50 + 2 * y) for y in range(3) for x in range(4)]
    n = len(coords)
    rng = np.random.default_rng(0)
    raw = rng.random((n, 3)).astype(np.float32) * 100
    obs = pd.DataFrame(
        {
            "x": [c[0] for c in coords],
            "y": [c[1] for c in coords],
            "tic": rng.random(n) * 1000,
            "count": np.arange(n, dtype=np.int64),
            "flag": np.arange(n) % 2 == 0,
            "leiden": pd.Categorical([str(i % 3) for i in range(n)]),
            "label": [f"a{i % 2}" for i in range(n)],
        },
        index=[f"px{i}" for i in range(n)],
    )
    var = pd.DataFrame({"mz": [100.0, 200.0, 300.0]}, index=["mz_100", "mz_200", "mz_300"])
    adata = ad.AnnData(X=raw if dense else csr_matrix(raw), obs=obs, var=var)
    adata.layers["raw"] = csr_matrix(raw)
    adata.layers["TIC"] = csr_matrix(raw / 2)
    adata.obsm["spatial"] = np.array(coords, dtype=float)
    if grid_axes:
        adata.uns[GRID_UNS_KEY] = {"x": np.arange(8, 20, 2.0), "y": np.arange(50, 56, 2.0)}
    adata.write_h5ad(path)
    return ad.read_h5ad(path)


@pytest.fixture
def sample(tmp_path):
    path = tmp_path / "s1.h5ad"
    full = _write(path)
    return full, LightSample(path)


TARGETS = [
    ["s1", "200.0", "TIC", "viridis", "auto", "auto"],
    ["s1", "100.0", "raw", "magma", "0", "50"],
    ["s1", "300.0", "X", "gray", "auto", "auto"],  # not a layer -> .X
    ["s1", "obs:tic", "viridis", "auto", "auto"],
    ["s1", "obs:count", "viridis", "0", "5"],
    ["s1", "obs:leiden", "0,1,2"],
    ["s1", "obs:label", "a0,a1"],
    ["s1", "obs:flag", "False,True"],
]


@pytest.mark.parametrize("parts", TARGETS, ids=lambda p: "|".join(p[1:3]))
def test_renders_identically_to_the_full_anndata(sample, parts):
    full, light = sample
    light.prepare(parts)
    expected = render_heatmap_by_target(full, parts[1], parts)
    np.testing.assert_array_equal(render_heatmap_by_target(light.adata, parts[1], parts), expected)


def test_only_the_requested_layer_is_loaded_and_as_csc(sample):
    _full, light = sample
    assert light.adata.X is None and len(light.adata.layers) == 0
    light.prepare(["s1", "200.0", "TIC", "viridis", "auto", "auto"])
    assert list(light.adata.layers.keys()) == ["TIC"]
    assert light.adata.X is None
    assert light.adata.layers["TIC"].format == "csc"


def test_x_fallback_is_csc_even_for_a_dense_file(tmp_path):
    path = tmp_path / "dense.h5ad"
    full = _write(path, dense=True)
    light = LightSample(path)
    light.ensure_layer("X")
    assert issparse(light.adata.X) and light.adata.X.format == "csc"
    np.testing.assert_allclose(light.adata.X.toarray(), full.X)


def test_obs_columns_listed_from_metadata_without_loading_them(sample):
    full, light = sample
    assert light.obs_columns() == list_obs_columns(full)
    assert len(light.adata.obs.columns) == 0  # nothing read yet


def test_value_range_and_categories_match(sample):
    full, light = sample
    light.ensure_layer("TIC")
    assert feature_value_range(light.adata, 200.0, "TIC") == feature_value_range(full, 200.0, "TIC")
    light.ensure_obs("tic")
    assert obs_value_range(light.adata, "tic") == obs_value_range(full, "tic")
    light.ensure_obs("leiden")
    assert obs_categories(light.adata, "leiden") == obs_categories(full, "leiden")


def test_grid_axes_are_kept(tmp_path):
    path = tmp_path / "imported.h5ad"
    full = _write(path, grid_axes=True)
    light = LightSample(path)
    parts = ["s1", "100.0", "raw", "viridis", "auto", "auto"]
    light.prepare(parts)
    rgba = render_heatmap_by_target(light.adata, parts[1], parts)
    assert rgba.shape[:2] == (3, 6)  # the stored (wider) grid, not the 4 present columns
    np.testing.assert_array_equal(rgba, render_heatmap_by_target(full, parts[1], parts))


def test_nbytes_grows_as_data_is_loaded(sample):
    _full, light = sample
    before = light.nbytes()
    light.ensure_layer("TIC")
    light.ensure_obs("tic")
    assert light.nbytes() > before


def test_local_moran_render_works_on_the_light_sample(sample):
    full, light = sample
    parts = ["s1", "200.0", "localMoran", "coolwarm", "auto", "auto"]
    light.prepare(parts)
    np.testing.assert_array_equal(
        render_heatmap_by_target(light.adata, parts[1], parts),
        render_heatmap_by_target(full, parts[1], parts),
    )
