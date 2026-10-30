from __future__ import annotations

import os
from unittest.mock import patch

import anndata as ad
import matplotlib
import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix

from msianalyzer.core.plotting.heatmap import pixel_grid_indices
from msianalyzer.core.spatial.local_moran import (
    SUMMARY_THRESHOLDS,
    SUMMARY_UNS_KEY,
    _chunk_indexed,
    _resolve_n_workers,
    compute_local_moran_layer,
    load_local_moran_summary,
    local_moran_summary,
    local_moran_values,
    pct_above_threshold,
    render_local_moran_heatmap,
)


def _make_grid_sample(n: int = 10, seed: int = 0) -> ad.AnnData:
    """Same shape as test_moran.py's fixture: a square pixel grid with one
    spatially-clustered feature (a blob centered on the grid) and one
    spatially-random feature, so local Moran's I should clearly separate
    the two at the per-pixel level, not just the per-sample global score.
    """
    rng = np.random.default_rng(seed)
    xs, ys = np.meshgrid(np.arange(n), np.arange(n))
    xs, ys = xs.ravel(), ys.ravel()
    n_pix = xs.size

    center = n / 2
    clustered = np.exp(-((xs - center) ** 2 + (ys - center) ** 2) / 8.0) * 1000.0
    random_feat = rng.random(n_pix) * 1000.0

    X = np.stack([clustered, random_feat], axis=1).astype(np.float32)
    var = pd.DataFrame({"mz": [100.0, 200.0]}, index=["mz_100.0000", "mz_200.0000"])

    adata = ad.AnnData(X=csr_matrix(X), var=var)
    adata.obsm["spatial"] = np.stack([xs, ys], axis=1).astype(float)
    adata.layers["TIC"] = adata.X.copy()
    return adata, xs, ys, center


def test_local_moran_values_high_at_cluster_center_low_at_random_feature():
    adata, xs, ys, center = _make_grid_sample()

    clustered_i = local_moran_values(adata, 100.0, layer="TIC")
    random_i = local_moran_values(adata, 200.0, layer="TIC")

    assert clustered_i.shape == (xs.size,)

    # A pixel right at the blob's center, surrounded by similarly-high
    # neighbors, should have a strongly positive local I (local
    # clustering) — clearly higher than the same pixel's score for the
    # spatially-random feature.
    center_idx = np.argmin((xs - center) ** 2 + (ys - center) ** 2)
    assert clustered_i[center_idx] > 0.5
    assert clustered_i[center_idx] > random_i[center_idx]

    # Overall, the clustered feature's local scores should be higher on
    # average than the random feature's — the per-pixel analogue of
    # test_moran.py's per-sample "clustered > random" assertion.
    assert np.nanmean(clustered_i) > np.nanmean(random_i)


def test_local_moran_values_constant_feature_is_all_nan():
    # m2 (population variance of the mean-centered values) is 0 for a
    # constant feature — no defined local Moran's I, not a divide-by-zero
    # crash or a misleading 0.
    adata, xs, ys, _ = _make_grid_sample()
    adata.X = csr_matrix(np.ones((xs.size, 2), dtype=np.float32))
    adata.layers["TIC"] = adata.X.copy()

    values = local_moran_values(adata, 100.0, layer="TIC")

    assert np.all(np.isnan(values))


def test_local_moran_values_falls_back_to_raw_when_layer_missing():
    adata, xs, _, _ = _make_grid_sample()
    del adata.layers["TIC"]

    values = local_moran_values(adata, 100.0, layer="TIC")

    assert values.shape == (xs.size,)
    assert not np.all(np.isnan(values))


def test_render_local_moran_heatmap_returns_rgba_uint8_grid():
    adata, xs, ys, _ = _make_grid_sample()

    rgba = render_local_moran_heatmap(adata, 100.0, layer="TIC")

    assert rgba.dtype == np.uint8
    assert rgba.shape == (np.unique(ys).size, np.unique(xs).size, 4)


def test_render_local_moran_heatmap_autoscale_is_symmetric_around_zero():
    # Real local Moran's I data is usually asymmetric (a few strongly
    # clustered hot spots, everything else near zero) — the color scale
    # must still put 0 at the colormap's visual center, or every ordinary
    # (near-zero, "no local structure") pixel renders as if it sat near
    # one extreme. Regression for a real bug: `_colorize_grid`'s own
    # default (raw data min/max) put 0 far off-center whenever the
    # positive/negative tails were unequal, making an entire real image
    # visually read as "everything is minimum".
    adata, xs, ys, _ = _make_grid_sample(n=3)
    n = xs.size
    asymmetric_values = np.zeros(n)
    asymmetric_values[0] = 5.0  # a strong positive outlier
    asymmetric_values[1] = -0.1  # a much smaller negative tail
    # every other pixel stays exactly 0.0 ("no local structure")

    with patch(
        "msianalyzer.core.spatial.local_moran.local_moran_values",
        return_value=asymmetric_values,
    ):
        rgba = render_local_moran_heatmap(adata, 100.0)

    x_index, y_index, _, _ = pixel_grid_indices(adata)
    zero_pixel_rgba = rgba[y_index[2], x_index[2]]
    expected_center = (np.array(matplotlib.colormaps["coolwarm"](0.5)) * 255).astype(np.uint8)
    np.testing.assert_array_equal(zero_pixel_rgba, expected_center)


def test_local_moran_values_reuses_an_already_built_neighbor_graph():
    # Whichever computed it first — a prior spatial_correlation pipeline
    # run (persisted to this same .h5ad) or this function itself, called
    # earlier for a different feature against this same in-memory adata —
    # a second call must not rebuild it (same n_neighs everywhere in this
    # module, so reuse is always correct, and rebuilding it once per
    # feature would be wasteful over a whole sample's feature list).
    adata, _, _, _ = _make_grid_sample()

    with patch("squidpy.gr.spatial_neighbors_grid") as spy:
        spy.side_effect = lambda a, **kw: a.obsp.__setitem__(
            "spatial_connectivities", csr_matrix(np.eye(a.n_obs))
        )
        local_moran_values(adata, 100.0, layer="TIC")
        local_moran_values(adata, 200.0, layer="TIC")

    assert spy.call_count == 1


def test_pct_above_threshold_basic():
    values = np.array([0.0, 0.5, 1.0, 1.5, 2.0, np.nan])
    assert pct_above_threshold(values, 1.0) == 40.0  # 1.5, 2.0 out of 5 finite


def test_pct_above_threshold_empty_or_all_nan_is_zero():
    assert pct_above_threshold(np.array([]), 1.0) == 0.0
    assert pct_above_threshold(np.array([np.nan, np.nan]), 1.0) == 0.0


def test_compute_local_moran_layer_writes_per_pixel_per_feature_matrix(tmp_path):
    adata, xs, ys, _ = _make_grid_sample()
    path = tmp_path / "sample.h5ad"
    adata.write_h5ad(path)

    # n_workers=1: this test is about output correctness, not parallelism
    # (covered separately below) — no need to pay for real subprocesses
    # for a 2-feature fixture.
    compute_local_moran_layer(path, layer="TIC", n_workers=1)

    out = ad.read_h5ad(path)
    assert "localMoranI" in out.layers
    layer = out.layers["localMoranI"]
    assert layer.shape == (xs.size, 2)  # n_pixels x n_features, same as X

    # The per-pixel test above already confirms the clustered feature's
    # local scores run higher than the random one's — recomputing a
    # percentage from the stored layer (as the GUI does) should hold the
    # same relationship.
    clustered_pct = pct_above_threshold(layer[:, 0], 0.5)
    random_pct = pct_above_threshold(layer[:, 1], 0.5)
    assert clustered_pct > random_pct
    assert 0.0 <= clustered_pct <= 100.0
    assert 0.0 <= random_pct <= 100.0

    # Column order must match var_names order — the GUI indexes into this
    # layer by var position, not by re-deriving it some other way.
    np.testing.assert_array_equal(layer[:, 0], local_moran_values(out, 100.0, layer="TIC"))
    np.testing.assert_array_equal(layer[:, 1], local_moran_values(out, 200.0, layer="TIC"))

    # The small summary the GUI reads instead of the layer is stored too.
    np.testing.assert_array_equal(
        out.uns[SUMMARY_UNS_KEY]["counts"], local_moran_summary(layer)["counts"]
    )


def test_compute_local_moran_layer_builds_neighbor_graph_once_for_every_feature(tmp_path):
    # n_workers=1 forces the serial path — the parallel path's own
    # per-*worker-process* graph reuse is a different thing, tested
    # separately below (mocking squidpy in this test's process can't see
    # what an actual child process calls).
    adata, _, _, _ = _make_grid_sample()
    path = tmp_path / "sample.h5ad"
    adata.write_h5ad(path)

    with patch("squidpy.gr.spatial_neighbors_grid") as spy:
        spy.side_effect = lambda a, **kw: a.obsp.__setitem__(
            "spatial_connectivities", csr_matrix(np.eye(a.n_obs))
        )
        compute_local_moran_layer(path, layer="TIC", n_workers=1)

    assert spy.call_count == 1


def test_compute_local_moran_layer_reuses_neighbor_graph_from_a_prior_global_run(tmp_path):
    # If compute_spatial_autocorr already ran and wrote obsp back to this
    # .h5ad, this function must not need squidpy again at all.
    # n_workers=1 forces the serial path — see the comment above.
    import squidpy as sq

    adata, _, _, _ = _make_grid_sample()
    sq.gr.spatial_neighbors_grid(adata, n_neighs=4)
    path = tmp_path / "sample.h5ad"
    adata.write_h5ad(path)

    with patch("squidpy.gr.spatial_neighbors_grid") as spy:
        compute_local_moran_layer(path, layer="TIC", n_workers=1)

    spy.assert_not_called()


def test_resolve_n_workers_none_or_zero_defaults_to_cpu_count():
    assert _resolve_n_workers(None, n_items=100) == max(1, os.cpu_count() or 1)
    assert _resolve_n_workers(0, n_items=100) == max(1, os.cpu_count() or 1)


def test_resolve_n_workers_clamped_to_available_work():
    # More workers requested than there's work for — never spin up idle
    # workers with nothing to do.
    assert _resolve_n_workers(8, n_items=2) == 2
    assert _resolve_n_workers(None, n_items=1) == 1


def test_resolve_n_workers_one_forces_serial_regardless_of_cpu_count():
    assert _resolve_n_workers(1, n_items=100) == 1


def test_chunk_indexed_splits_into_equal_nonempty_chunks():
    indexed = list(enumerate([10.0, 20.0, 30.0, 40.0, 50.0]))

    chunks = _chunk_indexed(indexed, 2)

    assert len(chunks) == 2
    assert all(len(c) > 0 for c in chunks)
    assert sorted(idx for chunk in chunks for idx, _ in chunk) == [0, 1, 2, 3, 4]


def test_chunk_indexed_never_returns_empty_chunks_even_if_over_requested():
    indexed = list(enumerate([10.0, 20.0]))

    chunks = _chunk_indexed(indexed, 10)

    assert all(len(c) > 0 for c in chunks)
    assert sum(len(c) for c in chunks) == 2


def _make_multi_feature_grid_sample(n_features: int = 5, grid_n: int = 6, seed: int = 0) -> ad.AnnData:
    """A small grid with several features, spread across more than one
    worker's chunk with `n_workers=2` — enough to actually exercise
    `compute_local_moran_layer`'s parallel path, unlike `_make_grid_sample`'s
    fixed 2 features (which `_resolve_n_workers` would clamp to at most 2
    workers, not exercising multi-item chunks)."""
    rng = np.random.default_rng(seed)
    xs, ys = np.meshgrid(np.arange(grid_n), np.arange(grid_n))
    xs, ys = xs.ravel(), ys.ravel()
    n_pix = xs.size

    X = (rng.random((n_pix, n_features)) * 1000.0).astype(np.float32)
    mzs = [100.0 * (i + 1) for i in range(n_features)]
    var = pd.DataFrame(
        {"mz": mzs}, index=[f"mz_test_{i}" for i in range(n_features)]
    )
    adata = ad.AnnData(X=csr_matrix(X), var=var)
    adata.obsm["spatial"] = np.stack([xs, ys], axis=1).astype(float)
    adata.layers["TIC"] = adata.X.copy()
    return adata


def test_compute_local_moran_layer_parallel_matches_serial_results(tmp_path):
    # The real correctness test for the parallel path: same math, just
    # distributed across worker processes and reassembled — results must
    # be identical to the trusted serial baseline, not just "some numbers".
    adata = _make_multi_feature_grid_sample()
    serial_path = tmp_path / "serial.h5ad"
    parallel_path = tmp_path / "parallel.h5ad"
    adata.write_h5ad(serial_path)
    adata.write_h5ad(parallel_path)

    compute_local_moran_layer(serial_path, layer="TIC", n_workers=1)
    compute_local_moran_layer(parallel_path, layer="TIC", n_workers=2)

    serial_layer = ad.read_h5ad(serial_path).layers["localMoranI"]
    parallel_layer = ad.read_h5ad(parallel_path).layers["localMoranI"]

    np.testing.assert_array_equal(serial_layer, parallel_layer)


def test_local_moran_summary_matches_pct_above_threshold_at_every_grid_value():
    rng = np.random.default_rng(1)
    layer = rng.normal(0.5, 1.5, size=(200, 4))
    layer[:20, 1] = np.nan  # NaN and inf are not counted, as in pct_above_threshold
    layer[5, 2] = np.inf
    layer[:, 3] = np.nan
    layer[0, 0] = 1.0  # exactly on a threshold: strictly above only

    from msianalyzer.core.spatial.local_moran import summary_percentages

    pct = summary_percentages(local_moran_summary(layer))
    for t_idx, t in enumerate(SUMMARY_THRESHOLDS):
        for j in range(layer.shape[1]):
            assert pct[t_idx, j] == pct_above_threshold(layer[:, j], t)


def test_load_local_moran_summary_backfills_an_older_file_once(tmp_path):
    adata, _, _, _ = _make_grid_sample()
    path = tmp_path / "sample.h5ad"
    adata.write_h5ad(path)
    assert load_local_moran_summary(path) is None  # no layer: nothing to show

    layer = np.arange(adata.n_obs * 2, dtype=float).reshape(-1, 2) / 50.0
    adata.layers["localMoranI"] = layer
    adata.write_h5ad(path)  # as written before the summary existed

    first = load_local_moran_summary(path)
    np.testing.assert_array_equal(first["counts"], local_moran_summary(layer)["counts"])
    assert SUMMARY_UNS_KEY in ad.read_h5ad(path).uns

    with patch("msianalyzer.core.spatial.local_moran.local_moran_summary") as recompute:
        second = load_local_moran_summary(path)
    recompute.assert_not_called()  # read back, the layer is not touched again
    np.testing.assert_array_equal(second["counts"], first["counts"])
