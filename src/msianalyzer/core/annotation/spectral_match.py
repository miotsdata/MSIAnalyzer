"""Reverse dot product spectral matching (MSDial-style).

Ppm-tolerant fragment alignment plus coverage-aware scoring. Ported from
the pre-split ``core.spectral_matching`` module; the only addition is that
:class:`MatchResult` now also carries the noise-filtered library spectrum
(``lib_filtered_mz`` / ``lib_filtered_intensity``) so the annotator can
persist both sides of a match for later mirror plots.

Scoring pipeline:
    1. Max-normalise both spectra to 1.
    2. Drop peaks below ``noise_threshold`` (a fraction in ``[0, 1]``) on
       *both* the empirical and the library spectrum.
    3. Align filtered empirical peaks onto library peaks (fragment ppm
       tolerance) and vice versa.
    4. Reverse dot product on the library m/z axis, weighted by
       ``intensity ** int_power * mz ** mz_power``.
    5. ``lib_coverage``  = matched_lib_peaks / n_lib_filtered
       ``emp_coverage``  = matched_emp_peaks / n_emp_filtered
       ``coverage_score`` = sqrt(lib_coverage * emp_coverage)
    6. ``score`` = ``dot_product_score**w_dot * lib_coverage**w_lib *
       emp_coverage**w_emp``, clipped to ``[0, 1]``. The weights are
       exponents (default ``1, 0.5, 0.5``, reproducing the original
       ``dot_product_score * sqrt(lib_coverage * emp_coverage)``), not a
       normalised split — raise one to lean on that term harder, or drop it
       to ``0`` to remove it from the score entirely (``x**0 == 1``). A
       matrix-heavy empirical spectrum with real background peaks the
       library doesn't carry will always have a depressed ``emp_coverage``;
       lowering ``w_emp`` stops that alone from crushing an otherwise good
       ``dot_product_score`` / ``lib_coverage`` match.

Every intermediate value is returned in :class:`MatchResult` for full
interpretability and downstream storage — the weights only shape the final
``score``; ``dot_product_score``, ``lib_coverage``, ``emp_coverage`` and
``coverage_score`` are always the unweighted, directly-comparable-across-runs
diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = ["MatchResult", "reverse_dot_product", "normalize_and_filter_spectrum"]


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class MatchResult:
    """Full result of comparing one query spectrum against one library spectrum.

    Attributes:
        score: Final combined score = ``dot_product_score * coverage_score``,
            in ``[0, 1]``. The primary ranking value.
        dot_product_score: Pure spectral alignment quality (reverse dot
            product), in ``[0, 1]``.
        lib_coverage: Fraction of filtered library peaks matched by a
            filtered empirical peak, in ``[0, 1]``.
        emp_coverage: Fraction of filtered empirical peaks that matched a
            library peak, in ``[0, 1]``. Low => many unrelated peaks in the
            empirical spectrum.
        coverage_score: Geometric mean of ``lib_coverage`` and
            ``emp_coverage``.
        n_matched_peaks: Number of filtered library peaks matched.
        n_lib_peaks: Filtered library peak count (peaks surviving the noise
            filter).
        n_emp_peaks_raw: Empirical peaks before noise filtering.
        n_emp_peaks_filtered: Empirical peaks surviving the noise filter.
        filtered_mz: Empirical m/z after noise filtering (what was scored).
        filtered_intensity: Empirical intensities after noise filtering,
            max-normalised to ``[0, 1]``.
        lib_filtered_mz: Library m/z after noise filtering.
        lib_filtered_intensity: Library intensities after noise filtering,
            max-normalised to ``[0, 1]``.
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
    lib_filtered_mz: np.ndarray
    lib_filtered_intensity: np.ndarray


def _empty_result(*, n_emp_raw: int, n_lib: int, n_emp_filtered: int = 0) -> MatchResult:
    """A zero-score :class:`MatchResult` with empty filtered spectra."""
    empty = np.array([], dtype=np.float32)
    return MatchResult(
        score=0.0,
        dot_product_score=0.0,
        lib_coverage=0.0,
        emp_coverage=0.0,
        coverage_score=0.0,
        n_matched_peaks=0,
        n_lib_peaks=n_lib,
        n_emp_peaks_raw=n_emp_raw,
        n_emp_peaks_filtered=n_emp_filtered,
        filtered_mz=empty,
        filtered_intensity=empty,
        lib_filtered_mz=empty,
        lib_filtered_intensity=empty,
    )


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
    weight_dot: float = 1.0,
    weight_lib_coverage: float = 0.5,
    weight_emp_coverage: float = 0.5,
) -> MatchResult:
    """Compute the coverage-aware reverse dot product score.

    Args:
        query_mz: Experimental MS2 peak m/z values (the spectrum being
            annotated).
        query_intensity: Experimental MS2 peak intensities.
        library_mz: Reference library MS2 peak m/z values.
        library_intensity: Reference library MS2 peak intensities.
        ppm_tolerance: Tolerance for fragment peak alignment.
        mz_power: MSDial-style m/z weighting exponent.
        int_power: MSDial-style intensity weighting exponent.
        noise_threshold: After max-normalisation, peaks with intensity below
            this fraction are dropped from both spectra. Default 0.01 = 1 %
            of the base peak.
        weight_dot: Exponent applied to ``dot_product_score`` in the final
            ``score``. Default ``1.0``.
        weight_lib_coverage: Exponent applied to ``lib_coverage``. Default
            ``0.5``.
        weight_emp_coverage: Exponent applied to ``emp_coverage``. Default
            ``0.5``. Lower this to stop a spectrum's own unmatched
            background/matrix peaks (low ``emp_coverage``) from dominating
            the score.

    Returns:
        A :class:`MatchResult`.
    """
    query_mz = np.asarray(query_mz, dtype=np.float64)
    query_intensity = np.asarray(query_intensity, dtype=np.float64)
    library_mz = np.asarray(library_mz, dtype=np.float64)
    library_intensity = np.asarray(library_intensity, dtype=np.float64)

    n_emp_raw = len(query_mz)
    n_lib = len(library_mz)

    if n_lib == 0:
        return _empty_result(n_emp_raw=n_emp_raw, n_lib=0)

    # --- 1 & 2. max-normalise, then noise-filter, each spectrum ---
    f_mz, f_int = normalize_and_filter_spectrum(query_mz, query_intensity, noise_threshold)
    n_emp_filtered = len(f_mz)

    l_f_mz, l_f_int = normalize_and_filter_spectrum(
        library_mz, library_intensity, noise_threshold
    )
    n_lib_filtered = len(l_f_mz)

    if n_emp_filtered == 0 or n_lib_filtered == 0:
        res = _empty_result(
            n_emp_raw=n_emp_raw, n_lib=n_lib_filtered, n_emp_filtered=n_emp_filtered
        )
        res.filtered_mz = f_mz
        res.filtered_intensity = f_int
        res.lib_filtered_mz = l_f_mz
        res.lib_filtered_intensity = l_f_int
        return res

    # --- 3. align filtered empirical -> library (lib_matched_mask) ---
    aligned_query_int, lib_matched_mask = _align_peaks(
        f_mz, f_int, l_f_mz, l_f_int, ppm_tolerance
    )

    # --- 3. align library -> filtered empirical (emp_matched_mask) ---
    _, emp_matched_mask = _align_peaks(l_f_mz, l_f_int, f_mz, f_int, ppm_tolerance)

    # --- 4. reverse dot product on the library m/z axis ---
    w_lib = _weight(l_f_mz, l_f_int, mz_power, int_power)
    w_query = _weight(l_f_mz, aligned_query_int, mz_power, int_power)

    denom = np.sqrt(np.sum(w_lib**2)) * np.sqrt(np.sum(w_query**2))
    dot_score = (
        0.0
        if denom == 0.0
        else float(np.clip(np.sum(w_lib * w_query) / denom, 0.0, 1.0))
    )

    # --- 5. coverage ---
    n_matched = int(lib_matched_mask.sum())
    n_emp_matched = int(emp_matched_mask.sum())

    lib_coverage = n_matched / n_lib_filtered if n_lib_filtered > 0 else 0.0
    emp_coverage = n_emp_matched / n_emp_filtered if n_emp_filtered > 0 else 0.0
    coverage_score = float(np.sqrt(lib_coverage * emp_coverage))

    weighted = (
        dot_score**weight_dot
        * lib_coverage**weight_lib_coverage
        * emp_coverage**weight_emp_coverage
    )
    score = float(np.clip(weighted, 0.0, 1.0))

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
        lib_filtered_mz=l_f_mz,
        lib_filtered_intensity=l_f_int,
    )


# ---------------------------------------------------------------------------
# Public: shared with anything that needs to reconstruct a filtered
# spectrum from a stored *raw* one (see ADR 0018) — not just an internal
# scoring step.
# ---------------------------------------------------------------------------


def normalize_and_filter_spectrum(
    mz: np.ndarray,
    intensity: np.ndarray,
    noise_threshold: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Max-normalise, then drop peaks below ``noise_threshold`` — the exact
    per-spectrum preprocessing `reverse_dot_product` scores against.

    A pure, deterministic function of `(mz, intensity, noise_threshold)`
    alone: no reordering, merging, or interpolation, only normalising the
    intensity axis and then a positional boolean subset. That's what makes
    it safe to reconstruct the "filtered" view of a spectrum on demand
    from a stored *raw* copy plus the run's own `noise_threshold` (see
    `Plotter.get_annotation_spectra`) instead of persisting the filtered
    arrays themselves — ``ms2_annotations`` no longer does (ADR 0018).

    Args:
        mz: Peak m/z values.
        intensity: Peak intensities, parallel to `mz`. Not required to
            already be normalised — normalisation happens here.
        noise_threshold: Fraction in `[0, 1]` of the (post-normalisation)
            base-peak intensity a peak must reach to survive.

    Returns:
        `(filtered_mz, filtered_intensity)` — `filtered_intensity` is on
        the max-normalised [0, 1] scale.
    """
    mz = np.asarray(mz, dtype=np.float64)
    intensity = np.asarray(intensity, dtype=np.float64)
    normalized = (
        intensity / intensity.max()
        if intensity.size and intensity.max() > 0
        else intensity.copy()
    )
    return _filter_noise(mz, normalized, noise_threshold)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _filter_noise(
    mz: np.ndarray,
    intensity: np.ndarray,
    threshold: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Keep only peaks at or above ``threshold * base-peak intensity`` of
    the (already max-normalised, by the caller) `intensity` array."""
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
    """Align query peaks onto library peaks within ``ppm_tolerance``.

    For each library peak, find the closest query peak within tolerance; if
    several fall inside, the most intense wins.

    Returns:
        aligned_query_intensity: Same length as ``library_mz``; 0 where no
            query peak matched.
        matched_mask: Boolean array, True where a query peak was found for
            that library peak.
    """
    n_lib = len(library_mz)
    aligned_intensity = np.zeros(n_lib, dtype=np.float64)
    matched_mask = np.zeros(n_lib, dtype=bool)

    if len(query_mz) == 0:
        return aligned_intensity, matched_mask

    order = np.argsort(query_mz)
    q_mz_sorted = query_mz[order]
    q_int_sorted = query_intensity[order]

    for i, lib_mz in enumerate(library_mz):
        tol_da = lib_mz * ppm_tolerance * 1e-6
        lo = np.searchsorted(q_mz_sorted, lib_mz - tol_da, side="left")
        hi = np.searchsorted(q_mz_sorted, lib_mz + tol_da, side="right")
        if lo == hi:
            continue
        best = int(np.argmax(q_int_sorted[lo:hi])) + lo
        aligned_intensity[i] = q_int_sorted[best]
        matched_mask[i] = True

    return aligned_intensity, matched_mask


def _weight(
    mz: np.ndarray,
    intensity: np.ndarray,
    mz_power: float,
    int_power: float,
) -> np.ndarray:
    """MSDial-style peak weighting: ``intensity ** int_power * mz ** mz_power``."""
    return (intensity**int_power) * (mz**mz_power)
