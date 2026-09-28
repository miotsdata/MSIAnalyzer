from __future__ import annotations

import numpy as np
import pytest

from msianalyzer.core.calibration.curve_fit import (
    CalibrationFitResult,
    fit_calibration_curve,
    interpolate_concentration,
)


def test_fit_calibration_curve_linear_exact_fit():
    # y = 2x + 3 exactly
    points = [(x, 2 * x + 3) for x in [0.0, 1.0, 2.0, 3.0, 4.0]]

    result = fit_calibration_curve(points, fit_type="linear")

    assert isinstance(result, CalibrationFitResult)
    assert result.fit_type == "linear"
    assert result.weighting is None
    assert result.slope == pytest.approx(2.0)
    assert result.intercept == pytest.approx(3.0)
    assert result.r_squared == pytest.approx(1.0)
    assert result.n_points == 5
    assert result.lod == pytest.approx(0.0, abs=1e-9)
    assert result.loq == pytest.approx(0.0, abs=1e-9)


def test_fit_calibration_curve_requires_at_least_two_points():
    with pytest.raises(ValueError, match="At least 2 calibration points"):
        fit_calibration_curve([(1.0, 2.0)], fit_type="linear")


def test_fit_calibration_curve_rejects_invalid_fit_type():
    with pytest.raises(ValueError, match="fit_type must be"):
        fit_calibration_curve([(0.0, 0.0), (1.0, 1.0)], fit_type="quadratic")


def test_fit_calibration_curve_weighted_linear_requires_weighting():
    with pytest.raises(ValueError, match="weighting must be"):
        fit_calibration_curve(
            [(1.0, 2.0), (2.0, 4.0)], fit_type="weighted_linear", weighting=None
        )


def test_fit_calibration_curve_weighted_linear_rejects_invalid_weighting():
    with pytest.raises(ValueError, match="weighting must be"):
        fit_calibration_curve(
            [(1.0, 2.0), (2.0, 4.0)], fit_type="weighted_linear", weighting="1/x3"
        )


@pytest.mark.parametrize("weighting", ["1/x", "1/x2"])
def test_fit_calibration_curve_weighted_linear_recovers_known_slope(weighting):
    rng = np.random.default_rng(42)
    x = np.array([1.0, 2.0, 5.0, 10.0, 20.0, 50.0])
    true_slope, true_intercept = 3.0, 1.0
    y = true_slope * x + true_intercept + rng.normal(0, 0.01, size=x.size)
    points = list(zip(x.tolist(), y.tolist()))

    result = fit_calibration_curve(points, fit_type="weighted_linear", weighting=weighting)

    assert result.weighting == weighting
    assert result.slope == pytest.approx(true_slope, rel=0.05)
    assert result.intercept == pytest.approx(true_intercept, abs=0.5)
    assert 0.0 <= result.r_squared <= 1.0 + 1e-9
    assert result.lod is not None and result.lod >= 0
    assert result.loq is not None and result.loq >= 0


@pytest.mark.parametrize("weighting", ["1/x", "1/x2"])
def test_fit_calibration_curve_weighted_linear_handles_zero_concentration_blank(weighting):
    # A blank standard (concentration 0) would give sigma=0 under either
    # weighting formula if not special-cased -> must not raise or produce
    # non-finite results.
    points = [(0.0, 0.5), (1.0, 2.1), (2.0, 4.0), (5.0, 10.2), (10.0, 20.1)]

    result = fit_calibration_curve(points, fit_type="weighted_linear", weighting=weighting)

    assert np.isfinite(result.slope)
    assert np.isfinite(result.intercept)
    assert np.isfinite(result.r_squared)


def test_fit_calibration_curve_zero_slope_gives_no_lod_loq():
    points = [(0.0, 5.0), (1.0, 5.0), (2.0, 5.0)]

    result = fit_calibration_curve(points, fit_type="linear")

    assert result.slope == pytest.approx(0.0, abs=1e-9)
    assert result.lod is None
    assert result.loq is None


def test_interpolate_concentration_basic():
    fit = CalibrationFitResult(
        fit_type="linear", weighting=None, slope=2.0, intercept=3.0,
        r_squared=1.0, lod=0.0, loq=0.0, n_points=5,
    )
    assert interpolate_concentration(fit, 11.0) == pytest.approx(4.0)


def test_interpolate_concentration_zero_slope_raises():
    fit = CalibrationFitResult(
        fit_type="linear", weighting=None, slope=0.0, intercept=3.0,
        r_squared=0.0, lod=None, loq=None, n_points=3,
    )
    with pytest.raises(ValueError, match="zero slope"):
        interpolate_concentration(fit, 5.0)
