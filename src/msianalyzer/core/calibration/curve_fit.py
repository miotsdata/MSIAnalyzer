from __future__ import annotations

from dataclasses import dataclass

import numpy as np

_LOD_FACTOR = 3.3
_LOQ_FACTOR = 10.0


@dataclass
class CalibrationFitResult:
    """A fitted calibration curve — one row of `analysis_db.calibration_curves`.

    Attributes:
        fit_type: `"linear"` or `"weighted_linear"`.
        weighting: `"1/x"` or `"1/x2"` for a weighted fit, `None` otherwise.
        slope: Fitted slope (intensity per unit concentration).
        intercept: Fitted intercept.
        r_squared: Coefficient of determination — the weighted version for
            a weighted fit, not the plain formula (see `fit_calibration_curve`).
        lod: Limit of detection (`3.3 * residual_std / |slope|`), or `None`
            if `slope == 0`.
        loq: Limit of quantification (`10 * residual_std / |slope|`), or
            `None` if `slope == 0`.
        n_points: Number of calibration points the fit used.
    """

    fit_type: str
    weighting: str | None
    slope: float
    intercept: float
    r_squared: float
    lod: float | None
    loq: float | None
    n_points: int


def _weighting_sigma(concentrations: np.ndarray, weighting: str) -> np.ndarray:
    """Per-point standard deviation (`sigma`, not a weight) for
    `scipy.optimize.curve_fit`'s `sigma=` argument.

    `curve_fit` weights each residual by `1/sigma**2`, so `"1/x"`
    weighting (weight `1/x`) needs `sigma = sqrt(x)`, and `"1/x2"`
    (weight `1/x**2`) needs `sigma = x` — easy to invert by accident,
    called out explicitly here since nothing else guards against it.

    A `concentration == 0` blank standard would give `sigma == 0` (i.e.
    infinite weight) under either formula — excluded from the weight
    computation by falling back to an unweighted (`sigma = 1`) value for
    that point only, so it still participates in the fit without
    dominating it.
    """
    if weighting == "1/x":
        sigma = np.sqrt(np.abs(concentrations))
    elif weighting == "1/x2":
        sigma = np.abs(concentrations)
    else:
        raise ValueError(f"weighting must be '1/x' or '1/x2', got {weighting!r}")
    return np.where(concentrations == 0, 1.0, sigma)


def _linear(x: np.ndarray, slope: float, intercept: float) -> np.ndarray:
    return slope * x + intercept


def fit_calibration_curve(
    points: list[tuple[float, float]],
    fit_type: str = "linear",
    weighting: str | None = None,
) -> CalibrationFitResult:
    """Fit a calibration curve (concentration -> mean intensity).

    Args:
        points: `(concentration, mean_intensity)` pairs, one per standard.
        fit_type: `"linear"` (ordinary least squares) or
            `"weighted_linear"` (weighted least squares, see `weighting`).
        weighting: Required when `fit_type="weighted_linear"` — `"1/x"` or
            `"1/x2"` (the more common bioanalytical default). Ignored for
            `fit_type="linear"`.

    Returns:
        The fitted `CalibrationFitResult`.

    Raises:
        ValueError: Fewer than 2 points, an invalid `fit_type`, or
            `fit_type="weighted_linear"` without a valid `weighting`.
    """
    if fit_type not in ("linear", "weighted_linear"):
        raise ValueError(
            f"fit_type must be 'linear' or 'weighted_linear', got {fit_type!r}"
        )
    if len(points) < 2:
        raise ValueError(
            f"At least 2 calibration points are required to fit a curve, got {len(points)}"
        )
    if fit_type == "weighted_linear" and weighting not in ("1/x", "1/x2"):
        raise ValueError(
            "weighting must be '1/x' or '1/x2' when fit_type='weighted_linear', "
            f"got {weighting!r}"
        )

    x = np.array([p[0] for p in points], dtype=float)
    y = np.array([p[1] for p in points], dtype=float)

    # Deferred: scipy.stats + scipy.optimize cost ~0.4 s at GUI startup.
    from scipy.optimize import curve_fit
    from scipy.stats import linregress

    if fit_type == "linear":
        result = linregress(x, y)
        slope, intercept = float(result.slope), float(result.intercept)
        r_squared = float(result.rvalue**2)
        residuals = y - _linear(x, slope, intercept)
    else:
        sigma = _weighting_sigma(x, weighting)
        (slope, intercept), _ = curve_fit(_linear, x, y, sigma=sigma)
        slope, intercept = float(slope), float(intercept)
        residuals = y - _linear(x, slope, intercept)
        weights = 1.0 / sigma**2
        weighted_mean_y = float(np.average(y, weights=weights))
        ss_res = float(np.sum(weights * residuals**2))
        ss_tot = float(np.sum(weights * (y - weighted_mean_y) ** 2))
        r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    lod = loq = None
    if slope != 0:
        ddof = 2 if len(points) > 2 else 0
        residual_std = float(np.std(residuals, ddof=ddof))
        lod = _LOD_FACTOR * residual_std / abs(slope)
        loq = _LOQ_FACTOR * residual_std / abs(slope)

    return CalibrationFitResult(
        fit_type=fit_type,
        weighting=weighting if fit_type == "weighted_linear" else None,
        slope=slope,
        intercept=intercept,
        r_squared=r_squared,
        lod=lod,
        loq=loq,
        n_points=len(points),
    )


def interpolate_concentration(fit: CalibrationFitResult, measured_intensity: float) -> float:
    """Interpolate a concentration from a measured intensity, using a
    fitted calibration curve.

    Args:
        fit: A `CalibrationFitResult` from `fit_calibration_curve`.
        measured_intensity: An unknown sample's ROI mean intensity.

    Returns:
        The interpolated concentration: `(measured_intensity - intercept) / slope`.

    Raises:
        ValueError: `fit.slope == 0` (a flat curve can't be inverted).
    """
    if fit.slope == 0:
        raise ValueError("Cannot interpolate concentration: calibration curve has zero slope.")
    return (measured_intensity - fit.intercept) / fit.slope
