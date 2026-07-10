"""
spectral_matching.py
Reverse dot product spectral matching (MSDial-style), with ppm-tolerant
peak alignment and coverage-aware scoring.

Scoring pipeline
----------------
1. Filter empirical peaks below `noise_threshold` × base-peak intensity
2. Align filtered empirical peaks onto library peaks (fragment ppm tolerance)
3. Compute reverse dot product (weighted by intensity^a × mz^b)
4. Compute lib_coverage  = matched_lib_peaks  / n_lib_peaks
   Compute emp_coverage  = matched_emp_peaks  / n_emp_filtered_peaks
   Compute coverage_score = sqrt(lib_coverage × emp_coverage)
5. final score = dot_product_score × coverage_score  ∈ [0, 1]

All intermediate values are returned in MatchResult for full
interpretability and downstream storage.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class MatchResult:
    """
    Full result of comparing one query spectrum against one library spectrum.

    Attributes
    ----------
    score : float
        Final combined score = dot_product_score × coverage_score. [0, 1]
        This is the primary ranking value.
    dot_product_score : float
        Pure spectral alignment quality (reverse dot product). [0, 1]
        Measures how well peak intensities align, ignoring peak counts.
    lib_coverage : float
        Fraction of library peaks matched in the filtered query. [0, 1]
        = matched_lib_peaks / n_lib_peaks
    emp_coverage : float
        Fraction of filtered empirical peaks that matched a library peak. [0, 1]
        = matched_emp_peaks / n_emp_peaks_filtered
        Low value → many noise/unrelated peaks in the empirical spectrum.
    coverage_score : float
        Geometric mean of lib_coverage and emp_coverage. [0, 1]
        Penalises matches where either side is poorly covered.
    n_matched_peaks : int
        Number of library peaks matched by a filtered empirical peak.
    n_lib_peaks : int
        Total peaks in the library spectrum.
    n_emp_peaks_raw : int
        Empirical peaks before noise filtering.
    n_emp_peaks_filtered : int
        Empirical peaks surviving the noise filter.
    filtered_mz : np.ndarray
        Empirical m/z values after noise filtering (what was actually scored).
    filtered_intensity : np.ndarray
        Empirical intensities after noise filtering, normalised to [0, 1].
    """

    score: float
    dot_product_score: float
    lib_coverage: float
    emp_coverage: float
    coverage_score: float
    n_matched_peaks: int
    n_lib_peaks: int
    n_emp_peaks_raw: int
    n_emp_peaks_filtered: int
    filtered_mz: np.ndarray
    filtered_intensity: np.ndarray


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def reverse_dot_product(
    query_mz: np.ndarray,
    query_intensity: np.ndarray,
    library_mz: np.ndarray,
    library_intensity: np.ndarray,
    ppm_tolerance: float = 10.0,
    mz_power: float = 2.0,
    int_power: float = 0.5,
    noise_threshold: float = 0.01,
) -> MatchResult:
    """
    Compute the coverage-aware reverse dot product score.

    Parameters
    ----------
    query_mz, query_intensity : np.ndarray
        Experimental MS2 peaks (the spectrum being annotated).
    library_mz, library_intensity : np.ndarray
        Reference library MS2 peaks.
    ppm_tolerance : float
        Tolerance for fragment peak alignment.
    mz_power, int_power : float
        MSDial-style peak weighting exponents.
    noise_threshold : float
        Empirical peaks below (noise_threshold × base_peak_intensity) are
        removed before scoring. Default 0.01 = 1 % of base peak.

    Returns
    -------
    MatchResult
    """
    n_emp_raw = len(query_mz)
    n_lib     = len(library_mz)

    # --- zero-library guard ---
    if n_lib == 0:
        empty = np.array([], dtype=np.float32)
        return MatchResult(
            score=0.0, dot_product_score=0.0,
            lib_coverage=0.0, emp_coverage=0.0, coverage_score=0.0,
            n_matched_peaks=0, n_lib_peaks=0,
            n_emp_peaks_raw=n_emp_raw, n_emp_peaks_filtered=0,
            filtered_mz=empty, filtered_intensity=empty,
        )
    
    # --- 1. Normalise empirical spectrum ---
    query_intensity = query_intensity / query_intensity.max() if query_intensity.max() > 0 else query_intensity.copy()
    library_intensity = library_intensity / library_intensity.max() if library_intensity.max() > 0 else library_intensity.copy()

    # --- 2. noise-filter the empirical spectrum ---
    f_mz, f_int = _filter_noise(query_mz, query_intensity, noise_threshold)
    n_emp_filtered = len(f_mz)

    if n_emp_filtered == 0:
        empty = np.array([], dtype=np.float32)
        return MatchResult(
            score=0.0, dot_product_score=0.0,
            lib_coverage=0.0, emp_coverage=0.0, coverage_score=0.0,
            n_matched_peaks=0, n_lib_peaks=n_lib,
            n_emp_peaks_raw=n_emp_raw, n_emp_peaks_filtered=0,
            filtered_mz=empty, filtered_intensity=empty,
        )

    # --- 2. noise-filter the library spectrum ---
    l_f_mz, l_f_int = _filter_noise(library_mz, library_intensity, noise_threshold)
    n_lib_filtered = len(l_f_mz)

    # --- 2. align filtered empirical → library (lib_matched_mask) ---
    aligned_query_int, lib_matched_mask = _align_peaks(
        f_mz, f_int, l_f_mz, l_f_int, ppm_tolerance
    )

    # --- 3. align library → filtered empirical (emp_matched_mask) ---
    _, emp_matched_mask = _align_peaks(
        l_f_mz, l_f_int, f_mz, f_int, ppm_tolerance
    )

    # --- 4. reverse dot product on library's m/z axis ---
    w_lib   = _weight(l_f_mz, l_f_int,   mz_power, int_power)
    w_query = _weight(l_f_mz, aligned_query_int,   mz_power, int_power)

    denom = np.sqrt(np.sum(w_lib ** 2)) * np.sqrt(np.sum(w_query ** 2))
    if denom == 0.0:
        dot_score = 0.0
    else:
        dot_score = float(np.clip(np.sum(w_lib * w_query) / denom, 0.0, 1.0))

    # --- 5. coverage ---
    n_matched    = int(lib_matched_mask.sum())
    n_emp_matched = int(emp_matched_mask.sum())

    lib_coverage = n_matched     / n_lib_filtered          if n_lib_filtered          > 0 else 0.0
    emp_coverage = n_emp_matched / n_emp_filtered if n_emp_filtered > 0 else 0.0
    coverage_score = float(np.sqrt(lib_coverage * emp_coverage))

    score = float(np.clip(dot_score * coverage_score, 0.0, 1.0))

    return MatchResult(
        score=score,
        dot_product_score=dot_score,
        lib_coverage=lib_coverage,
        emp_coverage=emp_coverage,
        coverage_score=coverage_score,
        n_matched_peaks=n_matched,
        n_lib_peaks=n_lib_filtered,
        n_emp_peaks_raw=n_emp_raw,
        n_emp_peaks_filtered=n_emp_filtered,
        filtered_mz=f_mz,
        filtered_intensity=f_int,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _filter_noise(
    mz: np.ndarray,
    intensity: np.ndarray,
    threshold: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Keep only peaks above threshold × base-peak intensity."""
    if len(intensity) == 0:
        return mz.copy(), intensity.copy()
    cutoff = intensity.max() * threshold
    mask = intensity >= cutoff
    return mz[mask], intensity[mask]


def _align_peaks(
    query_mz: np.ndarray,
    query_intensity: np.ndarray,
    library_mz: np.ndarray,
    library_intensity: np.ndarray,
    ppm_tolerance: float,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Align query peaks onto library peaks within ppm_tolerance.

    For each library peak, find the closest query peak within tolerance.
    If multiple query peaks fall within tolerance, the most intense wins.

    Returns
    -------
    aligned_query_intensity : np.ndarray
        Same length as library_mz. 0 where no query peak matched.
    matched_mask : np.ndarray (bool)
        True where a query peak was found for that library peak.
    """
    n_lib = len(library_mz)
    aligned_intensity = np.zeros(n_lib, dtype=np.float64)
    matched_mask      = np.zeros(n_lib, dtype=bool)

    if len(query_mz) == 0:
        return aligned_intensity, matched_mask

    order        = np.argsort(query_mz)
    q_mz_sorted  = query_mz[order]
    q_int_sorted = query_intensity[order]

    for i, lib_mz in enumerate(library_mz):
        tol_da = lib_mz * ppm_tolerance * 1e-6
        lo = np.searchsorted(q_mz_sorted, lib_mz - tol_da, side="left")
        hi = np.searchsorted(q_mz_sorted, lib_mz + tol_da, side="right")
        if lo == hi:
            continue
        best = int(np.argmax(q_int_sorted[lo:hi])) + lo
        aligned_intensity[i] = q_int_sorted[best]
        matched_mask[i]      = True

    return aligned_intensity, matched_mask


def _weight(
    mz: np.ndarray,
    intensity: np.ndarray,
    mz_power: float,
    int_power: float,
) -> np.ndarray:
    """MSDial-style peak weighting: intensity^a × mz^b."""
    return (intensity ** int_power) * (mz ** mz_power)
