import sqlite3
import numpy as np
from scipy.signal import find_peaks
from pathlib import Path

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


def save_average_ms1_spectra(
    mzs_array: np.ndarray,
    intensities_array: np.ndarray,
    bin_width: float,
    ms1_db_path: Path | str,
) -> None:
    mzs_blob = array_to_blob(mzs_array)
    intensities_blob = array_to_blob(intensities_array)
    with sqlite3.connect(Path(ms1_db_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS average_ms1 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bin_width FLOAT NOT NULL,
            mz_array BLOB,
            intensity_array BLOB
            );
        """)
        cursor.execute(
            """
            INSERT INTO average_ms1 (bin_width, mz_array, intensity_array)
            VALUES (?, ?, ?);
            """,
            (bin_width, mzs_blob, intensities_blob),
        )
        conn.commit()


def detect_ms1_centroids(
    bin_centers: np.ndarray,
    mean_intensities: np.ndarray,
    snr_threshold: float = 3.0,
    min_prominence_factor: float = 0.01,
    min_distance_bins: int = 3,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Detects real peaks and converts binned signals into centroided m/z and intensities.
    """
    # Estimate noise baseline (Median Absolute Deviation)
    noise_level = np.median(np.abs(mean_intensities - np.median(mean_intensities)))
    min_height = noise_level * snr_threshold

    # 1. Find local maxima with SciPy
    peak_indices, properties = find_peaks(
        mean_intensities,
        height=min_height,
        prominence=min_height * min_prominence_factor,
        distance=min_distance_bins,  # Ensures peaks are separated by at least 2 bins
    )

    if len(peak_indices) == 0:
        return np.array([]), np.array([])

    # 2. Refine centroid m/z using 3-point parabolic interpolation
    # (Fixes binning discretization, yielding sub-bin mass accuracy)
    refined_mzs = []
    refined_ints = []

    for idx in peak_indices:
        if 0 < idx < len(mean_intensities) - 1:
            y1, y2, y3 = mean_intensities[idx - 1 : idx + 2]
            x1, x2, x3 = bin_centers[idx - 1 : idx + 2]

            # Parabolic peak refinement
            denom = y1 - 2 * y2 + y3
            if denom != 0:
                delta = 0.5 * (y1 - y3) / denom
                exact_mz = x2 + delta * (x3 - x1) / 2
                exact_int = y2 - 0.25 * (y1 - y3) * delta
            else:
                exact_mz, exact_int = x2, y2
        else:
            exact_mz, exact_int = bin_centers[idx], mean_intensities[idx]

        refined_mzs.append(exact_mz)
        refined_ints.append(exact_int)

    return np.array(refined_mzs), np.array(refined_ints)
