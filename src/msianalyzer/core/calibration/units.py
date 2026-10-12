from __future__ import annotations

#: Physical unit an ROI's pixel edge length is given in — square pixels
#: assumed (one raster spot covers `pixel_size x pixel_size`).
PIXEL_SIZE_UNITS = ("um", "mm")

#: Amount-of-substance units a calibration standard's total spot content
#: can be entered in.
AMOUNT_UNITS = ("pmol", "nmol", "umol", "mmol", "mol")


def pixel_area(n_pixels: int, pixel_size: float, pixel_size_unit: str) -> float:
    """Physical area covered by `n_pixels` square pixels.

    The user enters concentration indirectly: a calibration standard's
    known quantity is a total *amount* in the spot (e.g. pmol), not a
    concentration — concentration only exists once that amount is spread
    over the ROI's actual physical area, which this computes from the
    pixel count already known from the drawn ROI and a pixel edge length
    the user provides (the MSI raster's spot spacing, assumed square and,
    for v1, uniform enough to be entered once — not measured per pixel).

    Args:
        n_pixels: Number of pixels in the ROI (`roi_intensity.roi_pixel_count`).
        pixel_size: Physical edge length of one (square) pixel.
        pixel_size_unit: `"um"` or `"mm"` — the returned area is in this
            unit squared (e.g. `pixel_size_unit="um"` -> the result is in
            um²). Validated here rather than assumed valid by a caller,
            since a silently-wrong unit would corrupt every concentration
            computed from it.

    Returns:
        `n_pixels * pixel_size ** 2`.

    Raises:
        ValueError: `pixel_size_unit` isn't `"um"`/`"mm"`, or `pixel_size`/
            `n_pixels` isn't positive.
    """
    if pixel_size_unit not in PIXEL_SIZE_UNITS:
        raise ValueError(
            f"pixel_size_unit must be one of {PIXEL_SIZE_UNITS}, got {pixel_size_unit!r}"
        )
    if pixel_size <= 0:
        raise ValueError(f"pixel_size must be positive, got {pixel_size}")
    if n_pixels <= 0:
        raise ValueError(f"n_pixels must be positive, got {n_pixels}")
    return n_pixels * pixel_size**2


def concentration_from_amount(amount: float, area: float) -> float:
    """A calibration point's concentration (the curve's x-value):
    `amount / area` — e.g. pmol / mm².

    Args:
        amount: Total quantity of the analyte in the spot.
        area: The spot ROI's physical area (`pixel_area`), same
            length-unit-squared the concentration will be reported in.

    Raises:
        ValueError: `area` isn't positive.
    """
    if area <= 0:
        raise ValueError(f"area must be positive, got {area}")
    return amount / area


def amount_from_concentration(concentration: float, area: float) -> float:
    """Inverse of `concentration_from_amount` — recovers an absolute
    amount from an unknown's interpolated concentration and its own ROI's
    area, so quantification results can report both "how concentrated"
    and "how much" for the same region.
    """
    return concentration * area


def concentration_unit(amount_unit: str, pixel_size_unit: str) -> str:
    """Canonical unit string for a concentration/curve, e.g. `"pmol/mm2"`.

    Args:
        amount_unit: One of `AMOUNT_UNITS`.
        pixel_size_unit: One of `PIXEL_SIZE_UNITS`.
    """
    return f"{amount_unit}/{pixel_size_unit}2"


def area_conversion_factor(from_unit: str, to_unit: str) -> float:
    """How many `to_unit`-squared fit in one `from_unit`-squared —
    `area_in_to_unit = area_in_from_unit * area_conversion_factor(from_unit, to_unit)`.

    Used to redisplay an already-computed area/concentration/curve in a
    different square-length unit than it was originally computed in
    (`AnalysisBridge.getCalibrationPlotData`'s `display_area_unit`, and
    `CalibrationSection.qml`'s own mirror of this same formula for its
    tables/stats label) — a live, non-destructive display choice, not a
    change to what's stored.

    Args:
        from_unit: One of `PIXEL_SIZE_UNITS` — the unit the value is
            currently expressed in.
        to_unit: One of `PIXEL_SIZE_UNITS` — the unit to convert to.

    Returns:
        `1e6` for `"mm"` -> `"um"` (1 mm² = 1,000,000 µm²), `1e-6` for the
        reverse, `1.0` if `from_unit == to_unit`.

    Raises:
        ValueError: Either unit isn't in `PIXEL_SIZE_UNITS`.
    """
    if from_unit not in PIXEL_SIZE_UNITS or to_unit not in PIXEL_SIZE_UNITS:
        raise ValueError(
            f"from_unit/to_unit must be one of {PIXEL_SIZE_UNITS}, "
            f"got {from_unit!r}/{to_unit!r}"
        )
    if from_unit == to_unit:
        return 1.0
    return 1e6 if from_unit == "mm" else 1e-6
