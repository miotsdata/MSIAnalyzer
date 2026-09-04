import logging
import os
import sqlite3
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from uuid import UUID

import anndata as ad
import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix

from msianalyzer.core.parser import blob_to_array
from msianalyzer.core.utils.logging_utils import log_call, worker_logging

logger = logging.getLogger(__name__)


@log_call(source="db_path")
def create_spatial_adata(
    db_path: str | Path,
    target_mz_set: set[float] | list[float] | np.ndarray,
    project_id: UUID,
    integration_ppm: float = 5.0,
    batch_size: int = 1000,
    scan_handling: str = "average",
    n_workers: int | None = None,
) -> ad.AnnData:
    """Quantify a set of target m/z values across MS1 spectra belonging to spatial pixels.

    Reads pixel-mapped MS1 scans from a SQLite database, quantifies each
    target m/z per pixel in parallel across worker processes, and assembles
    the result into an `AnnData` object with pixel coordinates in `.obsm["spatial"]`.

    Args:
        db_path: Path to SQLite database containing `ms1_scans` and
            `pixel_ms1_scans` tables.
        target_mz_set: Target m/z values to quantify.
        project_id: Identifier of the project this data belongs to, stored
            in `AnnData.uns["project_id"]`.
        integration_ppm: PPM tolerance for peak matching. Defaults to 5.0.
        batch_size: Number of spectra per processing chunk. Defaults to 1000.
        scan_handling: Either `"average"` (average spectral intensities
            across multiple MS1 scans per pixel) or `"first"` (use only the
            first MS1 scan acquired for each pixel). Defaults to `"average"`.
        n_workers: Number of parallel CPU workers. Defaults to
            `os.cpu_count()` if not provided.

    Returns:
        An `AnnData` object with pixels as observations, target m/z values
        as variables, and matched intensities as `.X`.

    Raises:
        ValueError: If `scan_handling` is not `"average"` or `"first"`, or
            if no MS1 scans are found matching spatial pixels in
            `pixel_ms1_scans`.
    """
    if scan_handling not in ("average", "first"):
        raise ValueError("scan_handling must be either 'average' or 'first'")

    if n_workers is None:
        n_workers = os.cpu_count() or 1

    logger.debug(
        "Started creating anndata object with %d workers.",
        n_workers,
        extra={"source_file": db_path},
    )

    # Convert target m/zs to sorted numpy array for fast search
    target_mzs = np.sort(np.fromiter(target_mz_set, dtype=np.float64))
    n_vars = len(target_mzs)

    # 1. Fetch metadata and byte arrays from SQLite for pixel-mapped scans only
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    if scan_handling == "first":
        # Select only the first MS1 scan per pixel using MIN(scan_id)
        query = """
            SELECT
                p.pixel_id,
                sp.x,
                sp.y,
                s.scan_id,
                s.rt,
                s.tic,
                s.polarity,
                s.mz_array,
                s.intensity_array
            FROM ms1_scans s
            INNER JOIN (
                SELECT pixel_id, MIN(scan_id) as first_scan_id
                FROM pixel_ms1_scans
                GROUP BY pixel_id
            ) first_p ON s.scan_id = first_p.first_scan_id
            INNER JOIN pixel_ms1_scans p ON s.scan_id = p.scan_id
            INNER JOIN spatial_pixels sp on p.pixel_id = sp.pixel_id
            ORDER BY p.pixel_id
        """
    else:
        # Fetch all scans belonging to pixels (ordered by pixel_id for grouping)
        query = """
            SELECT
                p.pixel_id,
                sp.x,
                sp.y,
                s.scan_id,
                s.rt,
                s.tic,
                s.polarity,
                s.mz_array,
                s.intensity_array
            FROM ms1_scans s
            INNER JOIN pixel_ms1_scans p ON s.scan_id = p.scan_id
            INNER JOIN spatial_pixels sp on p.pixel_id = sp.pixel_id
            ORDER BY p.pixel_id, s.scan_id
        """

    cursor.execute(query)

    batches = []
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        batches.append(rows)
    conn.close()

    if not batches:
        raise ValueError(
            "No MS1 scans found matching spatial pixels in 'pixel_ms1_scans'."
        )

    # 2. Parallel Processing across Spectrum Batches
    raw_obs_list = []
    matrix_pixel_ids = []
    matrix_cols = []
    matrix_vals = []

    # Multiprocessing-safe logging: workers push records onto a shared queue;
    # a QueueListener in this (main) process consumes them and re-emits
    # through the handlers already attached to the root logger here, so
    # worker logs land in the same file/console with the same formatting.
    with worker_logging() as (log_queue, initializer):
        with ProcessPoolExecutor(
            max_workers=n_workers,
            initializer=initializer,
            initargs=(log_queue,),
        ) as executor:
            futures = [
                executor.submit(
                    process_spectrum_batch, batch, target_mzs, integration_ppm
                )
                for batch in batches
            ]

            for future in as_completed(futures):
                # process_spectrum_batch returns:
                # (batch_obs_list, batch_pixel_ids, batch_cols, batch_vals)
                batch_obs, pix_ids, coo_cols, coo_vals = future.result()

                raw_obs_list.extend(batch_obs)
                matrix_pixel_ids.extend(pix_ids)
                matrix_cols.extend(coo_cols)
                matrix_vals.extend(coo_vals)

    logger.debug(
        "Finished multiprocessing. Assembling object.",
        extra={"source_file": db_path},
    )

    # 3. Assemble and Aggregate by Pixel
    df_entries = pd.DataFrame(
        {"pixel_id": matrix_pixel_ids, "col": matrix_cols, "val": matrix_vals}
    )

    df_obs_raw = pd.DataFrame(raw_obs_list)

    if scan_handling == "average":
        # Group intensities by pixel_id and feature column, taking the mean across scans
        df_aggregated = df_entries.groupby(["pixel_id", "col"], as_index=False)[
            "val"
        ].mean()

        # Aggregate observation metadata per pixel
        obs_df = (
            df_obs_raw.groupby("pixel_id")
            .agg(
                {
                    "scan_id": lambda x: "|".join(map(str, x)),
                    "rt": "mean",
                    "tic": "mean",
                    "polarity": "first",
                    "x": "first",
                    "y": "first",
                }
            )
            .reset_index()
        )
    else:  # scan_handling == 'first'
        df_aggregated = df_entries
        obs_df = df_obs_raw

    # 4. Construct Sparse Matrix
    # Map pixel_id strings/ints to 0-based row indices
    unique_pixels = obs_df["pixel_id"].unique()
    pixel_to_row = {pid: i for i, pid in enumerate(unique_pixels)}

    rows = [int(pixel_to_row[pid]) for pid in df_aggregated["pixel_id"]]
    cols = df_aggregated["col"].values
    vals = df_aggregated["val"].values

    n_obs = len(unique_pixels)
    X_sparse = csr_matrix((vals, (rows, cols)), shape=(n_obs, n_vars), dtype=np.float32)

    # Set AnnData observation index
    obs_df.set_index("pixel_id", inplace=True)

    # 5. Build Variable DataFrame
    var_df = pd.DataFrame(
        {"mz": target_mzs}, index=[f"mz_{mz:.4f}" for mz in target_mzs]
    )

    ad_obj = ad.AnnData(X=X_sparse, obs=obs_df, var=var_df)

    ad_obj.uns["spatial"] = {}
    ad_obj.uns["spatial"][Path(db_path).stem] = {
        "images": {},
        "scalefactors": {},
        "metadata": {},
    }
    ad_obj.obsm["spatial"] = np.array(ad_obj.obs.loc[:, ["x", "y"]])
    ad_obj.obs.file = db_path

    ad_obj.uns["project_id"] = project_id

    logger.debug(
        "Anndata object created, exiting from function.",
        extra={"source_file": db_path},
    )

    # 6. Return AnnData
    return ad_obj


@log_call
def process_spectrum_batch(
    scans_data: list[tuple], target_mzs: np.ndarray, integration_ppm: float = 5.0
) -> tuple[list[dict], list[int | str], list[int], list[float]]:
    """Quantify targeted m/z values in a batch of pixel-mapped MS1 spectra.

    Runs inside a worker process (submitted via `ProcessPoolExecutor`). Uses
    binary search over each scan's sorted m/z array to find, for every
    target m/z window, the maximum intensity within tolerance.

    Args:
        scans_data: Rows fetched from the database, each a tuple of
            `(pixel_id, x, y, scan_id, rt, tic, polarity, mz_bytes, int_bytes)`.
        target_mzs: Sorted array of target m/z values to quantify.
        integration_ppm: PPM tolerance for peak matching. Defaults to 5.0.

    Returns:
        A tuple `(batch_obs, coo_pixel_ids, coo_cols, coo_vals)`:

        - batch_obs: List of dicts holding scan metadata (including
          `pixel_id`) for each scan in the batch.
        - coo_pixel_ids: Pixel ids for each matched peak intensity.
        - coo_cols: Target m/z feature indices for each matched peak.
        - coo_vals: Maximum intensity values matched, one per entry above.
    """
    batch_obs = []
    coo_pixel_ids = []
    coo_cols = []
    coo_vals = []

    # Pre-calculate upper and lower bounds for each target m/z (vectorized)
    mz_deltas = target_mzs * (integration_ppm / 1e6)
    lower_bounds = target_mzs - mz_deltas
    upper_bounds = target_mzs + mz_deltas

    logger.debug("Processing batch of %d scans.", len(scans_data))

    # Unpack pixel_id as the first element from the SQL SELECT query
    for pixel_id, x, y, scan_id, rt, tic, polarity, mz_bytes, int_bytes in scans_data:
        # Store metadata
        batch_obs.append(
            {
                "pixel_id": pixel_id,
                "x": x,
                "y": y,
                "scan_id": scan_id,
                "rt": rt,
                "tic": tic,
                "polarity": polarity,
            }
        )

        # Deserialize SQLite binary BLOBs
        mz_arr = blob_to_array(mz_bytes)
        int_arr = blob_to_array(int_bytes)

        if len(mz_arr) == 0:
            continue

        # Binary search: find target window boundaries in the scan's m/z array
        left_indices = np.searchsorted(mz_arr, lower_bounds, side="left")
        right_indices = np.searchsorted(mz_arr, upper_bounds, side="right")

        # Extract maximum intensity inside each target m/z window
        for mz_idx, (l, r) in enumerate(zip(left_indices, right_indices)):
            if l < r:
                max_int = float(np.max(int_arr[l:r]))
                if max_int > 0:
                    coo_pixel_ids.append(pixel_id)
                    coo_cols.append(mz_idx)
                    coo_vals.append(max_int)

    return batch_obs, coo_pixel_ids, coo_cols, coo_vals
