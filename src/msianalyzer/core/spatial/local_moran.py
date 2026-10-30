from __future__ import annotations

import logging
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import h5py
import numpy as np

from msianalyzer.core.plotting.heatmap import (
    _colorize_grid,
    _feature_column_index,
    _layer_column,
    _reconstruct_grid,
)
from msianalyzer.core.utils import h5ad_io
from msianalyzer.core.utils.logging_utils import log_call
from msianalyzer.core.utils.lazy_import import lazy_module

ad = lazy_module("anndata")

logger = logging.getLogger(__name__)

#: Rook adjacency, matching compute_spatial_autocorr's own global-Moran's-I
#: neighbor graph — a square pixel grid, not the hexagonal Visium layout
#: squidpy's own default (n_neighs=6) assumes.
_N_NEIGHS = 4


def _ensure_spatial_neighbors(adata: ad.AnnData, n_neighs: int = _N_NEIGHS) -> None:
    """Build `adata.obsp["spatial_connectivities"]` if it isn't already
    there — shared by `local_moran_values` (one feature, on demand) and
    `_init_local_moran_worker` (once per worker process, see
    `compute_local_moran_layer`'s parallel path), so both go through the
    exact same "reuse if present" check rather than duplicating it.
    """
    if "spatial_connectivities" in adata.obsp:
        return
    import squidpy as sq  # heavy (scanpy/numba) — imported lazily so
    # importing this module (transitively, via heatmap_provider.py at GUI
    # startup) doesn't pull it in until a local Moran's I render is
    # actually requested.

    sq.gr.spatial_neighbors_grid(adata, n_neighs=n_neighs)


def local_moran_values(adata: ad.AnnData, mz: float, layer: str = "TIC") -> np.ndarray:
    """Per-pixel local Moran's I (LISA) for one feature, one already-open
    sample — the per-pixel analogue of `compute_spatial_autocorr`'s
    per-sample global score, for telling genuine within-tissue spatial
    structure apart from a sample's coarse matrix-vs-tissue split (both
    captured in the same image, which a single global score can't
    distinguish).

    Anselin (1995)'s definition: `I_i = (z_i / m2) * sum_j w_ij * z_j`,
    where `z` is the feature's pixel values mean-centered, `m2 = sum(z^2) /
    n` (the population variance), and `w` is `adata.obsp
    ["spatial_connectivities"]` (squidpy's binary grid adjacency)
    row-standardized so each pixel's neighbor contributions sum to 1.
    Pixels with no neighbors (`row_sum == 0`) have no defined LISA value
    and are `NaN`, not 0 — `_colorize_grid` already renders `NaN` as black,
    the same "no data" convention used elsewhere in this module.

    Squidpy itself has no local/LISA Moran's I (only the global
    `spatial_autocorr`), and `esda`/`libpysal` (the PySAL package that
    does) aren't a dependency here — this is a direct, hand-rolled
    implementation of the standard formula against squidpy's own adjacency
    output rather than adding another heavy dependency for one function.

    Builds the neighbor graph itself if `adata.obsp` doesn't already have
    one (see `_ensure_spatial_neighbors`) rather than *requiring* a prior
    `spatial_correlation` pipeline run to have populated it, so this works
    for any sample regardless of whether that (separate, opt-in) stage was
    ever enabled. If one is already present — because that stage *did*
    run and its result was written back to this `.h5ad`, or because this
    function already built one earlier for a different feature against
    this same in-memory `adata` (see `compute_local_moran_layer`, which
    calls this once per feature) — it's reused as-is rather than rebuilt,
    since `n_neighs` is the same fixed constant everywhere in this module.

    Args:
        adata: One sample's AnnData, already loaded (`ad.read_h5ad`, backed
            or in-memory — only `.obsm["spatial"]`/`.X`/`.layers` are read).
        mz: The feature's consensus m/z — matched to the nearest `.var["mz"]`.
        layer: `"raw"` or `"TIC"` — which `adata.layers` entry to read.

    Returns:
        A 1D array, one value per pixel, in `adata.obs` row order — the
        same shape `_reconstruct_grid` expects.
    """
    _ensure_spatial_neighbors(adata)

    col_idx = _feature_column_index(adata, mz)
    values = _layer_column(adata, layer, col_idx)

    z = values - np.nanmean(values)
    n = z.size
    m2 = float(np.sum(z**2)) / n
    if m2 == 0:
        return np.full(n, np.nan)

    weights = adata.obsp["spatial_connectivities"]
    row_sums = np.asarray(weights.sum(axis=1)).reshape(-1)
    has_neighbors = row_sums > 0
    inv_row_sums = np.divide(
        1.0, row_sums, out=np.zeros_like(row_sums), where=has_neighbors
    )
    weights_standardized = weights.multiply(inv_row_sums[:, None]).tocsr()
    lag = np.asarray(weights_standardized @ z).reshape(-1)

    local_i = (z / m2) * lag
    return np.where(has_neighbors, local_i, np.nan)


def render_local_moran_heatmap(
    adata: ad.AnnData,
    mz: float,
    layer: str = "TIC",
    colormap: str = "coolwarm",
    vmin: float | None = None,
    vmax: float | None = None,
) -> np.ndarray:
    """One feature's per-pixel local Moran's I as an RGBA `uint8` raster —
    the local/per-pixel analogue of
    `core.plotting.heatmap.render_feature_heatmap`. See
    `local_moran_values` for the computation.

    Args:
        adata: One sample's AnnData, already loaded.
        mz: The feature's consensus m/z — matched to the nearest `.var["mz"]`.
        layer: `"raw"` or `"TIC"` — which `adata.layers` entry to read.
        colormap: Any registered Matplotlib colormap name — a diverging
            one (e.g. `"coolwarm"`) reads better than the sequential
            defaults elsewhere in this app, since local Moran's I values
            are signed (positive: local clustering; negative: local
            spatial outlier).
        vmin: Lower color-scale bound. `None` autoscales symmetrically
            around 0 (see below) rather than to the data's raw min.
        vmax: Upper color-scale bound. `None` autoscales symmetrically
            around 0 (see below) rather than to the data's raw max.

    Returns:
        An `(n_unique_y, n_unique_x, 4)` `uint8` array (RGBA).
    """
    values = local_moran_values(adata, mz, layer=layer)

    if vmin is None and vmax is None:
        # `_colorize_grid`'s own default (raw data min/max) is wrong for a
        # *signed* metric: local Moran's I is usually dominated by a small
        # number of strongly-clustered hot spots against a much smaller
        # negative tail (or vice versa), so the raw min/max are rarely
        # symmetric. Normalizing to that asymmetric range puts "0" (no
        # local structure — the vast majority of any real image) well off
        # the colormap's visual center, so nearly the whole image reads as
        # sitting near one extreme even though its real values are close
        # to neutral. Force a symmetric range instead, so 0 always lands
        # exactly at coolwarm's white center and only genuinely extreme
        # pixels show strong color.
        finite = values[np.isfinite(values)]
        if finite.size:
            bound = float(np.max(np.abs(finite)))
            if bound > 0:
                vmin, vmax = -bound, bound

    grid = _reconstruct_grid(adata, values)
    return _colorize_grid(grid, colormap, vmin, vmax)


def pct_above_threshold(values: np.ndarray, threshold: float) -> float:
    """What percentage of `values` (a `local_moran_values` result) exceed
    `threshold` (strictly — not equal) — `0.0` when there are no finite
    values at all, never a divide-by-zero. Public (not `_`-prefixed): the
    GUI recomputes this live from `adata.layers["localMoranI"]` every time
    the user changes the threshold, so this is the shared, single
    definition both `compute_local_moran_layer`'s tests and
    `LocalMoranSummaryModel` (`gui/utils/local_moran_summary_model.py`)
    import and use, rather than each re-implementing the same formula.
    """
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return 0.0
    return 100.0 * float(np.count_nonzero(finite > threshold)) / finite.size


#: `.uns` key of the per-threshold summary `compute_local_moran_layer`
#: writes next to the layer.
SUMMARY_UNS_KEY = "localMoranSummary"

#: Thresholds the summary is computed at. The GUI snaps its threshold to
#: the nearest one: reading the full per-pixel layer instead took minutes
#: and GBs per analysis over a network share.
SUMMARY_THRESHOLDS = np.round(np.arange(-1.0, 5.0 + 1e-9, 0.05), 2)


def local_moran_summary(layer) -> dict:
    """`pct_above_threshold`'s inputs for every feature at every
    `SUMMARY_THRESHOLDS` value, from a `(n_pixels, n_features)` layer:
    `counts[t, j]` = finite pixels of feature `j` strictly above threshold
    `t`, `n_finite[j]` = its finite pixels. Small enough to store in `.uns`.
    """
    layer = np.asarray(layer.toarray() if hasattr(layer, "toarray") else layer, dtype=float)
    # One row per feature, non-finite as NaN, which sorts last.
    by_feature = np.where(np.isfinite(layer), layer, np.nan).T.copy()
    by_feature.sort(axis=1)
    n_finite = np.isfinite(by_feature).sum(axis=1)
    counts = np.empty((len(SUMMARY_THRESHOLDS), by_feature.shape[0]), dtype=np.int32)
    for j, n in enumerate(n_finite):
        counts[:, j] = n - np.searchsorted(by_feature[j, :n], SUMMARY_THRESHOLDS, side="right")
    return {
        "thresholds": SUMMARY_THRESHOLDS.copy(),
        "counts": counts,
        "n_finite": n_finite.astype(np.int32),
    }


def summary_percentages(summary: dict) -> np.ndarray:
    """`(n_thresholds, n_features)` % of pixels above each threshold —
    `pct_above_threshold` at every grid value (0 with no finite pixels)."""
    n_finite = np.asarray(summary["n_finite"], dtype=float)
    counts = np.asarray(summary["counts"], dtype=float)
    return np.divide(100.0 * counts, n_finite, out=np.zeros_like(counts), where=n_finite > 0)


def snap_threshold(value: float) -> int:
    """Index of the `SUMMARY_THRESHOLDS` value nearest `value` (clamped)."""
    step = SUMMARY_THRESHOLDS[1] - SUMMARY_THRESHOLDS[0]
    i = int(round((float(value) - SUMMARY_THRESHOLDS[0]) / step))
    return min(max(i, 0), len(SUMMARY_THRESHOLDS) - 1)


def load_local_moran_summary(h5ad_path: str | Path) -> dict | None:
    """One sample's stored summary, or `None` without a local Moran's I
    layer. A file computed before the summary existed gets it built from
    the layer once (slow: the full layer is read) and written back."""
    with h5py.File(h5ad_path, "r") as f:
        summary = h5ad_io.read_uns(f, SUMMARY_UNS_KEY)
        if summary is not None and np.array_equal(summary["thresholds"], SUMMARY_THRESHOLDS):
            return summary
        if "layers" not in f or "localMoranI" not in f["layers"]:
            return None
        layer = ad.io.read_elem(f["layers"]["localMoranI"])
    summary = local_moran_summary(layer)
    del layer
    try:
        with h5ad_io.open_for_write(h5ad_path) as f:
            h5ad_io.write_uns(f, SUMMARY_UNS_KEY, summary)
    except OSError:
        logger.warning("%s: could not store the local Moran's I summary", h5ad_path)
    return summary


def _resolve_n_workers(requested: int | None, n_items: int) -> int:
    """Same clamping convention as `precursor_purity._resolve_n_workers`:
    `None`/**0**/negative means "one per CPU", always clamped to
    `[1, n_items]` (never more workers than there is work to hand out).
    """
    if not requested or requested < 1:
        requested = os.cpu_count() or 1
    return max(1, min(int(requested), max(1, n_items)))


def _chunk_indexed(
    indexed: list[tuple[int, float]], n_chunks: int
) -> list[list[tuple[int, float]]]:
    """`indexed` (`[(original_index, mz), ...]`) split into `n_chunks`
    contiguous, roughly-equal, non-empty slices — kept as `(index, mz)`
    pairs (not bare `mz` floats) so results can be reassembled in the
    original `var_names` order afterward without using a float as a
    dict key (two distinct features could in principle share an `mz`)."""
    n = len(indexed)
    chunk_size = -(-n // n_chunks)  # ceil division
    return [
        indexed[i : i + chunk_size]
        for i in range(0, n, chunk_size)
        if indexed[i : i + chunk_size]
    ]


#: Set once per worker process by `_init_local_moran_worker` — module
#: state, not a return value, since a `ProcessPoolExecutor` initializer's
#: job is exactly to populate state a later `submit()`ed task then reads.
_worker_adata: ad.AnnData | None = None


def _init_local_moran_worker(h5ad_path: str, n_neighs: int) -> None:
    """`ProcessPoolExecutor` initializer for `compute_local_moran_layer`'s
    parallel path — loads this sample's `.h5ad` and builds its neighbor
    graph once per worker *process* (not once per feature, and not passed
    through pickling per task).

    Each worker reloads the file itself rather than receiving an
    already-loaded `AnnData` via `initargs` — the same "workers reconstruct
    shared state themselves from a cheap identifier" pattern
    `core.annotation.annotate`'s spectral-library-loading initializer
    uses, portable across the fork/spawn process-start-method split
    (`spawn`, the default on Windows/macOS, starts a fresh interpreter
    with no access to the parent's already-loaded Python objects at all —
    only `fork`, Linux's default, could get away with sharing them, and
    even then only by accident of implementation, not by contract).
    """
    global _worker_adata
    adata = ad.read_h5ad(h5ad_path)
    _ensure_spatial_neighbors(adata, n_neighs)
    _worker_adata = adata


def _score_feature_batch(
    batch: list[tuple[int, float]], layer: str
) -> list[tuple[int, np.ndarray]]:
    """One parallel task: the full per-pixel local Moran's I array for a
    batch of features, against the already-loaded, already-graphed
    worker-global `adata` (see `_init_local_moran_worker`). Batched rather
    than one task per feature — each feature's own computation is cheap
    (a single sparse matvec), so per-task inter-process overhead would
    otherwise dominate.

    Returns `[(original_index, values), ...]`, matching `batch`'s own
    `(index, mz)` shape so the parent can reassemble results in
    `var_names` order regardless of which chunk finishes first. Returns
    the raw per-pixel array, not a threshold-collapsed summary — see
    `compute_local_moran_layer`'s own docstring for why.
    """
    assert _worker_adata is not None, "_init_local_moran_worker must run first"
    results = []
    for idx, mz in batch:
        values = local_moran_values(_worker_adata, mz, layer=layer)
        results.append((idx, values))
    return results


@log_call(source="h5ad_path")
def compute_local_moran_layer(
    h5ad_path: str | Path,
    layer: str = "TIC",
    n_workers: int | None = None,
) -> None:
    """Local Moran's I for every feature, every pixel, for one sample,
    written back in place as a new `.h5ad` layer — the
    `spatial_correlation.local_enabled` pipeline pass.

    Writes `adata.layers["localMoranI"]`: the same `(n_pixels,
    n_features)` shape as `.X`/`.layers["TIC"]`, one column per feature,
    each column `local_moran_values`'s per-pixel result for that feature.

    **Why a full layer, not a pre-thresholded summary (this pass' original
    v1 shape)**: a fixed threshold baked in at pipeline-run time means a
    poorly-chosen value (local Moran's I isn't bounded like a correlation
    coefficient — see `SpatialCorrelationConfig.local_threshold`) silently
    produces a useless "0 features above threshold" table with no way to
    recover without a full re-run. Storing the raw per-pixel values instead
    means the GUI can compute "% of pixels above X" for *any* `X` the user
    picks, instantly, by reading straight from this layer — see
    `pct_above_threshold` and `LocalMoranSummaryModel`
    (`gui/utils/local_moran_summary_model.py`). The threshold becomes a
    pure GUI/display concern; `SpatialCorrelationConfig.local_threshold`
    now only seeds the GUI's *initial* value when a page first opens, not
    a value this function ever reads.

    Independent of `compute_spatial_autocorr`/`spatial_correlation.enabled`
    — callable (and useful) whether or not the global stage also ran; see
    `local_moran_values`'s own docstring for why local Moran's I doesn't
    need the global pass's neighbor graph to already exist. If the global
    stage *did* run first and its neighbor graph is already on disk, this
    reuses it instead of rebuilding — see `local_moran_values`.

    **Parallelism**: unlike `compute_spatial_autocorr` (which hands
    `n_jobs` straight to squidpy's own internal joblib-based parallel
    scoring), this hand-rolled computation has no such built-in mechanism,
    so it's parallelized here directly, one `concurrent.futures.ProcessPoolExecutor`
    worker process per chunk of features (`n_workers`, same "None/0 means
    one per CPU, clamped to the amount of actual work" convention as
    `precursor_purity.n_workers`/`h5ad.n_workers`) — each worker loads its
    own copy of `h5ad_path` and builds the neighbor graph once, then scores
    its whole batch of features against it (see `_init_local_moran_worker`/
    `_score_feature_batch`). `n_workers == 1` (or a single feature) takes
    the plain serial path instead, with no process pool at all.

    Args:
        h5ad_path: Path to one sample's `.h5ad`. Overwritten in place.
        layer: Which `.h5ad` layer's intensities to score — same
            convention as `compute_spatial_autocorr`.
        n_workers: Worker processes to score features in parallel with.
            `None`/**0** uses `os.cpu_count()`; **1** forces the serial
            path. Capped at the number of features in the sample.
    """
    h5ad_path = Path(h5ad_path)
    adata = ad.read_h5ad(h5ad_path)
    mzs = adata.var["mz"].to_numpy(dtype=float).tolist()
    indexed = list(enumerate(mzs))
    n_pixels = adata.n_obs

    workers = _resolve_n_workers(n_workers, len(indexed))

    if workers == 1 or len(indexed) <= 1:
        _ensure_spatial_neighbors(adata)
        results = [
            (idx, local_moran_values(adata, mz, layer=layer)) for idx, mz in indexed
        ]
    else:
        chunks = _chunk_indexed(indexed, workers)
        logger.info(
            "%s: scoring %d features' local Moran's I on %d worker processes",
            h5ad_path,
            len(indexed),
            len(chunks),
        )
        results = []
        with ProcessPoolExecutor(
            max_workers=len(chunks),
            initializer=_init_local_moran_worker,
            initargs=(str(h5ad_path), _N_NEIGHS),
        ) as executor:
            futures = [
                executor.submit(_score_feature_batch, chunk, layer) for chunk in chunks
            ]
            for future in futures:
                results.extend(future.result())

    results.sort(key=lambda r: r[0])
    local_moran_layer = np.empty((n_pixels, len(indexed)), dtype=float)
    for idx, values in results:
        local_moran_layer[:, idx] = values

    adata.layers["localMoranI"] = local_moran_layer
    adata.uns[SUMMARY_UNS_KEY] = local_moran_summary(local_moran_layer)
    with h5ad_io.write_lock:
        adata.write_h5ad(h5ad_path)
