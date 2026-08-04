import numpy as np
import pandas as pd


def align_mz_across_samples(
    mz_arrays: list[np.ndarray],
    sample_names: list[str] = None,
    ppm: float = 10.0,
    mz_decimals: int = 4,
) -> pd.DataFrame:
    """
    Aligns m/z arrays across multiple samples within a target ppm window.

    Parameters:
    -----------
    mz_arrays : list of np.ndarray
        List containing 1D numpy arrays of m/z values for each sample.
    sample_names : list of str, optional
        Column names for the samples. Defaults to ['sample_0', 'sample_1', ...]
    ppm : float
        Tolerance window in parts-per-million (e.g., 10.0 ppm).

    Returns:
    --------
    pd.DataFrame
        DataFrame indexed by aligned (averaged) final m/z.
        Columns contain the original array indices for each sample (NaN if missing).
    """
    n_samples = len(mz_arrays)
    if sample_names is None:
        sample_names = [f"sample_{i}" for i in range(n_samples)]

    # 1. Flatten all peaks into a single list tracking (mz, sample_idx, original_array_index)
    all_peaks = []
    for s_idx, arr in enumerate(mz_arrays):
        for orig_idx, mz_val in enumerate(arr):
            all_peaks.append((float(mz_val), s_idx, orig_idx))

    if not all_peaks:
        return pd.DataFrame(columns=sample_names)

    # Sort all peaks globally by m/z value
    all_peaks.sort(key=lambda x: x[0])

    # 2. Group peaks into clusters using ppm tolerance
    clusters = []
    current_cluster = [all_peaks[0]]

    for peak in all_peaks[1:]:
        mz_val, sample_idx, orig_idx = peak

        # Calculate ppm difference relative to the cluster's reference m/z (mean)
        cluster_mean_mz = np.mean([p[0] for p in current_cluster])
        delta_ppm = abs(mz_val - cluster_mean_mz) / cluster_mean_mz * 1e6

        # Check if peak is within ppm window AND sample hasn't already contributed to this cluster
        existing_samples = {p[1] for p in current_cluster}

        if delta_ppm <= ppm and sample_idx not in existing_samples:
            current_cluster.append(peak)
        else:
            clusters.append(current_cluster)
            current_cluster = [peak]

    if current_cluster:
        clusters.append(current_cluster)

    # 3. Construct DataFrame rows
    records = []
    final_mzs = []

    for cluster in clusters:
        # Calculate master m/z as average across matched peaks
        cluster_mz = np.mean([p[0] for p in cluster])
        final_mzs.append(round(cluster_mz, mz_decimals))

        # Record original index per sample
        row = {name: np.nan for name in sample_names}
        for _, sample_idx, orig_idx in cluster:
            row[sample_names[sample_idx]] = orig_idx

        records.append(row)

    # Build final DataFrame
    df_aligned = pd.DataFrame(records, index=final_mzs)
    df_aligned.index.name = "mz"

    # Ensure index types are Nullable Integers (Int64) so indices don't turn to floats unnecessarily
    for col in sample_names:
        df_aligned[col] = df_aligned[col].astype("Int64")

    return df_aligned
