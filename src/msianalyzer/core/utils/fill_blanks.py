"""Fill blanks (ADR 70): a new layer where a feature's 0 ("not detected")
pixels are filled with the mean of their detected neighbors, when enough
of those neighbors have a value.

A pixel with a spectrum where one m/z wasn't picked holds 0, so an image
of a feature that covers a region shows isolated holes inside it. This
fills a 0 pixel with the mean of the non-zero values among its neighbors
(the square of side `2 * radius + 1` around it), but only when at least
`min_fraction` of the neighbors that have a spectrum are non-zero. That
guard keeps the edge of a region where it is: a 0 pixel just outside
the edge has mostly 0 neighbors and stays 0. Grid positions without a
spectrum are never filled (they aren't pixels of the sample).

The source layer and every other element of the file are left as they
are: only `layers[<source>_filled_r<radius>]` is written (element-level,
ADR 50), in every sample file and in `merged.h5ad` when it exists.
"""

from __future__ import annotations

import logging
import math
import os
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import h5py
import numpy as np
from scipy.sparse import csr_matrix, hstack

from msianalyzer.core.utils import h5ad_io
from msianalyzer.core.utils.logging_utils import log_call, worker_logging
from msianalyzer.core.utils.lazy_import import lazy_module

ad = lazy_module("anndata")

logger = logging.getLogger(__name__)

#: Features processed per block — bounds the memory of the neighbor sums.
_COLUMN_BLOCK = 256

_FILLED_LAYER_PATTERN = re.compile(r"^(?P<source>.+)_filled_r(?P<radius>\d+)$")


def filled_layer_name(source_layer: str, radius: int) -> str:
    """The `.layers` key of `source_layer` filled with `radius`."""
    return f"{source_layer}_filled_r{int(radius)}"


def parse_filled_layer_name(layer_name: str) -> dict | None:
    """`{"source", "radius"}` for a `filled_layer_name`, else None."""
    match = _FILLED_LAYER_PATTERN.match(layer_name)
    if match is None:
        return None
    return {"source": match["source"], "radius": int(match["radius"])}


def neighbor_matrix(x_index: np.ndarray, y_index: np.ndarray, radius: int) -> csr_matrix:
    """`(n_pixels, n_pixels)` 0/1 matrix: row i has a 1 for every pixel
    within `radius` grid cells of pixel i (square window, i excluded).
    `x_index`/`y_index` are each pixel's grid column/row
    (`heatmap.pixel_grid_indices`)."""
    n_pixels = x_index.size
    if n_pixels == 0:
        return csr_matrix((0, 0), dtype=np.float32)
    grid = np.full((int(y_index.max()) + 1, int(x_index.max()) + 1), -1, dtype=np.int64)
    grid[y_index, x_index] = np.arange(n_pixels)
    pixel_ids = np.arange(n_pixels)
    rows, cols = [], []
    for row_offset in range(-radius, radius + 1):
        for col_offset in range(-radius, radius + 1):
            if row_offset == 0 and col_offset == 0:
                continue
            neighbor_y = y_index + row_offset
            neighbor_x = x_index + col_offset
            inside = (
                (neighbor_y >= 0) & (neighbor_y < grid.shape[0])
                & (neighbor_x >= 0) & (neighbor_x < grid.shape[1])
            )
            neighbor_ids = grid[neighbor_y[inside], neighbor_x[inside]]
            has_spectrum = neighbor_ids >= 0
            rows.append(pixel_ids[inside][has_spectrum])
            cols.append(neighbor_ids[has_spectrum])
    rows = np.concatenate(rows)
    cols = np.concatenate(cols)
    return csr_matrix(
        (np.ones(rows.size, dtype=np.float32), (rows, cols)), shape=(n_pixels, n_pixels)
    )


def fill_blanks(
    layer, neighbors: csr_matrix, min_fraction: float
) -> tuple[csr_matrix, int]:
    """`layer` (pixels x features) with its qualifying 0 entries filled.

    An entry is filled when it is 0 and at least
    `ceil(min_fraction * n)` of the pixel's `n` neighbors (with a
    spectrum) are non-zero for that feature; its value is the mean of
    those non-zero neighbors.

    Returns:
        `(filled layer as float32 CSR, number of entries filled)`.
    """
    layer = csr_matrix(layer, dtype=np.float32)
    layer.eliminate_zeros()
    n_neighbors = np.asarray(neighbors.sum(axis=1)).ravel()
    required = np.maximum(1, np.ceil(min_fraction * n_neighbors - 1e-9)).astype(np.int64)

    blocks, n_filled = [], 0
    by_column = layer.tocsc()
    for start in range(0, layer.shape[1], _COLUMN_BLOCK):
        block = by_column[:, start:start + _COLUMN_BLOCK].tocsr()
        detected = block.copy()
        detected.data = np.ones_like(detected.data)
        counts = (neighbors @ detected).tocoo()
        enough = counts.data >= required[counts.row]
        rows, cols = counts.row[enough], counts.col[enough]
        if rows.size:
            is_zero = np.asarray(detected[rows, cols]).ravel() == 0
            rows, cols = rows[is_zero], cols[is_zero]
        if rows.size:
            sums = (neighbors @ block).tocsr()
            values = (
                np.asarray(sums[rows, cols]).ravel()
                / np.asarray(counts.tocsr()[rows, cols]).ravel()
            )
            block = block + csr_matrix(
                (values.astype(np.float32), (rows, cols)), shape=block.shape
            )
            n_filled += int(rows.size)
        blocks.append(block)
    filled = hstack(blocks, format="csr") if blocks else layer
    return csr_matrix(filled, dtype=np.float32), n_filled


def _read_layer(f: h5py.File, source_layer: str):
    """`layers[source_layer]`, or `.X` for `"raw"` when the file has no
    `raw` layer (normalization was off)."""
    if "layers" in f and source_layer in f["layers"]:
        return ad.io.read_elem(f["layers"][source_layer])
    if source_layer == "raw":
        return ad.io.read_elem(f["X"])
    raise KeyError(f"{Path(f.filename).name} has no layer {source_layer!r}")


def _fill_sample(path: str, source_layer: str, radius: int, min_fraction: float, layer_name: str):
    """One sample: write `layers[layer_name]`; returns `(path, n_filled)`."""
    from msianalyzer.core.plotting.heatmap import pixel_grid_indices
    from msianalyzer.core.plotting.light_sample import LightSample

    x_index, y_index, _unique_x, _unique_y = pixel_grid_indices(LightSample(path).adata)
    neighbors = neighbor_matrix(x_index, y_index, radius)
    with h5ad_io.open_for_write(path) as f:
        filled, n_filled = fill_blanks(_read_layer(f, source_layer), neighbors, min_fraction)
        layers = f.require_group("layers")
        if layer_name in layers:
            del layers[layer_name]
        ad.io.write_elem(layers, layer_name, filled)
    return path, n_filled


@dataclass
class FillBlanksResult:
    """Outcome of `add_filled_layer`.

    Attributes:
        layer_name: `filled_layer_name(source_layer, radius)`.
        n_samples: Samples written.
        n_filled: Entries (pixel x feature) filled, over every sample.
        merged_written: False when the analysis has no `merged.h5ad`.
    """

    layer_name: str
    n_samples: int
    n_filled: int
    merged_written: bool


@log_call(source="merged_path")
def add_filled_layer(
    sample_h5ad_paths: dict[str, Path],
    merged_path: Path | str,
    source_layer: str,
    radius: int,
    min_fraction: float,
    *,
    n_workers: int | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> FillBlanksResult:
    """Add `layers[filled_layer_name(source_layer, radius)]` to every
    sample file (in a process pool) and to `merged.h5ad` if it exists.
    Running it again with the same source and radius replaces the layer.

    Args:
        sample_h5ad_paths: Sample name -> that sample's `.h5ad`.
        merged_path: The analysis' `merged.h5ad`; skipped if missing.
        source_layer: Layer to fill (`"TIC"`, `"raw"`, `feature_<id>`, ...).
        radius: Neighborhood half-size in pixels (1 = the 8 neighbors).
        min_fraction: Share of neighbors that must be non-zero, 0-1.
        n_workers: Process pool size (`None` = every core).
        progress: `(done, total, sample_name)` after each sample.
    """
    from msianalyzer.core.utils.tic_normalization import _write_merged_layer

    if radius < 1:
        raise ValueError(f"radius must be at least 1, got {radius}")
    if not 0 < min_fraction <= 1:
        raise ValueError(f"min_fraction must be in (0, 1], got {min_fraction}")
    merged_path = Path(merged_path)
    layer_name = filled_layer_name(source_layer, radius)
    paths = {name: str(path) for name, path in sample_h5ad_paths.items()}
    by_path = {path: name for name, path in paths.items()}

    total, n_filled = len(paths), 0
    workers = max(1, min(n_workers or os.cpu_count() or 1, total or 1))
    with worker_logging() as (log_queue, initializer):
        with ProcessPoolExecutor(
            max_workers=workers, initializer=initializer, initargs=(log_queue,)
        ) as executor:
            futures = [
                executor.submit(_fill_sample, path, source_layer, radius, min_fraction, layer_name)
                for path in paths.values()
            ]
            for done, future in enumerate(as_completed(futures), start=1):
                path, sample_filled = future.result()
                n_filled += sample_filled
                if progress is not None:
                    progress(done, total, by_path[path])

    merged_written = merged_path.exists() and total > 0
    if merged_written:
        _write_merged_layer(merged_path, sample_h5ad_paths, layer_name, scale_column=False)

    return FillBlanksResult(
        layer_name=layer_name, n_samples=total, n_filled=n_filled,
        merged_written=merged_written,
    )


@log_call(source="merged_path")
def remove_filled_layer(
    sample_h5ad_paths: dict[str, Path], merged_path: Path | str, layer_name: str
) -> int:
    """Drop `layers[layer_name]` (a filled layer) from every sample file
    and `merged.h5ad`. Returns how many files had it.

    Raises:
        ValueError: `layer_name` isn't a filled layer's name, so this
            can't be used to delete the run's own layers.
    """
    if parse_filled_layer_name(layer_name) is None:
        raise ValueError(f"{layer_name!r} is not a filled layer")
    paths = [Path(p) for p in sample_h5ad_paths.values()]
    if Path(merged_path).exists():
        paths.append(Path(merged_path))
    removed = 0
    for path in paths:
        with h5ad_io.open_for_write(path) as f:
            if "layers" in f and layer_name in f["layers"]:
                del f["layers"][layer_name]
                removed += 1
    return removed


def required_neighbors(radius: int, min_fraction: float) -> int:
    """How many non-zero neighbors a fully surrounded pixel needs —
    for the GUI's "at least N of M" hint."""
    return max(1, math.ceil(min_fraction * ((2 * radius + 1) ** 2 - 1) - 1e-9))
