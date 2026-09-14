"""
roi.py
Per-pixel region-of-interest (ROI) polygons for the GUI's "ROI Design"
window: rasterizing a drawn polygon into a per-pixel boolean mask, and
reading/writing that mask (plus the polygon itself, for redrawing) into a
sample's own `.h5ad` — and, best-effort, into the analysis' `merged.h5ad`
when one exists.

An ROI is defined once per sample (its border polygon lives in that
sample's own pixel-grid-index space, see `heatmap.pixel_grid_indices` — the
same coordinate space the spatial heatmap raster renders in, 1 image pixel
== 1 grid cell). Only its *name* and *color* are meant to be shared/kept
consistent across every sample it's drawn on (see
`core/analysis_db.py`'s `rois` catalog table) — the geometry itself is
always independent per sample.

ROI saves are a direct interactive GUI action, not a pipeline step — unlike
`core/utils/tic_normalization.py`, nothing here touches
`analysis_db.log_command`/`is_command_already_run` bookkeeping.
"""

from __future__ import annotations

import logging
from pathlib import Path

import anndata as ad
import numpy as np
from matplotlib.path import Path as MplPath

from msianalyzer.core.utils.logging_utils import log_call

logger = logging.getLogger(__name__)

# Imported lazily inside functions that need it (pixel_grid_indices) to
# avoid a module-level import cycle risk between plotting submodules; see
# the import inside polygon_pixel_mask below.


def roi_obs_column(name: str) -> str:
    """The `adata.obs` boolean column name one ROI's membership is stored
    under. Factored out so every caller (core, bridge, tests) formats this
    identically rather than hand-writing `f"roi_{name}"` in several places.
    """
    return f"roi_{name}"


def polygon_pixel_mask(adata: ad.AnnData, vertices: list) -> np.ndarray:
    """Boolean per-pixel membership (`adata.obs` row order) for a polygon
    given in the same pixel-grid-index coordinate space
    `heatmap.pixel_grid_indices` (and hence the heatmap raster) uses —
    `vertices` are `[col, row]` pairs, `col`/`row` being indices into the
    sorted unique-x/unique-y grid, NOT raw `obsm["spatial"]` coordinates.
    This is what guarantees a polygon drawn on the `ZoomableImage` raster
    (1 image pixel == 1 grid cell) lands on exactly the right `obs` rows.

    Tests each pixel's grid-cell *center* (`col + 0.5, row + 0.5`), not its
    corner — a pixel whose center falls inside the drawn border counts as
    "in", matching what a user visually tracing an outline around a set of
    pixels expects (whole-pixel in/out, not partial-coverage antialiasing).

    Args:
        adata: One sample's AnnData, already loaded (`ad.read_h5ad`).
        vertices: `[[col, row], ...]`, at least 3 points, in
            `pixel_grid_indices` grid-index space.

    Returns:
        A boolean array, one entry per `adata.obs` row (True = inside).
        All-False if `vertices` has fewer than 3 points.
    """
    from msianalyzer.core.plotting.heatmap import pixel_grid_indices

    if len(vertices) < 3:
        return np.zeros(adata.n_obs, dtype=bool)

    x_index, y_index, _unique_x, _unique_y = pixel_grid_indices(adata)
    points = np.column_stack([x_index + 0.5, y_index + 0.5])
    path = MplPath(np.asarray(vertices, dtype=float))
    return path.contains_points(points)


@log_call(source="h5ad_path")
def save_roi_to_sample(
    h5ad_path: Path | str, name: str, color: str, vertices: list,
) -> int:
    """Add/overwrite one ROI on a sample's `.h5ad`: the per-pixel boolean
    membership column (`adata.obs[roi_obs_column(name)]`) and the border
    polygon (`adata.uns["rois"][name]`), then write the file back to the
    SAME path — same read/mutate/write-back shape as
    `tic_normalization.run_tic_normalization`, but a single interactive
    save rather than a pipeline command.

    Args:
        h5ad_path: The sample's own `.h5ad` file, overwritten in place.
        name: ROI name — color consistency across samples is the caller's
            responsibility (see `AnalysisBridge.saveRoi`'s catalog lookup);
            this function does not touch the analysis-wide catalog.
        color: `"#rrggbb"`, stored alongside the geometry so the h5ad is
            self-describing even without the analysis DB.
        vertices: `[[col, row], ...]`, >= 3 points, in
            `pixel_grid_indices` grid-index space (see `polygon_pixel_mask`).

    Returns:
        The number of pixels (`True` count) the saved polygon covers.

    Raises:
        ValueError: `vertices` has fewer than 3 points.
    """
    if len(vertices) < 3:
        raise ValueError(f"ROI {name!r} needs at least 3 vertices, got {len(vertices)}")

    adata = ad.read_h5ad(h5ad_path)
    mask = polygon_pixel_mask(adata, vertices)
    adata.obs[roi_obs_column(name)] = mask

    rois = dict(adata.uns.get("rois") or {})
    rois[name] = {
        "color": color,
        "vertices": [[float(c), float(r)] for c, r in vertices],
    }
    adata.uns["rois"] = rois

    adata.write_h5ad(h5ad_path)
    return int(mask.sum())


@log_call(source="h5ad_path")
def delete_roi_from_sample(h5ad_path: Path | str, name: str) -> bool:
    """Remove one ROI's obs column + uns entry from a sample's `.h5ad`,
    writing back to the same path. A no-op (returns False) if the sample
    never had this ROI — deleting from the catalog can validly race ahead
    of, or entirely skip, any given sample's own h5ad.

    Returns:
        True if anything was actually removed and the file rewritten.
    """
    adata = ad.read_h5ad(h5ad_path)
    column = roi_obs_column(name)
    rois = dict(adata.uns.get("rois") or {})

    had_column = column in adata.obs.columns
    had_uns = name in rois

    if not had_column and not had_uns:
        return False

    if had_column:
        adata.obs = adata.obs.drop(columns=[column])
    if had_uns:
        del rois[name]
        adata.uns["rois"] = rois

    adata.write_h5ad(h5ad_path)
    return True


def load_sample_rois(h5ad_path: Path | str) -> dict:
    """`adata.uns["rois"]` for one sample, read-only — the border-polygon
    dict `RoiOverlay.qml` needs to redraw every saved ROI on top of that
    sample's heatmap.

    Args:
        h5ad_path: The sample's `.h5ad`.

    Returns:
        `{roi_name: {"color": "#rrggbb", "vertices": [[col, row], ...]}}`,
        `vertices` always a plain (not numpy) list of `[float, float]`
        pairs — h5ad round-trips a `uns` list-of-lists as a numpy array,
        which downstream QML consumers (RoiOverlay.qml's `.map()` calls)
        can't use directly. `{}` if the sample has no ROIs (or predates
        this feature).
    """
    adata = ad.read_h5ad(h5ad_path)
    rois = dict(adata.uns.get("rois") or {})
    return {
        name: {
            "color": info["color"],
            "vertices": [[float(c), float(r)] for c, r in info["vertices"]],
        }
        for name, info in rois.items()
    }


# ---------------------------------------------------------------------------
# merged.h5ad sync — best effort, no-op if the file doesn't exist
# ---------------------------------------------------------------------------


@log_call(source="merged_path")
def sync_roi_to_merged(
    merged_path: Path | str,
    sample_name: str,
    name: str,
    color: str,
    vertices: list,
    mask: np.ndarray,
) -> bool:
    """Patch one sample's ROI into an existing `merged.h5ad`
    (`core/utils/tic_normalization.py::run_tic_normalization`'s
    `ad.concat(..., label="sample", ...)` output) in place, WITHOUT
    re-running the concat — `ad.concat`'s `uns_merge="unique"` would
    silently drop `uns["rois"]` entirely the moment it differs across
    samples (which it always will, by design), and re-concatenating every
    sample's h5ad on every single interactive ROI save would be wasteful.

    `mask` is the same boolean array `save_roi_to_sample` already computed
    for this sample (in that sample's own `adata.obs` row order) — reused
    here rather than recomputed, and lines up positionally with
    `merged.obs["sample"] == sample_name`'s row block since `ad.concat`
    preserves each input's internal row order.

    Args:
        merged_path: The analysis' `merged.h5ad` (`Path(analysis_db_path)
            .parent / "merged.h5ad"`).
        sample_name: Which sample's rows to update (`merged.obs["sample"]`
            values, as set by `run_tic_normalization`'s `label="sample"`).
        name: The ROI's name.
        color: `"#rrggbb"`.
        vertices: This sample's polygon, `[[col, row], ...]`.
        mask: This sample's per-pixel membership, in that sample's own obs
            row order (same length as its row block in `merged`).

    Returns:
        True if the file was patched; False (no-op) if `merged_path`
        doesn't exist — TIC normalization may be disabled, or the analysis
        predates this feature. The per-sample `.h5ad` write is always the
        authoritative one and is unaffected by this returning False.
    """
    merged_path = Path(merged_path)
    if not merged_path.exists():
        return False

    merged = ad.read_h5ad(merged_path)
    row_mask = (merged.obs["sample"] == sample_name).to_numpy()

    column = roi_obs_column(name)
    if column not in merged.obs.columns:
        merged.obs[column] = False
    merged.obs.loc[row_mask, column] = mask

    rois = dict(merged.uns.get("rois") or {})
    entry = dict(rois.get(name) or {"color": color, "samples": {}})
    entry["color"] = color
    samples = dict(entry.get("samples") or {})
    samples[sample_name] = {"vertices": [[float(c), float(r)] for c, r in vertices]}
    entry["samples"] = samples
    rois[name] = entry
    merged.uns["rois"] = rois

    merged.write_h5ad(merged_path)
    return True


@log_call(source="merged_path")
def sync_roi_deletion_to_merged(
    merged_path: Path | str, sample_name: str, name: str,
) -> bool:
    """The deletion counterpart to `sync_roi_to_merged` — clears
    `sample_name`'s rows for this ROI back to `False` and drops its entry
    from `uns["rois"][name]["samples"]`. If no sample is left registered
    under `name` afterward, the whole `uns["rois"][name]` entry and the
    `obs` column are dropped too (no sample has this ROI anymore).

    Returns:
        True if the file was patched; False (no-op) if `merged_path`
        doesn't exist, or the ROI wasn't present there for this sample.
    """
    merged_path = Path(merged_path)
    if not merged_path.exists():
        return False

    merged = ad.read_h5ad(merged_path)
    rois = dict(merged.uns.get("rois") or {})
    entry = rois.get(name)
    column = roi_obs_column(name)

    had_sample = bool(entry and sample_name in (entry.get("samples") or {}))
    if not had_sample and column not in merged.obs.columns:
        return False

    if column in merged.obs.columns:
        row_mask = (merged.obs["sample"] == sample_name).to_numpy()
        merged.obs.loc[row_mask, column] = False

    if entry is not None:
        samples = dict(entry.get("samples") or {})
        samples.pop(sample_name, None)
        if samples:
            entry["samples"] = samples
            rois[name] = entry
        else:
            rois.pop(name, None)
            if column in merged.obs.columns:
                merged.obs = merged.obs.drop(columns=[column])
        merged.uns["rois"] = rois

    merged.write_h5ad(merged_path)
    return True
