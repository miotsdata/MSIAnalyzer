"""test_spectral_matching.py"""

from __future__ import annotations

import numpy as np
import pytest

from msianalyzer.core.spectral_matching import reverse_dot_product, _align_peaks, _weight, MatchResult


class TestAlignPeaks:
    def test_exact_match(self):
        query_mz = np.array([100.0, 150.0])
        query_int = np.array([500.0, 800.0])
        lib_mz = np.array([100.0, 150.0])
        lib_int = np.array([1000.0, 1000.0])

        aligned, mask = _align_peaks(query_mz, query_int, lib_mz, lib_int, ppm_tolerance=10.0)
        np.testing.assert_allclose(aligned, [500.0, 800.0])
        assert mask.tolist() == [True, True]

    def test_no_match_outside_tolerance(self):
        query_mz = np.array([100.5])
        query_int = np.array([500.0])
        lib_mz = np.array([100.0])
        lib_int = np.array([1000.0])

        aligned, mask = _align_peaks(query_mz, query_int, lib_mz, lib_int, ppm_tolerance=10.0)
        assert aligned[0] == 0.0
        assert mask[0] == False

    def test_within_ppm_tolerance_matches(self):
        # 100.0005 vs 100.0 = 5 ppm
        query_mz = np.array([100.0005])
        query_int = np.array([500.0])
        lib_mz = np.array([100.0])
        lib_int = np.array([1000.0])

        aligned, mask = _align_peaks(query_mz, query_int, lib_mz, lib_int, ppm_tolerance=10.0)
        assert mask[0] == True
        assert aligned[0] == 500.0

    def test_empty_query_returns_zeros(self):
        lib_mz = np.array([100.0, 200.0])
        lib_int = np.array([500.0, 600.0])
        aligned, mask = _align_peaks(
            np.array([]), np.array([]), lib_mz, lib_int, ppm_tolerance=10.0
        )
        assert (aligned == 0).all()
        assert (~mask).all()

    def test_multiple_candidates_picks_most_intense(self):
        # Two query peaks both within tolerance of one library peak
        query_mz = np.array([99.9999, 100.0001])
        query_int = np.array([100.0, 900.0])
        lib_mz = np.array([100.0])
        lib_int = np.array([1000.0])

        aligned, mask = _align_peaks(query_mz, query_int, lib_mz, lib_int, ppm_tolerance=50.0)
        assert aligned[0] == 900.0

    def test_unmatched_library_peaks_stay_zero(self):
        query_mz = np.array([100.0])
        query_int = np.array([500.0])
        lib_mz = np.array([100.0, 999.0])
        lib_int = np.array([1000.0, 1000.0])

        aligned, mask = _align_peaks(query_mz, query_int, lib_mz, lib_int, ppm_tolerance=10.0)
        assert aligned[0] == 500.0
        assert aligned[1] == 0.0
        assert mask.tolist() == [True, False]


class TestWeight:
    def test_basic_weighting(self):
        mz = np.array([100.0])
        intensity = np.array([4.0])
        w = _weight(mz, intensity, mz_power=2.0, int_power=0.5)
        # 4^0.5 * 100^2 = 2 * 10000 = 20000
        assert w[0] == pytest.approx(20000.0)

    def test_zero_intensity_zero_weight(self):
        mz = np.array([100.0])
        intensity = np.array([0.0])
        w = _weight(mz, intensity, mz_power=2.0, int_power=0.5)
        assert w[0] == 0.0


class TestReverseDotProduct:
    def test_identical_spectra_perfect_score(self):
        mz = np.array([100.0, 150.0, 200.0])
        intensity = np.array([1000.0, 500.0, 800.0])

        result = reverse_dot_product(
            query_mz=mz, query_intensity=intensity,
            library_mz=mz, library_intensity=intensity,
            ppm_tolerance=10.0,
        )
        assert result.score == pytest.approx(1.0, abs=1e-6)
        assert result.n_matched_peaks == 3
        assert result.n_library_peaks == 3
        assert result.matched_fraction == pytest.approx(1.0)

    def test_completely_different_spectra_low_score(self):
        result = reverse_dot_product(
            query_mz=np.array([100.0]), query_intensity=np.array([1000.0]),
            library_mz=np.array([500.0]), library_intensity=np.array([1000.0]),
            ppm_tolerance=10.0,
        )
        assert result.score == 0.0
        assert result.n_matched_peaks == 0

    def test_empty_library_returns_zero(self):
        result = reverse_dot_product(
            query_mz=np.array([100.0]), query_intensity=np.array([1000.0]),
            library_mz=np.array([]), library_intensity=np.array([]),
            ppm_tolerance=10.0,
        )
        assert result.score == 0.0
        assert result.n_library_peaks == 0

    def test_noisy_query_does_not_penalize_score(self):
        """Core requirement: extra unmatched QUERY peaks (noise) should
        not reduce the score, since reverse dot product only considers
        how well LIBRARY peaks are explained."""
        lib_mz = np.array([100.0, 150.0])
        lib_int = np.array([1000.0, 1000.0])

        clean_query_mz = np.array([100.0, 150.0])
        clean_query_int = np.array([1000.0, 1000.0])

        noisy_query_mz = np.array([100.0, 150.0, 300.0, 400.0, 500.0])
        noisy_query_int = np.array([1000.0, 1000.0, 50.0, 75.0, 30.0])

        clean_result = reverse_dot_product(
            clean_query_mz, clean_query_int, lib_mz, lib_int, ppm_tolerance=10.0
        )
        noisy_result = reverse_dot_product(
            noisy_query_mz, noisy_query_int, lib_mz, lib_int, ppm_tolerance=10.0
        )
        assert noisy_result.score == pytest.approx(clean_result.score, abs=1e-6)

    def test_missing_library_peak_reduces_score(self):
        """Unlike noise in the query, a library peak NOT found in the
        query should reduce the score (this is what 'reverse' captures)."""
        lib_mz = np.array([100.0, 150.0, 200.0])
        lib_int = np.array([1000.0, 1000.0, 1000.0])

        full_query_mz = np.array([100.0, 150.0, 200.0])
        full_query_int = np.array([1000.0, 1000.0, 1000.0])

        partial_query_mz = np.array([100.0, 150.0])  # missing 200.0
        partial_query_int = np.array([1000.0, 1000.0])

        full_result = reverse_dot_product(
            full_query_mz, full_query_int, lib_mz, lib_int, ppm_tolerance=10.0
        )
        partial_result = reverse_dot_product(
            partial_query_mz, partial_query_int, lib_mz, lib_int, ppm_tolerance=10.0
        )
        assert partial_result.score < full_result.score
        assert partial_result.matched_fraction == pytest.approx(2 / 3)

    def test_score_bounded_zero_one(self):
        rng = np.random.default_rng(42)
        for _ in range(20):
            n_lib = rng.integers(1, 10)
            n_query = rng.integers(1, 10)
            lib_mz = np.sort(rng.uniform(50, 500, n_lib))
            lib_int = rng.uniform(1, 1000, n_lib)
            query_mz = np.sort(rng.uniform(50, 500, n_query))
            query_int = rng.uniform(1, 1000, n_query)

            result = reverse_dot_product(
                query_mz, query_int, lib_mz, lib_int, ppm_tolerance=10.0
            )
            assert 0.0 <= result.score <= 1.0

    def test_empty_query_zero_score(self):
        result = reverse_dot_product(
            query_mz=np.array([]), query_intensity=np.array([]),
            library_mz=np.array([100.0]), library_intensity=np.array([1000.0]),
            ppm_tolerance=10.0,
        )
        assert result.score == 0.0
        assert result.n_matched_peaks == 0

    def test_custom_weighting_exponents(self):
        mz = np.array([100.0])
        intensity = np.array([1000.0])
        # With mz_power=0, int_power=1, weighting degenerates to pure intensity dot product
        result = reverse_dot_product(
            mz, intensity, mz, intensity,
            ppm_tolerance=10.0, mz_power=0.0, int_power=1.0,
        )
        assert result.score == pytest.approx(1.0, abs=1e-6)

    def test_result_is_match_result_instance(self):
        result = reverse_dot_product(
            np.array([100.0]), np.array([1000.0]),
            np.array([100.0]), np.array([1000.0]),
            ppm_tolerance=10.0,
        )
        assert isinstance(result, MatchResult)
