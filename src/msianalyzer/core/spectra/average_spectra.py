from ast import Tuple
import sqlite3
from uuid import UUID
import numpy as np
from scipy.signal import find_peaks
from pathlib import Path
from typing import Literal

import pandas as pd
from scipy.ndimage import gaussian_filter1d
from scipy.spatial import cKDTree
from msianalyzer.core.parser import array_to_blob, blob_to_array


def get_average_ms1_spectra(
    db_path: str | Path,
    chunk_size: int = 2000,
    bin_width: float = 0.0001,
    min_mz: float = 70.0,
    max_mz: float = 900.0,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Streams MS1 spectra corresponding only to valid spatial pixels in chunks,
    accumulating binned intensities and computing the mean spectrum across pixels.
    """
    num_bins = int(np.ceil((max_mz - min_mz) / bin_width))
    summed_intensities = np.zeros(num_bins, dtype=np.float64)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 1. Get total number of distinct pixels that have mapped MS1 scans
    cursor.execute("SELECT COUNT(DISTINCT pixel_id) FROM pixel_ms1_scans;")
    n_pixels = cursor.fetchone()[0]

    if n_pixels == 0:
        conn.close()
        raise ValueError("No mapped MS1 scans found in 'pixel_ms1_scans'.")

    # 2. Stream MS1 arrays joined with pixel_ms1_scans
    # DISTINCT scan_id ensures we don't process duplicate scans if a scan maps to >1 pixel
    query = """
        SELECT DISTINCT s.mz_array, s.intensity_array
        FROM ms1_scans s
        INNER JOIN pixel_ms1_scans p ON s.scan_id = p.scan_id
    """
    cursor.execute(query)

    while True:
        rows = cursor.fetchmany(chunk_size)
        if not rows:
            break

        # Deserialize BLOBs for the current chunk
        mz_chunk = [blob_to_array(r[0]) for r in rows]
        int_chunk = [blob_to_array(r[1]) for r in rows]

        # Concatenate chunk arrays
        flat_mz = np.concatenate(mz_chunk)
        flat_int = np.concatenate(int_chunk)

        # Convert m/z to bin indices
        bin_indices = ((flat_mz - min_mz) / bin_width).astype(np.int64)

        # Filter indices within valid range
        mask = (bin_indices >= 0) & (bin_indices < num_bins)

        # Accumulate weighted intensities directly into master bin array
        summed_intensities += np.bincount(
            bin_indices[mask], weights=flat_int[mask], minlength=num_bins
        )

    conn.close()

    # Calculate average across total pixels
    mean_intensities = summed_intensities / n_pixels
    bin_centers = min_mz + (np.arange(num_bins) + 0.5) * bin_width

    return bin_centers, mean_intensities


def save_aggregated_spectra(
    mzs_array: np.ndarray,
    intensities_array: np.ndarray,
    *,
    ms1_db_path: Path | str,
    project_id: str,
    command_id: int,
) -> None:
    mzs_blob = array_to_blob(mzs_array)
    intensities_blob = array_to_blob(intensities_array)
    with sqlite3.connect(Path(ms1_db_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS aggregated_spectra (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id UUID NOT NULL,
            command_id INT,
            mz_array BLOB,
            intensity_array BLOB,
            CONSTRAINT fk_command_id
            FOREIGN KEY (command_id)
            REFERENCES command(id)
            );
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_averagems1_project_id ON aggregated_spectra(project_id)"
        )
        cursor.execute(
            """
            INSERT OR REPLACE INTO aggregated_spectra (project_id, command_id, mz_array, intensity_array)
            VALUES (?, ?, ?, ?);
            """,
            (project_id, command_id, mzs_blob, intensities_blob),
        )
        conn.commit()


def _infer_decimal_places(bin_centers: np.ndarray) -> int:
    """
    Infer the decimal precision of the input m/z values from the bin spacing.
    """
    if len(bin_centers) < 2:
        return 4

    spacing = np.median(np.diff(bin_centers))
    text = f"{spacing:.10f}".rstrip("0")

    if "." not in text:
        return 0

    return len(text.split(".")[1])


def _estimate_baseline(
    intensities: np.ndarray,
    method: Literal["global", "local"] = "global",
    percentile: float = 10.0,
    local_window: int = 501,
    smooth_sigma: float = 10.0,
) -> np.ndarray:
    """
    Estimate the spectral baseline.

    Parameters
    ----------
    method
        "global": single baseline for the whole spectrum.
        "local": rolling percentile followed by Gaussian smoothing.
    percentile
        Percentile of non-zero intensities used as baseline.
    local_window
        Rolling window (bins) for local baseline estimation.
    smooth_sigma
        Gaussian smoothing sigma (bins) applied to the local baseline.
    """

    positive = intensities[intensities > 0]

    if len(positive) == 0:
        if method == "global":
            return np.array(0.0)
        return np.zeros_like(intensities)

    global_baseline = np.percentile(positive, percentile)

    if method == "global":
        return np.array(global_baseline)

    # Ignore empty bins
    s = pd.Series(intensities).mask(lambda x: x == 0)

    baseline = (
        s.rolling(
            window=local_window,
            center=True,
            min_periods=max(5, local_window // 10),
        )
        .quantile(percentile / 100)
        .to_numpy()
    )

    # Fill windows with insufficient data
    baseline = np.nan_to_num(baseline, nan=global_baseline)

    # Smooth slowly varying background
    baseline = gaussian_filter1d(baseline, sigma=smooth_sigma)

    return baseline


def _merge_peaks_ppm(mz, intensity, ppm: float = 5):

    order = np.argsort(intensity)[::-1]

    mz_ordered = mz[order]

    tree = cKDTree(mz_ordered[:, None])

    removed = np.zeros(len(mz), dtype=bool)

    selected = []

    for idx in range(len(mz_ordered)):
        if removed[idx]:
            continue

        selected.append(order[idx])

        mz0 = mz_ordered[idx]
        tol = mz0 * ppm * 1e-6

        neighbours = tree.query_ball_point([[mz0]], r=tol)[0]

        removed[neighbours] = True

    selected = np.array(selected)
    selected = selected[np.argsort(mz[selected])]

    return mz[selected], intensity[selected]


def detect_ms1_centroids(
    bin_centers: np.ndarray,
    mean_intensities: np.ndarray,
    *,
    baseline_factor: float = 3.0,
    prominence_factor: float = 1.0,
    merge_ppm: float = 5,
    baseline_method: Literal["global", "local"] = "local",
    baseline_percentile: float = 10.0,
    local_window: int = 501,
    smooth_sigma: float = 10.0,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Detect centroided peaks from an aggregated profile MS1 spectrum.

    Parameters
    ----------
    baseline_factor
        Peaks must be at least baseline + baseline_factor * baseline.
    prominence_factor
        Minimum prominence expressed as a multiple of the baseline.
    min_distance_bins
        Minimum separation between peaks (bins).
    baseline_method
        "global" or "local".
    baseline_percentile
        Percentile used to estimate the baseline.
    local_window
        Rolling window size for local baseline estimation.
    smooth_sigma
        Gaussian smoothing sigma applied to the local baseline.
    """

    baseline = _estimate_baseline(
        intensities=mean_intensities,
        method=baseline_method,
        percentile=baseline_percentile,
        local_window=local_window,
        smooth_sigma=smooth_sigma,
    )

    height = baseline * (1.0 + baseline_factor)
    prominence = baseline * prominence_factor

    peak_indices, _ = find_peaks(
        mean_intensities,
        height=height,
        prominence=prominence,
    )

    if len(peak_indices) == 0:
        return np.array([], dtype=float), np.array([], dtype=float)

    decimals = _infer_decimal_places(bin_centers)

    refined_mzs = np.empty(len(peak_indices), dtype=float)
    refined_intensities = np.empty(len(peak_indices), dtype=float)

    for i, idx in enumerate(peak_indices):
        if 0 < idx < len(mean_intensities) - 1:
            y1, y2, y3 = mean_intensities[idx - 1 : idx + 2]
            x1, x2, x3 = bin_centers[idx - 1 : idx + 2]

            denom = y1 - 2 * y2 + y3

            if np.abs(denom) > 1e-12:
                delta = 0.5 * (y1 - y3) / denom

                refined_mzs[i] = x2 + delta * (x3 - x1) / 2
                refined_intensities[i] = y2 - 0.25 * (y1 - y3) * delta
            else:
                refined_mzs[i] = x2
                refined_intensities[i] = y2

        else:
            refined_mzs[i] = bin_centers[idx]
            refined_intensities[i] = mean_intensities[idx]

    mz_centroid, int_centroid = _merge_peaks_ppm(
        refined_mzs, refined_intensities, ppm=merge_ppm
    )

    mz_centroid = np.round(mz_centroid, decimals)

    return mz_centroid, int_centroid


def filter_intensities_mad(
    mz_array, intensity_array, *, log: bool = True, n_mads: float = 2
) -> tuple[np.ndarray, np.ndarray]:

    if log:
        threshold = 10 ** (
            np.median(np.log10(intensity_array))
            + n_mads
            * np.median(
                np.abs(np.log10(intensity_array) - np.median(np.log10(intensity_array)))
            )
        )
    else:
        threshold = np.median(intensity_array) + n_mads * np.median(
            np.abs(intensity_array - np.median(intensity_array))
        )

    mask = intensity_array > threshold

    return mz_array[mask], intensity_array[mask]


def load_aggregated_spectra(
    ms1_db_path: str | Path, project_id: str, command_name: str
) -> tuple[np.ndarray, np.ndarray]:

    with sqlite3.connect(Path(ms1_db_path)) as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
                       SELECT mz_array, intensity_array FROM aggregated_spectra 
                       LEFT JOIN commands on aggregated_spectra.command_id = commands.id 
                       WHERE commands.command_name = ? AND commands.project_id = ?""",
            (command_name, project_id),
        )

        mzs_blob, intensities_blob = cursor.fetchone()

        return blob_to_array(mzs_blob), blob_to_array(intensities_blob)
