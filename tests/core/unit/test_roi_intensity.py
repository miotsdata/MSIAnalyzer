from __future__ import annotations

import anndata as ad
import numpy as np
import pandas as pd
import pytest
from scipy.sparse import csr_matrix

from msianalyzer.core.calibration.roi_intensity import (
    roi_internal_standard_signal,
    roi_mean_intensity,
    roi_pixel_count,
)


def _make_adata():
    # 4 pixels, one feature (mz=100.0); pixels 0/1 belong to "spot_a".
    var = pd.DataFrame({"mz": [100.0]}, index=["mz_100.0000"])
    obs = pd.DataFrame(
        {"roi_spot_a": [True, True, False, False]},
        index=["p0", "p1", "p2", "p3"],
    )
    X = csr_matrix(np.array([[10.0], [30.0], [100.0], [200.0]], dtype=np.float32))
    adata = ad.AnnData(X=X, obs=obs, var=var)
    adata.layers["raw"] = X.copy()
    return adata


def _make_two_feature_adata():
    # 4 pixels, analyte (mz=100.0) + reference (mz=200.0); pixels 0/1 in
    # "spot_a". Reference varies enough between the two ROI pixels (100,
    # 50) that a ratio-of-means would give a different answer than a
    # mean-of-ratios (0.2667 vs 0.35), letting a test tell the two apart.
    var = pd.DataFrame({"mz": [100.0, 200.0]}, index=["mz_100.0000", "mz_200.0000"])
    obs = pd.DataFrame(
        {"roi_spot_a": [True, True, False, False]},
        index=["p0", "p1", "p2", "p3"],
    )
    analyte = np.array([10.0, 30.0, 100.0, 200.0], dtype=np.float32)
    reference = np.array([100.0, 50.0, 999.0, 999.0], dtype=np.float32)
    X = csr_matrix(np.column_stack([analyte, reference]))
    adata = ad.AnnData(X=X, obs=obs, var=var)
    adata.layers["raw"] = X.copy()
    return adata


def test_roi_mean_intensity_averages_only_roi_pixels():
    adata = _make_adata()
    result = roi_mean_intensity(adata, "spot_a", mz=100.0, layer="raw")
    assert result == pytest.approx(20.0)  # mean(10, 30)


def test_roi_mean_intensity_uses_nearest_mz():
    adata = _make_adata()
    result = roi_mean_intensity(adata, "spot_a", mz=100.1, layer="raw")
    assert result == pytest.approx(20.0)


def test_roi_mean_intensity_missing_roi_returns_none():
    adata = _make_adata()
    assert roi_mean_intensity(adata, "does_not_exist", mz=100.0) is None


def test_roi_mean_intensity_empty_roi_returns_none():
    adata = _make_adata()
    adata.obs["roi_empty"] = [False, False, False, False]
    assert roi_mean_intensity(adata, "empty", mz=100.0) is None


def test_roi_mean_intensity_falls_back_to_x_when_layer_missing():
    adata = _make_adata()
    del adata.layers["raw"]
    result = roi_mean_intensity(adata, "spot_a", mz=100.0, layer="raw")
    assert result == pytest.approx(20.0)


def test_roi_pixel_count():
    adata = _make_adata()
    assert roi_pixel_count(adata, "spot_a") == 2


def test_roi_pixel_count_missing_roi_returns_zero():
    adata = _make_adata()
    assert roi_pixel_count(adata, "does_not_exist") == 0


def test_roi_internal_standard_signal_averages_per_pixel_ratio_not_ratio_of_means():
    adata = _make_two_feature_adata()
    result = roi_internal_standard_signal(adata, "spot_a", analyte_mz=100.0, reference_mz=200.0)
    # mean(10/100, 30/50) = mean(0.1, 0.6) = 0.35 -- NOT 20/75 = 0.2667,
    # the ratio-of-means a naive implementation might compute instead.
    assert result == pytest.approx(0.35)


def test_roi_internal_standard_signal_uses_nearest_mz_for_both_channels():
    adata = _make_two_feature_adata()
    result = roi_internal_standard_signal(
        adata, "spot_a", analyte_mz=100.1, reference_mz=199.9
    )
    assert result == pytest.approx(0.35)


def test_roi_internal_standard_signal_missing_roi_returns_none():
    adata = _make_two_feature_adata()
    assert roi_internal_standard_signal(
        adata, "does_not_exist", analyte_mz=100.0, reference_mz=200.0
    ) is None


def test_roi_internal_standard_signal_empty_roi_returns_none():
    adata = _make_two_feature_adata()
    adata.obs["roi_empty"] = [False, False, False, False]
    assert roi_internal_standard_signal(
        adata, "empty", analyte_mz=100.0, reference_mz=200.0
    ) is None


def test_roi_internal_standard_signal_excludes_non_positive_reference_pixels():
    adata = _make_two_feature_adata()
    raw = adata.layers["raw"].tolil()
    raw[0, 1] = 0.0  # pixel 0's reference channel is zero
    adata.layers["raw"] = raw.tocsr()
    result = roi_internal_standard_signal(adata, "spot_a", analyte_mz=100.0, reference_mz=200.0)
    assert result == pytest.approx(0.6)  # only pixel 1's ratio (30/50) survives


def test_roi_internal_standard_signal_none_when_every_reference_pixel_is_zero():
    adata = _make_two_feature_adata()
    raw = adata.layers["raw"].tolil()
    raw[[0, 1], 1] = 0.0
    adata.layers["raw"] = raw.tocsr()
    assert roi_internal_standard_signal(
        adata, "spot_a", analyte_mz=100.0, reference_mz=200.0
    ) is None


def test_roi_internal_standard_signal_falls_back_to_x_when_layer_missing():
    adata = _make_two_feature_adata()
    del adata.layers["raw"]
    result = roi_internal_standard_signal(adata, "spot_a", analyte_mz=100.0, reference_mz=200.0)
    assert result == pytest.approx(0.35)
