from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import anndata as ad
import numpy as np

from msianalyzer.core.utils.logging_utils import log_call

logger = logging.getLogger(__name__)


@dataclass
class TicNormalizationResult:
    """Outcome of `run_tic_normalization`.

    Attributes:
        n_samples: Number of samples normalized.
        n_pixels: Total pixel count across every sample.
        median_tic: The dataset-wide median `obs['tic']`, used as the
            normalization reference for every pixel.
        merged_path: Path the concatenated `merged.h5ad` was written to.
    """

    n_samples: int
    n_pixels: int
    median_tic: float
    merged_path: Path


def _add_normalized_layers(adata: ad.AnnData, median_tic: float) -> None:
    """Add `layers['raw']` and `layers['TIC']` to `adata`, in place.

    `layers['raw']` is an untouched copy of `.X`. `layers['TIC']` is, per
    pixel *i* and feature *j*:
    `log1p(X_raw[i, j] / (obs['tic'][i] / median_tic))` — pixels whose own
    `tic` (or the dataset-wide `median_tic`) is zero or non-finite are
    normalized to `0` rather than dividing by zero.

    Args:
        adata: A per-sample `AnnData` with `obs['tic']` and a sparse `.X`
            (as built by `create_spatial_adata`). Mutated in place.
        median_tic: Dataset-wide median `obs['tic']`, computed once across
            every sample in the analysis by `run_tic_normalization`.
    """
    tic = adata.obs["tic"].to_numpy(dtype=float)

    if median_tic > 0:
        norm_factor = tic / median_tic
    else:
        norm_factor = np.zeros_like(tic)

    inv_factor = np.zeros_like(norm_factor, dtype=np.float32)
    nonzero = norm_factor > 0
    inv_factor[nonzero] = (1.0 / norm_factor[nonzero]).astype(np.float32)

    raw = adata.X.tocsr().astype(np.float32)
    normalized = raw.multiply(inv_factor[:, None]).tocsr()
    normalized.data = np.log1p(normalized.data).astype(np.float32)

    adata.layers["raw"] = raw
    adata.layers["TIC"] = normalized


@log_call(source="out_dir")
def run_tic_normalization(
    sample_h5ad_paths: dict[str, Path],
    out_dir: str | Path,
) -> TicNormalizationResult:
    """TIC-normalize every sample of an analysis and persist a merged AnnData.

    Reads each sample's `.h5ad`, computes the median `obs['tic']` across
    every pixel of every sample, uses it to add `layers['raw']` /
    `layers['TIC']` to each (see `_add_normalized_layers`), concatenates the
    (now layer-carrying) samples into one `merged.h5ad` (a `sample` obs
    column is added from the dict keys), and writes both the merged object
    and each updated per-sample `.h5ad` back to disk.

    Args:
        sample_h5ad_paths: Sample name -> that sample's `.h5ad` path. Files
            are overwritten in place with the two new layers added.
        out_dir: Analysis output folder; `merged.h5ad` is written here.

    Returns:
        A `TicNormalizationResult` summarising what was written.
    """
    out_dir = Path(out_dir)

    samples = {
        name: ad.read_h5ad(path) for name, path in sample_h5ad_paths.items()
    }

    all_tic = np.concatenate(
        [a.obs["tic"].to_numpy(dtype=float) for a in samples.values()]
    )
    n_pixels = int(all_tic.size)
    median_tic = float(np.median(all_tic)) if n_pixels else 0.0

    if median_tic <= 0:
        logger.warning(
            "TIC normalization: dataset-wide median TIC is %.3g (no usable "
            "signal) — every pixel will normalize to 0.",
            median_tic,
        )

    for adata in samples.values():
        _add_normalized_layers(adata, median_tic)

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

    return TicNormalizationResult(
        n_samples=len(samples),
        n_pixels=n_pixels,
        median_tic=median_tic,
        merged_path=merged_path,
    )
