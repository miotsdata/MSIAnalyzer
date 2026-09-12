"""Tests for the ported reverse-dot-product matcher.

`MatchResult` now carries the noise-filtered spectra of *both* sides so the
annotator can persist them for mirror plots.
"""

import numpy as np
import pytest

from msianalyzer.core.annotation.spectral_match import (
    MatchResult,
    normalize_and_filter_spectrum,
    reverse_dot_product,
)


def test_identical_spectra_score_near_one():
    mz = np.array([80.05, 120.08, 160.10, 200.12, 240.15])
    inten = np.array([100.0, 500.0, 250.0, 900.0, 50.0])

    m = reverse_dot_product(mz, inten, mz.copy(), inten.copy(), ppm_tolerance=10.0)

    assert isinstance(m, MatchResult)
    assert m.score == pytest.approx(1.0, abs=1e-6)
    assert m.dot_product_score == pytest.approx(1.0, abs=1e-6)
    assert m.lib_coverage == pytest.approx(1.0)
    assert m.emp_coverage == pytest.approx(1.0)
    assert m.n_matched_peaks == m.n_lib_peaks


def test_disjoint_spectra_score_zero():
    q_mz = np.array([100.0, 150.0, 200.0])
    l_mz = np.array([300.0, 350.0, 400.0])
    inten = np.array([1.0, 1.0, 1.0])

    m = reverse_dot_product(q_mz, inten, l_mz, inten, ppm_tolerance=5.0)

    assert m.score == 0.0
    assert m.n_matched_peaks == 0


def test_noise_threshold_drops_sub_threshold_peaks_on_both_sides():
    # base peak 1000, a 5-unit peak is 0.005 -> below a 0.01 cut
    mz = np.array([100.0, 200.0, 300.0])
    inten = np.array([1000.0, 5.0, 800.0])

    m = reverse_dot_product(
        mz, inten, mz.copy(), inten.copy(), ppm_tolerance=10.0, noise_threshold=0.01
    )

    assert m.n_emp_peaks_raw == 3
    assert m.n_emp_peaks_filtered == 2
    assert 200.0 not in set(np.round(m.filtered_mz, 4))
    assert 200.0 not in set(np.round(m.lib_filtered_mz, 4))
    # surviving intensities are max-normalised
    assert m.filtered_intensity.max() == pytest.approx(1.0)
    assert m.lib_filtered_intensity.max() == pytest.approx(1.0)


def test_empty_library_guard_returns_empty_filtered_fields():
    m = reverse_dot_product(
        np.array([100.0, 200.0]),
        np.array([1.0, 1.0]),
        np.array([]),
        np.array([]),
    )
    assert m.score == 0.0
    assert m.n_lib_peaks == 0
    for arr in (
        m.filtered_mz,
        m.filtered_intensity,
        m.lib_filtered_mz,
        m.lib_filtered_intensity,
    ):
        assert isinstance(arr, np.ndarray)
        assert arr.size == 0


def test_all_empirical_peaks_below_threshold_keeps_library_filtered_spectrum():
    # every empirical peak is identical -> none dropped; but make the library
    # survive while the empirical filtered set is non-empty and disjoint
    q_mz = np.array([111.0, 222.0])
    q_int = np.array([1.0, 1.0])
    l_mz = np.array([900.0, 950.0])
    l_int = np.array([1.0, 1.0])

    m = reverse_dot_product(q_mz, q_int, l_mz, l_int, ppm_tolerance=5.0)

    assert m.n_emp_peaks_filtered == 2
    assert set(np.round(m.lib_filtered_mz, 1)) == {900.0, 950.0}
    assert m.score == 0.0


# ---------------------------------------------------------------------------
# configurable score weights
# ---------------------------------------------------------------------------

# every library peak matches (lib_coverage == 1), plus two unmatched
# empirical peaks push emp_coverage below 1 without touching dot_product_score
# or lib_coverage — the "high dot product, high lib coverage, low emp
# coverage" shape the weights exist to rebalance.
_Q_MZ = np.array([100.0, 200.0, 300.0, 400.0, 500.0])
_Q_INT = np.array([500.0, 900.0, 100.0, 700.0, 700.0])
_L_MZ = np.array([100.0, 200.0, 300.0])
_L_INT = np.array([500.0, 900.0, 100.0])


def test_default_weights_reproduce_dot_times_sqrt_coverage():
    m = reverse_dot_product(_Q_MZ, _Q_INT, _L_MZ, _L_INT, ppm_tolerance=10.0)
    assert m.emp_coverage < 1.0 == m.lib_coverage
    assert m.score == pytest.approx(m.dot_product_score * m.coverage_score)


def test_custom_weights_reweight_score_without_touching_diagnostics():
    default = reverse_dot_product(_Q_MZ, _Q_INT, _L_MZ, _L_INT, ppm_tolerance=10.0)
    custom = reverse_dot_product(
        _Q_MZ, _Q_INT, _L_MZ, _L_INT, ppm_tolerance=10.0,
        weight_dot=2.0, weight_lib_coverage=0.25, weight_emp_coverage=0.1,
    )
    # unweighted diagnostics never change with the weights
    assert custom.dot_product_score == pytest.approx(default.dot_product_score)
    assert custom.lib_coverage == pytest.approx(default.lib_coverage)
    assert custom.emp_coverage == pytest.approx(default.emp_coverage)
    assert custom.coverage_score == pytest.approx(default.coverage_score)
    assert custom.score == pytest.approx(
        custom.dot_product_score**2.0
        * custom.lib_coverage**0.25
        * custom.emp_coverage**0.1
    )


def test_zero_weight_drops_emp_coverage_from_score():
    m = reverse_dot_product(
        _Q_MZ, _Q_INT, _L_MZ, _L_INT, ppm_tolerance=10.0,
        weight_dot=1.0, weight_lib_coverage=1.0, weight_emp_coverage=0.0,
    )
    assert m.emp_coverage < 1.0
    assert m.score == pytest.approx(m.dot_product_score * m.lib_coverage)


# ---------------------------------------------------------------------------
# normalize_and_filter_spectrum — public, reused to reconstruct a
# "filtered" view from a stored *raw* spectrum (ADR 0018).
# ---------------------------------------------------------------------------


def test_normalize_and_filter_spectrum_normalises_and_drops_below_threshold():
    mz = np.array([100.0, 150.0, 200.0])
    intensity = np.array([10.0, 100.0, 5.0])  # normalised: [0.1, 1.0, 0.05]

    f_mz, f_int = normalize_and_filter_spectrum(mz, intensity, noise_threshold=0.08)

    # 200.0 (normalised 0.05) drops below the 0.08 threshold; the rest survive.
    np.testing.assert_allclose(f_mz, [100.0, 150.0])
    np.testing.assert_allclose(f_int, [0.1, 1.0])


def test_normalize_and_filter_spectrum_matches_reverse_dot_products_own_filtering():
    # Same code path now (reverse_dot_product calls this function directly),
    # but assert it explicitly: whatever a mirror plot reconstructs via this
    # function for "filtered" must equal what was actually scored.
    m = reverse_dot_product(_Q_MZ, _Q_INT, _L_MZ, _L_INT, ppm_tolerance=10.0)

    f_mz, f_int = normalize_and_filter_spectrum(_Q_MZ, _Q_INT, noise_threshold=0.01)
    np.testing.assert_allclose(f_mz, m.filtered_mz)
    np.testing.assert_allclose(f_int, m.filtered_intensity)

    l_f_mz, l_f_int = normalize_and_filter_spectrum(_L_MZ, _L_INT, noise_threshold=0.01)
    np.testing.assert_allclose(l_f_mz, m.lib_filtered_mz)
    np.testing.assert_allclose(l_f_int, m.lib_filtered_intensity)


def test_normalize_and_filter_spectrum_empty_input():
    mz, intensity = normalize_and_filter_spectrum(
        np.array([]), np.array([]), noise_threshold=0.01
    )
    assert mz.size == 0
    assert intensity.size == 0


def test_normalize_and_filter_spectrum_all_zero_intensity_returns_unfiltered():
    # No positive max to normalise against — matches _filter_noise's own
    # pre-existing "leave it as-is" behaviour for this degenerate case.
    mz = np.array([100.0, 200.0])
    intensity = np.array([0.0, 0.0])

    f_mz, f_int = normalize_and_filter_spectrum(mz, intensity, noise_threshold=0.01)

    np.testing.assert_allclose(f_mz, mz)
    np.testing.assert_allclose(f_int, intensity)
