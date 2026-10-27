from __future__ import annotations

import logging
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import h5py
import numpy as np
from scipy.sparse import csr_matrix, vstack

from msianalyzer.core.plotting.heatmap import _feature_column_index, _layer_column
from msianalyzer.core.utils import h5ad_io
from msianalyzer.core.utils.logging_utils import log_call, worker_logging
from msianalyzer.core.utils.lazy_import import lazy_module

ad = lazy_module("anndata")

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


def _normalize(raw, reference_values, dataset_reference: float):
    """`(log1p(raw[i, :] * inv[i]), inv)` with `inv[i] = dataset_reference /
    reference_values[i]`, or `0` where either is zero or non-finite. `raw`
    is a float32 CSR matrix. The math of `_add_normalized_layers` and
    `add_feature_normalized_layer`."""
    reference_values = np.asarray(reference_values, dtype=float)

    if dataset_reference > 0:
        norm_factor = reference_values / dataset_reference
    else:
        norm_factor = np.zeros_like(reference_values)

    inv_factor = np.zeros_like(norm_factor, dtype=np.float32)
    nonzero = norm_factor > 0
    inv_factor[nonzero] = (1.0 / norm_factor[nonzero]).astype(np.float32)

    normalized = raw.multiply(inv_factor[:, None]).tocsr()
    normalized.data = np.log1p(normalized.data).astype(np.float32)
    return normalized, inv_factor


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
    raw = adata.X.tocsr().astype(np.float32)
    normalized, inv_factor = _normalize(raw, reference_values, dataset_reference)

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


# ---------------------------------------------------------------------------
# After the run (ADR 61): normalize to one feature
# ---------------------------------------------------------------------------


def feature_layer_name(feature_id: int) -> str:
    """The `.layers` key of the layer normalized to `feature_id`."""
    return f"feature_{int(feature_id)}"


@dataclass
class FeatureNormalizationResult:
    """Outcome of `add_feature_normalized_layer`.

    Attributes:
        layer_name: `feature_layer_name(feature_id)`.
        n_samples: Samples written.
        n_pixels: Pixels across every sample.
        n_zero_pixels: Pixels where the reference feature is 0. They are
            0 in the new layer.
        reference_median: Dataset-wide median of the reference feature.
        merged_written: False when the analysis has no `merged.h5ad`.
    """

    layer_name: str
    n_samples: int
    n_pixels: int
    n_zero_pixels: int
    reference_median: float
    merged_written: bool


def _read_reference_column(path: str, mz: float) -> np.ndarray:
    """Raw intensity (`.X`, which the pipeline never normalizes in place)
    of the `.var` column nearest `mz`, reading only that column."""
    with h5py.File(path, "r") as f:
        var_mz = np.asarray(ad.io.read_elem(f["var"])["mz"], dtype=float)
        col = int(np.argmin(np.abs(var_mz - mz)))
        X = ad.io.sparse_dataset(f["X"]) if isinstance(f["X"], h5py.Group) else f["X"]
        column = X[:, [col]]
    column = column.toarray() if hasattr(column, "toarray") else np.asarray(column)
    return np.asarray(column, dtype=float).ravel()


def _write_feature_layer(
    path: str, reference_values: np.ndarray, reference_median: float, layer_name: str
) -> str:
    """One sample: write `layers[layer_name]` and `obs["norm_scale_<layer>"]`
    in place, without rewriting the rest of the file (ADR 50)."""
    with h5py.File(path, "r+") as f:
        raw = csr_matrix(ad.io.read_elem(f["X"]), dtype=np.float32)
        normalized, inv_factor = _normalize(raw, reference_values, reference_median)
        layers = f.require_group("layers")
        if layer_name in layers:
            del layers[layer_name]
        ad.io.write_elem(layers, layer_name, normalized)
        h5ad_io.set_obs_column(f, f"norm_scale_{layer_name}", inv_factor)
    return path


def _write_merged_layer(
    merged_path: Path, sample_paths: dict[str, Path], layer_name: str
) -> None:
    """Stack the per-sample layers into `merged.h5ad`'s row order (its
    `sample` obs column) and write them as one layer."""
    blocks, scales = {}, {}
    for name, path in sample_paths.items():
        with h5py.File(path, "r") as f:
            blocks[name] = ad.io.read_elem(f["layers"][layer_name]).tocsr()
            scales[name] = np.asarray(
                h5ad_io.read_obs_column(f, f"norm_scale_{layer_name}"), dtype=np.float32
            )
    with h5py.File(merged_path, "r+") as f:
        sample_col = np.asarray(h5ad_io.read_obs_column(f, "sample")).astype(str)
        rows = [np.flatnonzero(sample_col == name) for name in blocks]
        for name, idx in zip(blocks, rows):
            if idx.size != blocks[name].shape[0]:
                raise ValueError(
                    f"merged.h5ad has {idx.size} rows for sample {name!r}, "
                    f"its own file has {blocks[name].shape[0]}"
                )
        order = np.argsort(np.concatenate(rows), kind="stable")
        stacked = vstack(list(blocks.values()), format="csr")[order]
        scale = np.concatenate(list(scales.values()))[order]
        if stacked.shape[0] != sample_col.size:
            raise ValueError("merged.h5ad has rows of samples this analysis doesn't list")
        layers = f.require_group("layers")
        if layer_name in layers:
            del layers[layer_name]
        ad.io.write_elem(layers, layer_name, stacked)
        h5ad_io.set_obs_column(f, f"norm_scale_{layer_name}", scale)


@log_call(source="merged_path")
def add_feature_normalized_layer(
    sample_h5ad_paths: dict[str, Path],
    merged_path: Path | str,
    feature_id: int,
    mz: float,
    *,
    n_workers: int | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> FeatureNormalizationResult:
    """Normalize a finished analysis to one feature, as a new layer.

    Same math as the run's `mode="reference_mz"` (`_normalize`): per pixel,
    `log1p(raw / (ref / median(ref)))`, the median taken over every pixel
    of every sample. The result goes to `layers[feature_layer_name(id)]`
    and `obs["norm_scale_<layer>"]` of each sample file and of
    `merged.h5ad` (if present). `.X`, `raw`, the run's own normalized
    layer and `obs["norm_scale"]` are left alone. Running it again for
    the same feature replaces that layer.

    Args:
        sample_h5ad_paths: Sample name -> that sample's `.h5ad`.
        merged_path: The analysis' `merged.h5ad`; skipped if missing.
        feature_id: Names the layer.
        mz: The feature's m/z; the nearest `.var` column is the reference.
        n_workers: Process pool size (`None` = every core).
        progress: `(done, total, sample_name)` after each sample.
    """
    merged_path = Path(merged_path)
    layer_name = feature_layer_name(feature_id)
    paths = {name: str(path) for name, path in sample_h5ad_paths.items()}

    reference = {name: _read_reference_column(path, mz) for name, path in paths.items()}
    all_reference = np.concatenate(list(reference.values())) if reference else np.zeros(0)
    n_pixels = int(all_reference.size)
    reference_median = float(np.median(all_reference)) if n_pixels else 0.0
    if reference_median <= 0:
        logger.warning(
            "Feature %s (m/z %.4f): median intensity is %.3g, more than half "
            "the pixels have no signal; they normalize to 0.",
            feature_id, mz, reference_median,
        )

    total = len(paths)
    workers = max(1, min(n_workers or os.cpu_count() or 1, total))
    by_path = {path: name for name, path in paths.items()}
    with worker_logging() as (log_queue, initializer):
        with ProcessPoolExecutor(
            max_workers=workers, initializer=initializer, initargs=(log_queue,)
        ) as executor:
            futures = [
                executor.submit(
                    _write_feature_layer, path, reference[name], reference_median, layer_name
                )
                for name, path in paths.items()
            ]
            for done, future in enumerate(as_completed(futures), start=1):
                path = future.result()
                if progress is not None:
                    progress(done, total, by_path[path])

    merged_written = merged_path.exists()
    if merged_written:
        _write_merged_layer(merged_path, sample_h5ad_paths, layer_name)

    return FeatureNormalizationResult(
        layer_name=layer_name,
        n_samples=total,
        n_pixels=n_pixels,
        n_zero_pixels=int(np.count_nonzero(~(all_reference > 0))),
        reference_median=reference_median,
        merged_written=merged_written,
    )


@log_call(source="merged_path")
def remove_feature_layer(
    sample_h5ad_paths: dict[str, Path], merged_path: Path | str, feature_id: int
) -> int:
    """Undo `add_feature_normalized_layer`: drop `layers["feature_<id>"]`
    and `obs["norm_scale_feature_<id>"]` from every sample file and
    `merged.h5ad`. HDF5 doesn't shrink a file when an element is deleted,
    so the files keep their size until rewritten.

    Returns:
        The number of files the layer was removed from.
    """
    layer_name = feature_layer_name(feature_id)
    paths = [Path(p) for p in sample_h5ad_paths.values()]
    if Path(merged_path).exists():
        paths.append(Path(merged_path))
    removed = 0
    for path in paths:
        with h5py.File(path, "r+") as f:
            had_layer = "layers" in f and layer_name in f["layers"]
            if had_layer:
                del f["layers"][layer_name]
            h5ad_io.drop_obs_column(f, f"norm_scale_{layer_name}")
        removed += had_layer
    return removed
