from __future__ import annotations

import numpy as np

from msianalyzer.core.plotting.heatmap import _feature_column_index, _layer_column
from msianalyzer.core.plotting.roi import roi_obs_column
from msianalyzer.core.utils.lazy_import import lazy_module

ad = lazy_module("anndata")


def roi_mean_intensity(
    adata: ad.AnnData, roi_name: str, mz: float, layer: str = "raw"
) -> float | None:
    """Mean intensity of one feature over one ROI's pixels.

    General-purpose — not what the calibration workflow itself uses for
    its curve's "measured signal" (see `roi_internal_standard_signal` for
    that): a bare raw intensity has no correction for cross-sample/
    cross-pixel ionization efficiency differences, which matters once
    standards and unknowns can legitimately live on different samples.

    Reuses the same `obs["roi_<name>"]` boolean column ROI drawing already
    persists (`core.plotting.roi.save_roi_to_sample`) and the same
    nearest-`.var["mz"]` lookup the heatmap renderer uses
    (`_feature_column_index`/`_layer_column`) — no new geometry or feature
    lookup, just their combination.

    Args:
        adata: One sample's `AnnData`, already loaded.
        roi_name: The ROI to average over.
        mz: The analyte's m/z — matched to the nearest `.var["mz"]`.
        layer: Which `adata.layers` entry to read (falls back to `.X` if
            not present, see `_layer_column`).

    Returns:
        The mean intensity across the ROI's pixels, or `None` if the ROI
        doesn't exist on this sample or has no pixels.
    """
    col = roi_obs_column(roi_name)
    if col not in adata.obs.columns:
        return None
    mask = adata.obs[col].to_numpy(dtype=bool)
    if not mask.any():
        return None
    col_idx = _feature_column_index(adata, mz)
    values = _layer_column(adata, layer, col_idx)
    return float(np.mean(values[mask]))


def roi_internal_standard_signal(
    adata: ad.AnnData,
    roi_name: str,
    analyte_mz: float,
    reference_mz: float,
    layer: str = "raw",
) -> float | None:
    """The calibration workflow's actual "measured signal" for one ROI:
    the mean, per pixel, of the analyte's intensity divided by the
    reference channel's own intensity (`NormalizationConfig.reference_mz`)
    — a classic internal-standard correction, computed here regardless of
    whatever normalization `mode` the run itself used (`"tic"` or
    `"reference_mz"`), since a calibration curve's standards and its
    unknowns can legitimately live on entirely different samples/
    acquisitions, and raw intensity alone carries no correction for
    ionization-efficiency drift between them.

    Ratio computed per pixel *before* averaging (not analyte-mean /
    reference-mean) — more robust to the reference channel varying
    meaningfully within the ROI than a ratio of the two means would be.

    Args:
        adata: One sample's `AnnData`, already loaded.
        roi_name: The ROI to average over.
        analyte_mz: The analyte's m/z — matched to the nearest `.var["mz"]`.
        reference_mz: The internal-standard/reference channel's m/z, same
            matching convention.
        layer: Which `adata.layers` entry both channels are read from
            (falls back to `.X` if not present) — always `"raw"` in
            practice, since a log1p-normalized layer would distort the
            ratio.

    Returns:
        The mean per-pixel ratio across the ROI's pixels, or `None` if the
        ROI doesn't exist on this sample, has no pixels, or every one of
        its pixels has a non-positive reference-channel value (an
        undefined ratio).
    """
    col = roi_obs_column(roi_name)
    if col not in adata.obs.columns:
        return None
    mask = adata.obs[col].to_numpy(dtype=bool)
    if not mask.any():
        return None
    analyte_idx = _feature_column_index(adata, analyte_mz)
    reference_idx = _feature_column_index(adata, reference_mz)
    analyte_values = _layer_column(adata, layer, analyte_idx)[mask]
    reference_values = _layer_column(adata, layer, reference_idx)[mask]
    valid = reference_values > 0
    if not valid.any():
        return None
    return float(np.mean(analyte_values[valid] / reference_values[valid]))


def roi_pixel_count(adata: ad.AnnData, roi_name: str) -> int:
    """Number of pixels belonging to one ROI on this sample.

    Args:
        adata: One sample's `AnnData`, already loaded.
        roi_name: The ROI to count.

    Returns:
        `0` if the ROI doesn't exist on this sample.
    """
    col = roi_obs_column(roi_name)
    if col not in adata.obs.columns:
        return 0
    return int(adata.obs[col].to_numpy(dtype=bool).sum())
