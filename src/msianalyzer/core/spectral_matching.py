"""
spectral_matching.py
Reverse dot product spectral matching (MSDial-style), with ppm-tolerant
peak alignment.

"Reverse" dot product = how much of the LIBRARY spectrum is explained by
the QUERY (experimental MS2) spectrum. Library peaks with no matching
query peak penalize the score; query peaks with no matching library peak
(noise) do NOT penalize the score. This is what makes it noise-robust.

No I/O, no SQLite, no multiprocessing here — pure numpy on in-memory
arrays. This keeps it trivially unit-testable and reusable from workers.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class MatchResult:
    """
    Result of comparing one query spectrum against one library spectrum.

    Attributes
    ----------
    score : float
        Reverse dot product score, in [0, 1]. 1.0 = perfect match
        (every library peak explained, intensities proportional).
    n_matched_peaks : int
        Number of library peaks that found a query peak within ppm_tolerance.
    n_library_peaks : int
        Total number of peaks in the library spectrum.
    matched_fraction : float
        n_matched_peaks / n_library_peaks — how much of the library
        spectrum was "explained" by the query, independent of intensity
        weighting. Useful as a secondary confidence filter.
    """

    score: float
    n_matched_peaks: int
    n_library_peaks: int
    matched_fraction: float


def _weight(mz: np.ndarray, intensity: np.ndarray, mz_power: float, int_power: float) -> np.ndarray:
    """MSDial-style peak weighting: intensity^a * mz^b."""
    return (intensity ** int_power) * (mz ** mz_power)


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
    If multiple query peaks fall within tolerance, the most intense one
    is used (standard practice — avoids splitting signal across noise).

    Returns
    -------
    aligned_query_intensity : np.ndarray
        Same length as library_mz. 0 where no query peak matched.
    matched_mask : np.ndarray (bool)
        True where a query peak was found for that library peak.
    """
    n_lib = len(library_mz)
    aligned_intensity = np.zeros(n_lib, dtype=np.float64)
    matched_mask = np.zeros(n_lib, dtype=bool)

    if len(query_mz) == 0:
        return aligned_intensity, matched_mask

    # Sort query by mz for efficient searchsorted-based candidate lookup
    order = np.argsort(query_mz)
    q_mz_sorted = query_mz[order]
    q_int_sorted = query_intensity[order]

    for i, lib_mz in enumerate(library_mz):
        tol_da = lib_mz * ppm_tolerance * 1e-6
        lo = np.searchsorted(q_mz_sorted, lib_mz - tol_da, side="left")
        hi = np.searchsorted(q_mz_sorted, lib_mz + tol_da, side="right")
        if lo == hi:
            continue
        candidates_int = q_int_sorted[lo:hi]
        best_idx = np.argmax(candidates_int)
        aligned_intensity[i] = candidates_int[best_idx]
        matched_mask[i] = True

    return aligned_intensity, matched_mask


def reverse_dot_product(
    query_mz: np.ndarray,
    query_intensity: np.ndarray,
    library_mz: np.ndarray,
    library_intensity: np.ndarray,
    ppm_tolerance: float = 10.0,
    mz_power: float = 2.0,
    int_power: float = 0.5,
) -> MatchResult:
    """
    Compute the reverse dot product score between a query MS2 spectrum
    and a library MS2 spectrum.

    Parameters
    ----------
    query_mz, query_intensity : np.ndarray
        Experimental MS2 peaks (the "noisy" spectrum being annotated).
    library_mz, library_intensity : np.ndarray
        Reference library MS2 peaks.
    ppm_tolerance : float
        Tolerance for considering a query peak and a library peak the
        "same" m/z.
    mz_power, int_power : float
        Weighting exponents, MSDial convention: weight = intensity^int_power
        * mz^mz_power. Defaults (mz_power=2, int_power=0.5) match MSDial.

    Returns
    -------
    MatchResult
    """
    if len(library_mz) == 0:
        return MatchResult(score=0.0, n_matched_peaks=0, n_library_peaks=0, matched_fraction=0.0)

    aligned_query_intensity, matched_mask = _align_peaks(
        query_mz, query_intensity, library_mz, library_intensity, ppm_tolerance
    )

    # Weight both sides using the library's m/z (since we've already
    # aligned the query intensity onto the library's m/z axis).
    w_library = _weight(library_mz, library_intensity, mz_power, int_power)
    w_query = _weight(library_mz, aligned_query_intensity, mz_power, int_power)

    denom = np.sqrt(np.sum(w_library ** 2)) * np.sqrt(np.sum(w_query ** 2))
    if denom == 0:
        score = 0.0
    else:
        score = float(np.sum(w_library * w_query) / denom)
        score = max(0.0, min(1.0, score))  # clamp for numerical safety

    n_matched = int(matched_mask.sum())
    n_library = len(library_mz)

    return MatchResult(
        score=score,
        n_matched_peaks=n_matched,
        n_library_peaks=n_library,
        matched_fraction=n_matched / n_library,
    )
