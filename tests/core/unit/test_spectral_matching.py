"""test_spectral_matching.py"""
from __future__ import annotations
import numpy as np
import pytest
from msianalyzer.core.spectral_matching import (
    reverse_dot_product, _filter_noise, _align_peaks, _weight, MatchResult
)


class TestFilterNoise:
    def test_removes_peaks_below_threshold(self):
        mz  = np.array([100.0, 200.0, 300.0])
        ins = np.array([1000.0, 5.0, 50.0])   # base=1000, 1%=10 → keep 100 & 300
        f_mz, f_int = _filter_noise(mz, ins, threshold=0.01)
        np.testing.assert_array_equal(f_mz,  [100.0, 300.0])
        np.testing.assert_array_equal(f_int, [1000.0, 50.0])

    def test_keeps_all_above_threshold(self):
        mz  = np.array([100.0, 200.0])
        ins = np.array([1000.0, 500.0])
        f_mz, f_int = _filter_noise(mz, ins, threshold=0.01)
        assert len(f_mz) == 2

    def test_empty_input(self):
        f_mz, f_int = _filter_noise(np.array([]), np.array([]), 0.01)
        assert len(f_mz) == 0

    def test_threshold_zero_keeps_all(self):
        mz  = np.array([100.0, 200.0, 300.0])
        ins = np.array([1000.0, 1.0, 0.001])
        f_mz, _ = _filter_noise(mz, ins, threshold=0.0)
        assert len(f_mz) == 3

    def test_high_threshold_keeps_only_base(self):
        mz  = np.array([100.0, 200.0, 300.0])
        ins = np.array([1000.0, 500.0, 200.0])
        f_mz, _ = _filter_noise(mz, ins, threshold=0.99)
        assert len(f_mz) == 1
        assert f_mz[0] == 100.0


class TestAlignPeaks:
    def test_exact_match(self):
        q_mz  = np.array([100.0, 150.0])
        q_int = np.array([500.0, 800.0])
        l_mz  = np.array([100.0, 150.0])
        l_int = np.array([1000.0, 1000.0])
        aligned, mask = _align_peaks(q_mz, q_int, l_mz, l_int, ppm_tolerance=10.0)
        np.testing.assert_allclose(aligned, [500.0, 800.0])
        assert mask.tolist() == [True, True]

    def test_outside_tolerance_no_match(self):
        aligned, mask = _align_peaks(
            np.array([100.5]), np.array([500.0]),
            np.array([100.0]), np.array([1000.0]),
            ppm_tolerance=10.0,
        )
        assert aligned[0] == 0.0
        assert mask[0] is np.bool_(False)

    def test_most_intense_candidate_wins(self):
        q_mz  = np.array([99.9999, 100.0001])
        q_int = np.array([100.0, 900.0])
        aligned, _ = _align_peaks(q_mz, q_int, np.array([100.0]), np.array([1.0]), 50.0)
        assert aligned[0] == 900.0

    def test_empty_query_returns_zeros(self):
        aligned, mask = _align_peaks(
            np.array([]), np.array([]),
            np.array([100.0, 200.0]), np.array([1.0, 1.0]),
            ppm_tolerance=10.0,
        )
        assert (aligned == 0).all()
        assert (~mask).all()


class TestReverseDotProduct:
    def test_identical_spectra_perfect_score(self):
        mz  = np.array([100.0, 150.0, 200.0])
        ins = np.array([1000.0, 500.0, 800.0])
        r = reverse_dot_product(mz, ins, mz, ins, ppm_tolerance=10.0)
        assert r.score          == pytest.approx(1.0, abs=1e-6)
        assert r.dot_product_score == pytest.approx(1.0, abs=1e-6)
        assert r.lib_coverage   == pytest.approx(1.0)
        assert r.emp_coverage   == pytest.approx(1.0)
        assert r.coverage_score == pytest.approx(1.0)

    def test_empty_library_zero_score(self):
        r = reverse_dot_product(
            np.array([100.0]), np.array([1000.0]),
            np.array([]), np.array([]),
        )
        assert r.score == 0.0
        assert r.n_lib_peaks == 0

    def test_all_empirical_below_noise_zero_score(self):
        # base peak = 1000, noise peak = 5 → cutoff = 10 → noise filtered out
        # library only matches the noise peak (200.0) → 0 matched after filtering
        r = reverse_dot_product(
            np.array([100.0, 200.0]), np.array([1000.0, 5.0]),
            np.array([200.0]),        np.array([1000.0]),
            ppm_tolerance=10.0, noise_threshold=0.01,
        )
        assert r.n_emp_peaks_filtered == 1   # only 100.0 survives (5.0 < 1% of 1000)
        assert r.n_matched_peaks == 0        # library peak 200.0 not in filtered emp
        assert r.score == 0.0


    def test_noise_filter_counts(self):
        q_mz  = np.array([100.0, 200.0, 300.0])
        q_int = np.array([1000.0, 5.0, 500.0])  # 5.0 < 1% of 1000 → removed
        r = reverse_dot_product(
            q_mz, q_int,
            np.array([100.0]), np.array([1000.0]),
            ppm_tolerance=10.0, noise_threshold=0.01,
        )
        assert r.n_emp_peaks_raw      == 3
        assert r.n_emp_peaks_filtered == 2

    def test_filtered_spectrum_stored(self):
        q_mz  = np.array([100.0, 200.0, 300.0])
        q_int = np.array([1000.0, 5.0, 500.0])
        r = reverse_dot_product(q_mz, q_int, np.array([100.0]), np.array([1000.0]),
                                ppm_tolerance=10.0, noise_threshold=0.01)
        assert len(r.filtered_mz) == 2
        assert len(r.filtered_intensity) == 2
        assert r.filtered_mz[0] == pytest.approx(100.0, rel=1e-4)

    def test_filtered_intensity_normalised(self):
        q_mz  = np.array([100.0, 200.0])
        q_int = np.array([500.0, 1000.0])
        r = reverse_dot_product(q_mz, q_int, np.array([100.0]), np.array([1000.0]),
                                ppm_tolerance=10.0, noise_threshold=0.0)
        assert r.filtered_intensity.max() == pytest.approx(1.0, abs=1e-6)

    def test_noisy_query_penalized_by_emp_coverage(self):
        """Extra unmatched empirical peaks reduce emp_coverage and therefore score."""
        lib_mz  = np.array([100.0, 150.0])
        lib_int = np.array([1000.0, 1000.0])

        clean_mz  = np.array([100.0, 150.0])
        clean_int = np.array([1000.0, 1000.0])

        noisy_mz  = np.array([100.0, 150.0, 300.0, 400.0, 500.0])
        noisy_int = np.array([1000.0, 1000.0, 800.0, 800.0, 800.0])

        clean = reverse_dot_product(clean_mz, clean_int, lib_mz, lib_int,
                                    ppm_tolerance=10.0, noise_threshold=0.0)
        noisy = reverse_dot_product(noisy_mz, noisy_int, lib_mz, lib_int,
                                    ppm_tolerance=10.0, noise_threshold=0.0)
        assert noisy.score < clean.score
        assert noisy.emp_coverage < clean.emp_coverage

    def test_missing_library_peak_reduces_lib_coverage(self):
        lib_mz  = np.array([100.0, 150.0, 200.0])
        lib_int = np.array([1000.0, 1000.0, 1000.0])

        full_mz  = np.array([100.0, 150.0, 200.0])
        full_int = np.array([1000.0, 1000.0, 1000.0])

        partial_mz  = np.array([100.0, 150.0])  # missing 200.0
        partial_int = np.array([1000.0, 1000.0])

        full    = reverse_dot_product(full_mz,    full_int,    lib_mz, lib_int, 10.0)
        partial = reverse_dot_product(partial_mz, partial_int, lib_mz, lib_int, 10.0)
        assert partial.lib_coverage < full.lib_coverage
        assert partial.score < full.score

    def test_single_peak_library_penalized(self):
        """A 1-peak library matching 1 of many empirical peaks should score < 1."""
        lib_mz  = np.array([100.0])
        lib_int = np.array([1000.0])
        q_mz    = np.array([100.0, 150.0, 200.0, 250.0, 300.0])
        q_int   = np.array([1000.0, 800.0, 700.0, 600.0, 500.0])

        r = reverse_dot_product(q_mz, q_int, lib_mz, lib_int,
                                ppm_tolerance=10.0, noise_threshold=0.0)
        assert r.dot_product_score == pytest.approx(1.0, abs=1e-6)  # dp is still 1
        assert r.emp_coverage < 1.0                                  # but coverage penalizes
        assert r.score < 1.0                                         # so final score < 1

    def test_score_bounded_zero_one(self):
        rng = np.random.default_rng(42)
        for _ in range(30):
            n_lib   = rng.integers(1, 10)
            n_query = rng.integers(1, 15)
            l_mz  = np.sort(rng.uniform(50, 500, n_lib))
            l_int = rng.uniform(100, 1000, n_lib)
            q_mz  = np.sort(rng.uniform(50, 500, n_query))
            q_int = rng.uniform(1, 1000, n_query)
            r = reverse_dot_product(q_mz, q_int, l_mz, l_int, ppm_tolerance=10.0)
            assert 0.0 <= r.score <= 1.0, f"score={r.score} out of bounds"

    def test_result_type(self):
        r = reverse_dot_product(
            np.array([100.0]), np.array([1000.0]),
            np.array([100.0]), np.array([1000.0]),
        )
        assert isinstance(r, MatchResult)

    def test_coverage_score_geometric_mean(self):
        r = reverse_dot_product(
            np.array([100.0]), np.array([1000.0]),
            np.array([100.0]), np.array([1000.0]),
            noise_threshold=0.0,
        )
        expected_cov = float(np.sqrt(r.lib_coverage * r.emp_coverage))
        assert r.coverage_score == pytest.approx(expected_cov, abs=1e-6)
