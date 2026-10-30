from __future__ import annotations

import anndata as ad
import numpy as np
import pandas as pd
import pytest
from scipy.sparse import csr_matrix

from msianalyzer.core.plotting.feature_overlay import (
    Channel,
    auto_range,
    composite,
    parse_channels,
)
from msianalyzer.core.plotting.heatmap import render_feature_overlay, render_heatmap_by_target

RED, GREEN, BLUE = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)


def _grid(*rows):
    return np.array(rows, dtype=float)


def test_parse_channels():
    channels = parse_channels("150.5:ff0000:auto:10:1,200:00ff80:2:auto:0.5")
    assert channels[0] == Channel(150.5, (1.0, 0.0, 0.0), None, 10.0, 1.0)
    assert channels[1].color == pytest.approx((0.0, 1.0, 128 / 255))
    assert (channels[1].vmin, channels[1].vmax, channels[1].gamma) == (2.0, None, 0.5)
    assert parse_channels("") == []


def test_add_mixes_overlapping_channels():
    a = _grid([0, 10], [10, 0])
    b = _grid([0, 10], [0, 10])
    rgba = composite([a, b], [Channel(1, RED, 0, 10), Channel(2, GREEN, 0, 10)], "add")
    assert rgba.dtype == np.uint8 and rgba.shape == (2, 2, 4)
    assert tuple(rgba[0, 1]) == (255, 255, 0, 255)  # both: yellow
    assert tuple(rgba[1, 0]) == (255, 0, 0, 255)  # red only
    assert tuple(rgba[1, 1]) == (0, 255, 0, 255)  # green only
    assert tuple(rgba[0, 0]) == (0, 0, 0, 255)  # neither


def test_add_clips_and_order_does_not_matter():
    a = _grid([10]); b = _grid([10])
    c1 = [Channel(1, (1.0, 0.5, 0.0), 0, 10), Channel(2, (1.0, 0.5, 0.0), 0, 10)]
    rgba = composite([a, b], c1, "add")
    assert tuple(rgba[0, 0, :3]) == (255, 255, 0)
    np.testing.assert_array_equal(composite([b, a], c1[::-1], "add"), rgba)


def test_max_takes_brightest_component():
    a = _grid([10]); b = _grid([5])
    rgba = composite([a, b], [Channel(1, (1.0, 0.5, 0.0), 0, 10), Channel(2, (0.4, 1.0, 0.0), 0, 10)], "max")
    assert tuple(rgba[0, 0, :3]) == (255, 128, 0)


def test_contrast_and_gamma():
    g = _grid([0, 5, 10, 20])
    rgba = composite([g], [Channel(1, RED, 5, 10, 1.0)])
    assert list(rgba[0, :, 0]) == [0, 0, 255, 255]
    gamma = composite([_grid([7.5])], [Channel(1, RED, 5, 10, 2.0)])
    assert gamma[0, 0, 0] == round(0.25 * 255)


def test_outside_the_sample_is_black_even_with_a_constant_channel():
    g = _grid([np.nan, 3.0])
    rgba = composite([g], [Channel(1, BLUE, 0, 1)])
    assert tuple(rgba[0, 0]) == (0, 0, 0, 255)
    assert tuple(rgba[0, 1]) == (0, 0, 255, 255)


def test_auto_range_ignores_zeros_and_hotspots():
    values = np.zeros(1000)
    values[:800] = np.linspace(8, 10, 800)
    values[0] = 1000.0  # one hotspot
    vmin, vmax = auto_range(values)
    assert vmin == 0.0
    assert 9.9 < vmax < 11  # the 99.5th percentile of the detected values
    assert auto_range(np.zeros(5)) == (0.0, 1.0)
    assert auto_range(np.array([np.nan])) == (0.0, 1.0)


def test_bad_mode_or_no_channel_raises():
    with pytest.raises(ValueError, match="mode"):
        composite([_grid([1])], [Channel(1, RED)], "multiply")
    with pytest.raises(ValueError, match="channel"):
        composite([], [], "add")


def _adata():
    xs, ys = np.meshgrid(np.arange(3), np.arange(2))
    X = np.zeros((6, 2), dtype=np.float32)
    X[0, 0] = 10.0  # feature 100 at pixel (0, 0)
    X[5, 1] = 10.0  # feature 200 at pixel (2, 1)
    var = pd.DataFrame({"mz": [100.0, 200.0]}, index=["mz_100.0000", "mz_200.0000"])
    adata = ad.AnnData(X=csr_matrix(X), var=var)
    adata.layers["TIC"] = csr_matrix(X)
    adata.obsm["spatial"] = np.column_stack([xs.ravel(), ys.ravel()]).astype(float)
    return adata


def test_render_feature_overlay_on_the_sample_grid():
    rgba = render_feature_overlay(
        _adata(), [Channel(100.0, RED, 0, 10), Channel(200.0, GREEN, 0, 10)], layer="TIC"
    )
    assert rgba.shape == (2, 3, 4)
    assert tuple(rgba[0, 0, :3]) == (255, 0, 0)
    assert tuple(rgba[1, 2, :3]) == (0, 255, 0)
    assert tuple(rgba[1, 1, :3]) == (0, 0, 0)


def test_heatmap_dispatch_renders_overlay_ids():
    parts = "s1|overlay|TIC|add|100:ff0000:0:10:1,200:00ff00:0:10:1|ds-1".split("|")
    rgba = render_heatmap_by_target(_adata(), parts[1], parts)
    assert tuple(rgba[0, 0, :3]) == (255, 0, 0)
    assert tuple(rgba[1, 2, :3]) == (0, 255, 0)
