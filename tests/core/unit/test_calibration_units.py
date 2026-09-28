from __future__ import annotations

import pytest

from msianalyzer.core.calibration.units import (
    amount_from_concentration,
    area_conversion_factor,
    concentration_from_amount,
    concentration_unit,
    pixel_area,
)


def test_pixel_area_computes_n_pixels_times_size_squared():
    assert pixel_area(n_pixels=4, pixel_size=2.0, pixel_size_unit="um") == pytest.approx(16.0)


def test_pixel_area_rejects_invalid_unit():
    with pytest.raises(ValueError, match="pixel_size_unit must be"):
        pixel_area(4, 2.0, "cm")


def test_pixel_area_rejects_non_positive_pixel_size():
    with pytest.raises(ValueError, match="pixel_size must be positive"):
        pixel_area(4, 0.0, "um")


def test_pixel_area_rejects_non_positive_n_pixels():
    with pytest.raises(ValueError, match="n_pixels must be positive"):
        pixel_area(0, 2.0, "um")


def test_concentration_from_amount_divides_by_area():
    assert concentration_from_amount(amount=10.0, area=4.0) == pytest.approx(2.5)


def test_concentration_from_amount_rejects_non_positive_area():
    with pytest.raises(ValueError, match="area must be positive"):
        concentration_from_amount(10.0, 0.0)


def test_amount_from_concentration_is_the_inverse():
    concentration = concentration_from_amount(amount=10.0, area=4.0)
    assert amount_from_concentration(concentration, area=4.0) == pytest.approx(10.0)


def test_concentration_unit_formats_amount_over_area_squared():
    assert concentration_unit("pmol", "mm") == "pmol/mm2"
    assert concentration_unit("nmol", "um") == "nmol/um2"


def test_area_conversion_factor_mm_to_um_is_one_million():
    assert area_conversion_factor("mm", "um") == pytest.approx(1e6)


def test_area_conversion_factor_um_to_mm_is_one_millionth():
    assert area_conversion_factor("um", "mm") == pytest.approx(1e-6)


def test_area_conversion_factor_same_unit_is_one():
    assert area_conversion_factor("mm", "mm") == 1.0
    assert area_conversion_factor("um", "um") == 1.0


def test_area_conversion_factor_round_trips():
    area_mm2 = 0.04
    area_um2 = area_mm2 * area_conversion_factor("mm", "um")
    back_to_mm2 = area_um2 * area_conversion_factor("um", "mm")
    assert back_to_mm2 == pytest.approx(area_mm2)


def test_area_conversion_factor_rejects_invalid_unit():
    with pytest.raises(ValueError, match="from_unit/to_unit"):
        area_conversion_factor("cm", "mm")
