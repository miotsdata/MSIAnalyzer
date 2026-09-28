from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import anndata as ad
import numpy as np

from msianalyzer.core.plotting.heatmap import _feature_column_index, _layer_column
from msianalyzer.core.utils.logging_utils import log_call

logger = logging.getLogger(__name__)


@dataclass
class NormalizationResult:
    """Outcome of `run_normalization`.

    Attributes:
        n_samples: Number of samples normalized.
        n_pixels: Total pixel count across every sample.
        reference_median: The dataset-wide median of the reference values
            (per-pixel TIC, or per-pixel reference-m/z intensity), used as
            the normalization reference for every pixel.
        layer_name: The `adata.layers` key the normalized layer was written
            under — `"TIC"` for `mode="tic"`, or `f"mz_{reference_mz:.4f}"`
            for `mode="reference_mz"`.
        merged_path: Path the concatenated `merged.h5ad` was written to.
    """

    n_samples: int
    n_pixels: int
    reference_median: float
    layer_name: str
    merged_path: Path


def _add_normalized_layers(
    adata: ad.AnnData,
    reference_values: np.ndarray,
    dataset_reference: float,
    layer_name: str,
) -> None:
    """Add `layers['raw']` and `layers[layer_name]` to `adata`, in place.

    `layers['raw']` is an untouched copy of `.X`. `layers[layer_name]` is,
    per pixel *i* and feature *j*:
    `log1p(X_raw[i, j] / (reference_values[i] / dataset_reference))` —
    pixels whose own reference value (or the dataset-wide
    `dataset_reference`) is zero or non-finite are normalized to `0` rather
    than dividing by zero. The per-pixel scale factor actually applied
    (`1 / (reference_values[i] / dataset_reference)`) is also persisted to
    `obs['norm_scale']`, so a feature quantified later against this same
    `.h5ad` could be normalized the same way without re-deriving the
    reference.

    Args:
        adata: A per-sample `AnnData` with a sparse `.X` (as built by
            `create_spatial_adata`). Mutated in place.
        reference_values: Per-pixel reference value — `obs['tic']` for
            `mode="tic"`, or the reference m/z's own column for
            `mode="reference_mz"`. Same length/order as `adata.obs`.
        dataset_reference: Dataset-wide median of `reference_values`,
            computed once across every sample in the analysis by
            `run_normalization`.
        layer_name: The `adata.layers` key to write the normalized layer
            under.
    """
    reference_values = np.asarray(reference_values, dtype=float)

    if dataset_reference > 0:
        norm_factor = reference_values / dataset_reference
    else:
        norm_factor = np.zeros_like(reference_values)

    inv_factor = np.zeros_like(norm_factor, dtype=np.float32)
    nonzero = norm_factor > 0
    inv_factor[nonzero] = (1.0 / norm_factor[nonzero]).astype(np.float32)

    raw = adata.X.tocsr().astype(np.float32)
    normalized = raw.multiply(inv_factor[:, None]).tocsr()
    normalized.data = np.log1p(normalized.data).astype(np.float32)

    adata.layers["raw"] = raw
    adata.layers[layer_name] = normalized
    adata.obs["norm_scale"] = inv_factor


def _reference_mz_column(adata: ad.AnnData, mz: float, ppm: float) -> np.ndarray:
    """Per-pixel raw intensity of the `.var` column nearest `mz`.

    Reuses the same nearest-`.var["mz"]` lookup the heatmap renderer uses
    (`_feature_column_index`/`_layer_column`), against `layers['raw']` when
    present (the untouched pre-normalization values) or `.X` otherwise —
    `_layer_column` already falls back to `.X` when the named layer isn't
    there yet.

    Args:
        adata: One sample's `AnnData`, already carrying the reference m/z
            in its `.var` (guaranteed by `run.py`'s `assemble_adata` step,
            which unions `reference_mz` into `target_mz_set`).
        mz: The reference m/z to normalize against.
        ppm: Tolerance used only for a sanity warning — the nearest column
            is used regardless (matches `_feature_column_index`'s own
            nearest-match, no-hard-failure behavior).

    Returns:
        Per-pixel intensity, same order as `adata.obs`.
    """
    col_idx = _feature_column_index(adata, mz)
    matched_mz = float(adata.var["mz"].to_numpy(dtype=float)[col_idx])
    tolerance = mz * ppm / 1e6
    if abs(matched_mz - mz) > tolerance:
        logger.warning(
            "Reference m/z %.4f: nearest feature is %.4f, outside the "
            "%.1f ppm tolerance — using it anyway.",
            mz,
            matched_mz,
            ppm,
        )
    return _layer_column(adata, "raw", col_idx)


@log_call(source="out_dir")
def run_normalization(
    sample_h5ad_paths: dict[str, Path],
    out_dir: str | Path,
    mode: str = "tic",
    reference_mz: float | None = None,
    reference_ppm: float = 5.0,
) -> NormalizationResult:
    """Normalize every sample of an analysis and persist a merged AnnData.

    Reads each sample's `.h5ad`, computes the median reference value across
    every pixel of every sample (`obs['tic']` for `mode="tic"`, or the
    reference m/z's own column for `mode="reference_mz"`), uses it to add
    `layers['raw']` / `layers[layer_name]` to each (see
    `_add_normalized_layers`), concatenates the (now layer-carrying)
    samples into one `merged.h5ad` (a `sample` obs column is added from the
    dict keys), and writes both the merged object and each updated
    per-sample `.h5ad` back to disk.

    Args:
        sample_h5ad_paths: Sample name -> that sample's `.h5ad` path. Files
            are overwritten in place with the two new layers added.
        out_dir: Analysis output folder; `merged.h5ad` is written here.
        mode: `"tic"` (default) or `"reference_mz"`.
        reference_mz: Required when `mode="reference_mz"` — the m/z each
            pixel is normalized against.
        reference_ppm: Tolerance used only for a sanity warning when
            matching `reference_mz` to the nearest `.var["mz"]`.

    Returns:
        A `NormalizationResult` summarising what was written.
    """
    out_dir = Path(out_dir)
    layer_name = "TIC" if mode == "tic" else f"mz_{reference_mz:.4f}"

    samples = {
        name: ad.read_h5ad(path) for name, path in sample_h5ad_paths.items()
    }

    if mode == "tic":
        reference_by_sample = {
            name: a.obs["tic"].to_numpy(dtype=float) for name, a in samples.items()
        }
    else:
        reference_by_sample = {
            name: _reference_mz_column(a, reference_mz, reference_ppm)
            for name, a in samples.items()
        }

    all_reference = np.concatenate(list(reference_by_sample.values()))
    n_pixels = int(all_reference.size)
    reference_median = float(np.median(all_reference)) if n_pixels else 0.0

    if reference_median <= 0:
        logger.warning(
            "Normalization (%s): dataset-wide median reference value is "
            "%.3g (no usable signal) — every pixel will normalize to 0.",
            mode,
            reference_median,
        )

    for name, adata in samples.items():
        _add_normalized_layers(
            adata, reference_by_sample[name], reference_median, layer_name
        )

    merged = ad.concat(
        samples,
        label="sample",
        index_unique="-",
        merge="same",
        uns_merge="unique",
    )
    merged_path = out_dir / "merged.h5ad"
    merged.write_h5ad(merged_path)

    for name, adata in samples.items():
        adata.write_h5ad(sample_h5ad_paths[name])

    return NormalizationResult(
        n_samples=len(samples),
        n_pixels=n_pixels,
        reference_median=reference_median,
        layer_name=layer_name,
        merged_path=merged_path,
    )
