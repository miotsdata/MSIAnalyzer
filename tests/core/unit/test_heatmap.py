import anndata as ad
import matplotlib
import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix

from msianalyzer.core.plotting.heatmap import feature_value_range, render_feature_heatmap


def _make_grid_adata(values, *, xs=(0.0, 1.0), ys=(0.0, 1.0), layers=None):
    """A 2x2 spatial grid (x fastest-varying), one feature, `values` in
    (x=0,y=0), (x=1,y=0), (x=0,y=1), (x=1,y=1) order."""
    coords = [(x, y) for y in ys for x in xs]
    obs = pd.DataFrame(index=[f"px{i}" for i in range(len(coords))])
    var = pd.DataFrame({"mz": [100.0]}, index=["mz_100.0000"])
    X = csr_matrix(np.array(values, dtype=np.float32).reshape(-1, 1))
    adata = ad.AnnData(X=X, obs=obs, var=var)
    adata.obsm["spatial"] = np.array(coords, dtype=float)
    if layers:
        for name, layer_values in layers.items():
            adata.layers[name] = csr_matrix(
                np.array(layer_values, dtype=np.float32).reshape(-1, 1)
            )
    return adata


def test_render_feature_heatmap_reconstructs_grid_and_applies_colormap():
    adata = _make_grid_adata([0.0, 10.0, 5.0, 15.0])

    rgba = render_feature_heatmap(
        adata, mz=100.0, layer="X_unused_falls_back_to_X", colormap="gray",
        vmin=0.0, vmax=15.0,
    )

    assert rgba.shape == (2, 2, 4)
    assert rgba.dtype == np.uint8

    grid = np.array([[0.0, 10.0], [5.0, 15.0]])
    norm = matplotlib.colors.Normalize(vmin=0.0, vmax=15.0, clip=True)
    expected = (matplotlib.colormaps["gray"](norm(grid)) * 255).astype(np.uint8)
    np.testing.assert_array_equal(rgba, expected)


def test_render_feature_heatmap_paints_missing_pixels_black():
    # Only 3 of the 4 grid positions present -> one NaN cell.
    coords = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
    obs = pd.DataFrame(index=["a", "b", "c"])
    var = pd.DataFrame({"mz": [100.0]}, index=["mz_100.0000"])
    X = csr_matrix(np.array([1.0, 2.0, 3.0], dtype=np.float32).reshape(-1, 1))
    adata = ad.AnnData(X=X, obs=obs, var=var)
    adata.obsm["spatial"] = np.array(coords, dtype=float)

    rgba = render_feature_heatmap(adata, mz=100.0, layer="raw", colormap="viridis")

    assert tuple(rgba[1, 1]) == (0, 0, 0, 255)


def test_render_feature_heatmap_clips_out_of_range_values():
    adata = _make_grid_adata([-100.0, 0.0, 50.0, 1000.0])

    rgba = render_feature_heatmap(
        adata, mz=100.0, layer="raw", colormap="gray", vmin=0.0, vmax=50.0
    )

    black = tuple(matplotlib.colormaps["gray"](0.0))
    white = tuple(matplotlib.colormaps["gray"](1.0))
    assert tuple(rgba[0, 0]) == tuple(int(c * 255) for c in black)
    assert tuple(rgba[1, 1]) == tuple(int(c * 255) for c in white)


def test_render_feature_heatmap_selects_requested_layer():
    adata = _make_grid_adata(
        [0.0, 0.0, 0.0, 0.0],
        layers={
            "raw": [1.0, 2.0, 3.0, 4.0],
            "TIC": [10.0, 20.0, 30.0, 40.0],
        },
    )

    raw_rgba = render_feature_heatmap(
        adata, mz=100.0, layer="raw", colormap="gray", vmin=0.0, vmax=4.0
    )
    tic_rgba = render_feature_heatmap(
        adata, mz=100.0, layer="TIC", colormap="gray", vmin=0.0, vmax=40.0
    )

    # Same normalized position in each layer's own vmin/vmax range -> same color.
    np.testing.assert_array_equal(raw_rgba, tic_rgba)


def test_feature_value_range_returns_min_and_max():
    adata = _make_grid_adata([0.0, 10.0, 5.0, 15.0])

    assert feature_value_range(adata, mz=100.0, layer="raw") == (0.0, 15.0)


def test_feature_value_range_selects_requested_layer():
    adata = _make_grid_adata(
        [0.0, 0.0, 0.0, 0.0],
        layers={"raw": [1.0, 2.0, 3.0, 4.0], "TIC": [10.0, 20.0, 30.0, 40.0]},
    )

    assert feature_value_range(adata, mz=100.0, layer="raw") == (1.0, 4.0)
    assert feature_value_range(adata, mz=100.0, layer="TIC") == (10.0, 40.0)


def test_feature_value_range_defaults_when_all_values_missing():
    obs = pd.DataFrame(index=["a"])
    var = pd.DataFrame({"mz": [100.0]}, index=["mz_100.0000"])
    X = csr_matrix(np.array([np.nan], dtype=np.float32).reshape(-1, 1))
    adata = ad.AnnData(X=X, obs=obs, var=var)
    adata.obsm["spatial"] = np.array([(0.0, 0.0)], dtype=float)

    assert feature_value_range(adata, mz=100.0, layer="raw") == (0.0, 1.0)


def test_render_feature_heatmap_picks_nearest_mz_column():
    obs = pd.DataFrame(index=["a", "b", "c", "d"])
    var = pd.DataFrame({"mz": [100.0, 200.0]}, index=["mz_100.0000", "mz_200.0000"])
    X = csr_matrix(
        np.array([[1.0, 9.0], [2.0, 8.0], [3.0, 7.0], [4.0, 6.0]], dtype=np.float32)
    )
    adata = ad.AnnData(X=X, obs=obs, var=var)
    adata.obsm["spatial"] = np.array([(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)])

    rgba = render_feature_heatmap(
        adata, mz=199.5, layer="raw", colormap="gray", vmin=6.0, vmax=9.0
    )
    # Nearest to 199.5 is the mz=200.0 column, values [9, 8, 7, 6].
    white = tuple(int(c * 255) for c in matplotlib.colormaps["gray"](1.0))
    black = tuple(int(c * 255) for c in matplotlib.colormaps["gray"](0.0))
    assert tuple(rgba[0, 0]) == white
    assert tuple(rgba[1, 1]) == black
