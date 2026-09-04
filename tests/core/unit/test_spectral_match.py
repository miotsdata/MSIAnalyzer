"""Tests for the ported reverse-dot-product matcher.

`MatchResult` now carries the noise-filtered spectra of *both* sides so the
annotator can persist them for mirror plots.
"""

import numpy as np
import pytest

from msianalyzer.core.annotation.spectral_match import (
    MatchResult,
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
