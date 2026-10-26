import anndata as ad
import matplotlib
import matplotlib.figure
import matplotlib.patches
import matplotlib.ticker
import numpy as np
import pandas as pd
import pytest
from scipy.sparse import csr_matrix

from msianalyzer.core.plotting.heatmap import (
    GRID_UNS_KEY,
    category_color,
    category_colors,
    feature_value_range,
    list_obs_columns,
    obs_categories,
    obs_value_range,
    parse_category_colors,
    pixel_grid_indices,
    render_colorbar,
    render_feature_heatmap,
    render_heatmap_by_target,
    render_heatmap_figure,
    render_obs_categories_heatmap,
    render_obs_heatmap,
    scale_bar_spec,
)


def _make_grid_adata(
    values, *, xs=(0.0, 1.0), ys=(0.0, 1.0), layers=None, obs_columns=None
):
    """A 2x2 spatial grid (x fastest-varying), one feature, `values` in
    (x=0,y=0), (x=1,y=0), (x=0,y=1), (x=1,y=1) order."""
    coords = [(x, y) for y in ys for x in xs]
    obs = pd.DataFrame(index=[f"px{i}" for i in range(len(coords))])
    if obs_columns:
        for name, column_values in obs_columns.items():
            obs[name] = column_values
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


def test_pixel_grid_indices_matches_reconstruct_grid_scatter():
    # 3 of the 4 grid positions present, values in obs row order — the
    # same shape render_feature_heatmap's internal _reconstruct_grid
    # scatters via grid[y_index, x_index] = values; pixel_grid_indices is
    # just that computation exposed standalone, so scattering by hand with
    # its output must reproduce the exact same grid.
    coords = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
    obs = pd.DataFrame(index=["a", "b", "c"])
    var = pd.DataFrame({"mz": [100.0]}, index=["mz_100.0000"])
    X = csr_matrix(np.array([1.0, 2.0, 3.0], dtype=np.float32).reshape(-1, 1))
    adata = ad.AnnData(X=X, obs=obs, var=var)
    adata.obsm["spatial"] = np.array(coords, dtype=float)

    x_index, y_index, unique_x, unique_y = pixel_grid_indices(adata)

    assert list(unique_x) == [0.0, 1.0]
    assert list(unique_y) == [0.0, 1.0]
    assert x_index.shape == (3,)
    assert y_index.shape == (3,)

    values = np.array([1.0, 2.0, 3.0])
    grid = np.full((unique_y.size, unique_x.size), np.nan)
    grid[y_index, x_index] = values

    rgba = render_feature_heatmap(adata, mz=100.0, layer="raw", colormap="gray")
    # The one uncovered cell (x=1,y=1) renders pure black regardless of
    # colormap/vmin/vmax (_colorize_grid's NaN convention) — same cell
    # pixel_grid_indices leaves untouched above.
    assert np.isnan(grid[1, 1])
    assert tuple(rgba[1, 1]) == (0, 0, 0, 255)


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


def test_render_colorbar_shape_and_endpoints_match_the_colormap():
    rgba = render_colorbar("gray", width=10, height=4)

    assert rgba.shape == (4, 10, 4)
    assert rgba.dtype == np.uint8

    black = tuple(int(c * 255) for c in matplotlib.colormaps["gray"](0.0))
    white = tuple(int(c * 255) for c in matplotlib.colormaps["gray"](1.0))
    # Left edge is the colormap's low end, right edge its high end, every
    # row identical (a flat gradient, not spatial data).
    assert tuple(rgba[0, 0]) == black
    assert tuple(rgba[0, -1]) == white
    np.testing.assert_array_equal(rgba[0], rgba[-1])


def test_render_colorbar_defaults_to_a_256x16_strip():
    rgba = render_colorbar("viridis")

    assert rgba.shape == (16, 256, 4)


def test_list_obs_columns_classifies_numeric_and_discrete():
    adata = _make_grid_adata(
        [0.0, 0.0, 0.0, 0.0],
        obs_columns={
            "tic": [1.0, 2.0, 3.0, 4.0],
            "region": ["a", "a", "b", "b"],
            "flagged": [True, False, True, False],
        },
    )

    columns = {c["name"]: c["numeric"] for c in list_obs_columns(adata)}

    assert columns["tic"] is True
    assert columns["region"] is False
    # bool dtype is discrete (a legend of True/False), not a color scale.
    assert columns["flagged"] is False


def test_list_obs_columns_excludes_spatial_coordinates():
    adata = _make_grid_adata([0.0, 0.0, 0.0, 0.0])

    names = [c["name"] for c in list_obs_columns(adata)]

    assert "x" not in names
    assert "y" not in names


def test_list_obs_columns_excludes_scan_id_and_polarity():
    # scan_id is effectively unique per pixel after scan averaging (a
    # joined string of scan ids) — as a "discrete" column its category
    # count is ~the pixel count, and the categorical render path is
    # O(categories) per pixel, so exposing it froze the UI. polarity is a
    # single constant value per sample, nothing to see spatially.
    adata = _make_grid_adata(
        [0.0, 0.0, 0.0, 0.0],
        obs_columns={
            "scan_id": ["1", "2", "3", "4"],
            "polarity": ["positive"] * 4,
            "tic": [1.0, 2.0, 3.0, 4.0],
        },
    )

    names = [c["name"] for c in list_obs_columns(adata)]

    assert "scan_id" not in names
    assert "polarity" not in names
    assert "tic" in names


def test_obs_value_range_returns_min_and_max():
    adata = _make_grid_adata(
        [0.0, 0.0, 0.0, 0.0], obs_columns={"tic": [5.0, 20.0, 1.0, 100.0]}
    )

    assert obs_value_range(adata, "tic") == (1.0, 100.0)


def test_obs_value_range_defaults_when_all_values_missing():
    adata = _make_grid_adata(
        [0.0], xs=(0.0,), ys=(0.0,), obs_columns={"tic": [np.nan]}
    )

    assert obs_value_range(adata, "tic") == (0.0, 1.0)


def test_render_obs_heatmap_reconstructs_grid_and_applies_colormap():
    adata = _make_grid_adata(
        [0.0, 0.0, 0.0, 0.0], obs_columns={"tic": [0.0, 10.0, 5.0, 15.0]}
    )

    rgba = render_obs_heatmap(adata, "tic", colormap="gray", vmin=0.0, vmax=15.0)

    grid = np.array([[0.0, 10.0], [5.0, 15.0]])
    norm = matplotlib.colors.Normalize(vmin=0.0, vmax=15.0, clip=True)
    expected = (matplotlib.colormaps["gray"](norm(grid)) * 255).astype(np.uint8)
    np.testing.assert_array_equal(rgba, expected)


def test_obs_categories_returns_sorted_distinct_values():
    adata = _make_grid_adata(
        [0.0, 0.0, 0.0, 0.0],
        obs_columns={"polarity": ["negative", "positive", "negative", "positive"]},
    )

    assert obs_categories(adata, "polarity") == ["negative", "positive"]


def test_obs_categories_excludes_null_values():
    adata = _make_grid_adata(
        [0.0, 0.0, 0.0, 0.0],
        obs_columns={"polarity": ["positive", None, "positive", None]},
    )

    assert obs_categories(adata, "polarity") == ["positive"]


def test_render_obs_categories_heatmap_colors_by_category_position():
    adata = _make_grid_adata(
        [0.0, 0.0, 0.0, 0.0],
        obs_columns={"polarity": ["negative", "positive", "negative", "positive"]},
    )

    rgba = render_obs_categories_heatmap(
        adata, "polarity", categories=["negative", "positive"]
    )

    assert tuple(rgba[0, 0]) == _to_rgba(category_color(0))
    assert tuple(rgba[0, 1]) == _to_rgba(category_color(1))
    assert tuple(rgba[1, 0]) == _to_rgba(category_color(0))
    assert tuple(rgba[1, 1]) == _to_rgba(category_color(1))


def test_render_obs_categories_heatmap_paints_unlisted_category_black():
    adata = _make_grid_adata(
        [0.0, 0.0, 0.0, 0.0],
        obs_columns={"polarity": ["negative", "positive", "negative", "positive"]},
    )

    # "positive" isn't in the passed category list (e.g. hidden from
    # another sample's union) -> pixels with that value render black
    # rather than reusing a color meant for a different category.
    rgba = render_obs_categories_heatmap(adata, "polarity", categories=["negative"])

    assert tuple(rgba[0, 1]) == (0, 0, 0, 255)
    assert tuple(rgba[1, 1]) == (0, 0, 0, 255)


def test_render_heatmap_by_target_feature_matches_render_feature_heatmap():
    adata = _make_grid_adata([0.0, 10.0, 5.0, 15.0])
    parts = ["s1", "100.0", "raw", "gray", "0.0", "15.0"]

    dispatched = render_heatmap_by_target(adata, parts[1], parts)
    direct = render_feature_heatmap(
        adata, mz=100.0, layer="raw", colormap="gray", vmin=0.0, vmax=15.0
    )

    np.testing.assert_array_equal(dispatched, direct)


def test_render_heatmap_by_target_feature_autoscale_tokens():
    adata = _make_grid_adata([0.0, 10.0, 5.0, 15.0])
    parts = ["s1", "100.0", "raw", "gray", "auto", "auto"]

    dispatched = render_heatmap_by_target(adata, parts[1], parts)
    direct = render_feature_heatmap(
        adata, mz=100.0, layer="raw", colormap="gray", vmin=None, vmax=None
    )

    np.testing.assert_array_equal(dispatched, direct)


def test_render_heatmap_by_target_local_moran_dispatches_to_render_local_moran_heatmap():
    # Local import inside render_heatmap_by_target (core/spatial/local_moran
    # pulls in squidpy) — imported here, not at module scope, to match.
    from msianalyzer.core.spatial.local_moran import render_local_moran_heatmap

    # squidpy's neighbor graph needs more pixels than the 2x2 fixture
    # elsewhere in this file has (n_neighs=4 requires n_samples > 4) — a
    # small 3x3 grid instead, just for this dispatch smoke test.
    adata = _make_grid_adata(
        list(range(9)), xs=(0.0, 1.0, 2.0), ys=(0.0, 1.0, 2.0)
    )
    parts = ["s1", "100.0", "localMoran", "coolwarm", "auto", "auto"]

    dispatched = render_heatmap_by_target(adata, parts[1], parts)
    direct = render_local_moran_heatmap(
        adata, mz=100.0, colormap="coolwarm", vmin=None, vmax=None
    )

    np.testing.assert_array_equal(dispatched, direct)


def test_render_heatmap_by_target_numeric_obs():
    adata = _make_grid_adata(
        [0.0, 0.0, 0.0, 0.0], obs_columns={"tic": [0.0, 10.0, 5.0, 15.0]}
    )
    parts = ["s1", "obs:tic", "gray", "0.0", "15.0"]

    dispatched = render_heatmap_by_target(adata, parts[1], parts)
    direct = render_obs_heatmap(adata, "tic", colormap="gray", vmin=0.0, vmax=15.0)

    np.testing.assert_array_equal(dispatched, direct)


def test_render_heatmap_by_target_categorical_obs():
    adata = _make_grid_adata(
        [0.0, 0.0, 0.0, 0.0],
        obs_columns={"polarity": ["negative", "positive", "negative", "positive"]},
    )
    parts = ["s1", "obs:polarity", "negative,positive"]

    dispatched = render_heatmap_by_target(adata, parts[1], parts)
    direct = render_obs_categories_heatmap(adata, "polarity", ["negative", "positive"])

    np.testing.assert_array_equal(dispatched, direct)


def test_render_heatmap_by_target_categorical_obs_empty_category_list():
    adata = _make_grid_adata(
        [0.0, 0.0, 0.0, 0.0],
        obs_columns={"polarity": ["negative", "positive", "negative", "positive"]},
    )
    parts = ["s1", "obs:polarity", ""]

    dispatched = render_heatmap_by_target(adata, parts[1], parts)
    direct = render_obs_categories_heatmap(adata, "polarity", [])

    np.testing.assert_array_equal(dispatched, direct)


def test_category_colors_applies_overrides_over_positional_defaults():
    colors = category_colors(["a", "b", "c"], {"b": "#123456"})

    assert colors == [category_color(0), "#123456", category_color(2)]


def test_parse_category_colors_reads_hex_and_falls_back_to_default():
    categories, colors = parse_category_colors("a=FF0000,x=y,b")

    assert categories == ["a", "x=y", "b"]
    assert colors == ["#ff0000", category_color(1), category_color(2)]


def test_render_heatmap_by_target_categorical_obs_uses_colors_in_id():
    adata = _make_grid_adata(
        [0.0, 0.0, 0.0, 0.0],
        obs_columns={"polarity": ["negative", "positive", "negative", "positive"]},
    )
    parts = ["s1", "obs:polarity", "negative=ff0000,positive=00ff00"]

    rgba = render_heatmap_by_target(adata, parts[1], parts)

    assert tuple(rgba[0, 0]) == (255, 0, 0, 255)
    assert tuple(rgba[0, 1]) == (0, 255, 0, 255)


def test_render_heatmap_figure_legend_uses_custom_colors():
    adata = _make_grid_adata(
        [0.0, 0.0, 0.0, 0.0],
        obs_columns={"polarity": ["negative", "positive", "negative", "positive"]},
    )

    fig = render_heatmap_figure(
        adata, mode="obs_categorical", obs_column="polarity",
        categories=["negative", "positive"], colors=["#ff0000", "#00ff00"],
    )

    handles = fig.axes[0].get_legend().legend_handles
    assert [matplotlib.colors.to_hex(h.get_facecolor()) for h in handles] == [
        "#ff0000", "#00ff00",
    ]


def test_render_heatmap_figure_hidden_categories_are_black_and_not_in_legend():
    adata = _make_grid_adata(
        [0.0, 0.0, 0.0, 0.0],
        obs_columns={"polarity": ["negative", "positive", "negative", "positive"]},
    )

    fig = render_heatmap_figure(
        adata, mode="obs_categorical", obs_column="polarity",
        categories=["negative", "positive"], colors=["#ff0000", "#00ff00"],
        hidden_categories={"negative"},
    )

    ax = fig.axes[0]
    assert [t.get_text() for t in ax.get_legend().get_texts()] == ["positive"]
    raster = ax.get_images()[0].get_array()
    assert tuple(raster[0, 0]) == (0, 0, 0, 255)
    assert tuple(raster[0, 1]) == (0, 255, 0, 255)


def test_category_color_is_stable_and_distinct_for_different_indices():
    assert category_color(0) == category_color(0)
    assert category_color(0) != category_color(1)


def _to_rgba(hex_color):
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    return (r, g, b, 255)


def test_render_heatmap_figure_feature_mode_returns_figure_with_colorbar():
    adata = _make_grid_adata([0.0, 10.0, 5.0, 15.0])

    fig = render_heatmap_figure(adata, mode="feature", mz=100.0, layer="X_unused_falls_back_to_X")

    assert isinstance(fig, matplotlib.figure.Figure)
    # One image axes + one colorbar axes.
    assert len(fig.axes) == 2


def test_render_heatmap_figure_obs_categorical_mode_draws_legend_not_colorbar():
    adata = _make_grid_adata(
        [0.0, 0.0, 0.0, 0.0],
        obs_columns={"polarity": ["negative", "positive", "negative", "positive"]},
    )

    fig = render_heatmap_figure(
        adata, mode="obs_categorical", obs_column="polarity",
        categories=["negative", "positive"],
    )

    assert len(fig.axes) == 1  # no separate colorbar axes
    assert fig.axes[0].get_legend() is not None


def test_render_heatmap_figure_scientific_notation_above_threshold():
    adata = _make_grid_adata([0.0, 10.0, 5.0, 15.0])

    fig_small = render_heatmap_figure(
        adata, mode="feature", mz=100.0, layer="X_unused_falls_back_to_X",
        vmin=0.0, vmax=15.0,
    )
    fig_large = render_heatmap_figure(
        adata, mode="feature", mz=100.0, layer="X_unused_falls_back_to_X",
        vmin=0.0, vmax=5000.0,
    )

    small_formatter = fig_small.axes[1].yaxis.get_major_formatter()
    large_formatter = fig_large.axes[1].yaxis.get_major_formatter()
    assert not isinstance(small_formatter, matplotlib.ticker.ScalarFormatter) or \
        small_formatter.get_useMathText() is False or small_formatter._powerlimits != (0, 0)
    assert isinstance(large_formatter, matplotlib.ticker.ScalarFormatter)
    assert large_formatter._powerlimits == (0, 0)


def test_render_heatmap_figure_draws_roi_polygon():
    adata = _make_grid_adata([0.0, 10.0, 5.0, 15.0])
    rois = {"tumor": {"color": "#ff0000", "vertices": [[0, 0], [1, 0], [1, 1], [0, 1]]}}

    fig = render_heatmap_figure(
        adata, mode="feature", mz=100.0, layer="X_unused_falls_back_to_X", rois=rois,
    )

    patches = [p for p in fig.axes[0].patches if isinstance(p, matplotlib.patches.Polygon)]
    assert len(patches) == 1


def test_render_heatmap_figure_autoscales_when_vmin_vmax_are_none():
    adata = _make_grid_adata([0.0, 10.0, 5.0, 15.0])

    fig = render_heatmap_figure(adata, mode="feature", mz=100.0, layer="X_unused_falls_back_to_X")

    # Colorbar's own scale reflects this sample's actual data range.
    cbar_axes = fig.axes[1]
    assert cbar_axes.get_ylim()[0] == 0.0
    assert cbar_axes.get_ylim()[1] == 15.0


def test_render_heatmap_figure_rejects_unknown_mode():
    adata = _make_grid_adata([0.0, 10.0, 5.0, 15.0])
    with pytest.raises(ValueError):
        render_heatmap_figure(adata, mode="bogus")


# ---------------------------------------------------------------------------
# Imported datasets (ADR 49): stored grid axes + render m/z guard
# ---------------------------------------------------------------------------


def test_pixel_grid_indices_uses_stored_full_grid_axes():
    # Only x=1 pixels left (the x=0 column was filtered out). Without the
    # stored axes the grid would collapse to 1 column and x=1 would land
    # on column 0.
    adata = _make_grid_adata([1.0, 2.0, 3.0, 4.0])[[1, 3]].copy()
    adata.uns[GRID_UNS_KEY] = {"x": np.array([0.0, 1.0]), "y": np.array([0.0, 1.0])}

    x_index, y_index, unique_x, unique_y = pixel_grid_indices(adata)

    np.testing.assert_array_equal(x_index, [1, 1])
    np.testing.assert_array_equal(y_index, [0, 1])
    assert unique_x.size == 2 and unique_y.size == 2
    rgba = render_feature_heatmap(adata, 100.0, layer="raw")
    assert rgba.shape[:2] == (2, 2)
    # the missing column renders black
    assert (rgba[:, 0, :3] == 0).all()


def test_render_feature_heatmap_imported_missing_feature_renders_black_not_neighbor():
    adata = _make_grid_adata([1.0, 2.0, 3.0, 4.0])
    adata.uns[GRID_UNS_KEY] = {"x": np.array([0.0, 1.0]), "y": np.array([0.0, 1.0])}
    # 150.0 is 50 Da away from the only var (100.0): nearest-match alone
    # would silently render mz 100's intensities.
    rgba = render_feature_heatmap(adata, 150.0, layer="raw")
    assert (rgba[..., :3] == 0).all()
    assert feature_value_range(adata, 150.0, layer="raw") == (0.0, 1.0)


def test_render_feature_heatmap_imported_tolerates_tiny_mz_roundoff():
    adata = _make_grid_adata([1.0, 2.0, 3.0, 4.0])
    adata.uns[GRID_UNS_KEY] = {"x": np.array([0.0, 1.0]), "y": np.array([0.0, 1.0])}
    exact = render_feature_heatmap(adata, 100.0, layer="raw")
    np.testing.assert_array_equal(
        render_feature_heatmap(adata, 100.0 + 1e-7, layer="raw"), exact
    )


def test_list_obs_columns_hides_imported_bookkeeping_columns():
    adata = _make_grid_adata(
        [1.0, 2.0, 3.0, 4.0],
        obs_columns={
            "sample": ["s1"] * 4,
            "source_obs_name": ["a", "b", "c", "d"],
            "leiden": ["0", "1", "0", "1"],
        },
    )
    names = {c["name"] for c in list_obs_columns(adata)}
    assert "leiden" in names
    assert not names & {"sample", "source_obs_name"}


def test_scale_bar_spec_converts_um_to_pixels_per_sample():
    settings = {"enabled": True, "bar_um": 50.0, "color": "#ffffff",
                "pixel_um": {"s1": 10.0, "s2": 25.0}}

    assert scale_bar_spec(settings, "s1") == {"length_px": 5.0, "label": "50 µm", "color": "#ffffff"}
    assert scale_bar_spec(settings, "s2")["length_px"] == 2.0
    assert scale_bar_spec(settings, "unset") is None
    assert scale_bar_spec({**settings, "enabled": False}, "s1") is None


def test_render_heatmap_figure_draws_scale_bar_bottom_right():
    adata = _make_grid_adata([0.0, 10.0, 5.0, 15.0])
    bar = {"length_px": 1.0, "label": "10 µm", "color": "#ff0000"}

    fig = render_heatmap_figure(adata, mode="feature", mz=100.0, scale_bar=bar)

    ax = fig.axes[0]
    rect = next(p for p in ax.patches if isinstance(p, matplotlib.patches.Rectangle))
    assert rect.get_width() == 1.0
    # Right edge inside the 2-pixel raster (extent -0.5..1.5).
    assert -0.5 < rect.get_x() + rect.get_width() < 1.5
    assert [t.get_text() for t in ax.texts] == ["10 µm"]

    plain = render_heatmap_figure(adata, mode="feature", mz=100.0)
    assert not plain.axes[0].patches and not plain.axes[0].texts
