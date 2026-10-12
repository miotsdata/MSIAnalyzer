from __future__ import annotations

import logging
from pathlib import Path


from msianalyzer.core.utils.logging_utils import log_call
from msianalyzer.core.utils.lazy_import import lazy_module

ad = lazy_module("anndata")

logger = logging.getLogger(__name__)


@log_call(source="h5ad_path")
def compute_spatial_autocorr(
    h5ad_path: str | Path, layer: str = "TIC", n_jobs: int | None = None
) -> None:
    """Compute per-feature Moran's I for one sample and write it back in place.

    Reads `h5ad_path`, builds a spatial neighbor graph over its pixels
    (`squidpy.gr.spatial_neighbors_grid` — the pixel raster from
    `create_spatial_adata` is an exact regular lattice, so the grid-specific
    builder applies directly; `n_neighs=4` for rook adjacency, matching a
    square pixel grid rather than the hexagonal Visium layout the library
    default (`n_neighs=6`) assumes), then runs
    `squidpy.gr.spatial_autocorr(mode="moran")` for every feature. Results
    land in `adata.uns["moranI"]` (squidpy's own convention — a DataFrame
    indexed by `var_names` with an `I` score column, plus p-values), and
    the enriched object is written back to `h5ad_path`, overwriting it.

    Args:
        h5ad_path: Path to one sample's `.h5ad` (as built by
            `create_spatial_adata` / normalized by `run_normalization`).
            Overwritten in place with the Moran's I results added.
        layer: Which `.h5ad` layer's intensities to score. Falls back to
            raw `.X` when the layer isn't present (e.g.
            `normalization.enabled` was `False`).
        n_jobs: Passed straight through to `spatial_autocorr`'s own
            `n_jobs` — parallelizes scoring across this sample's features
            (squidpy's own joblib-backed parallelism), not across samples;
            callers still process one sample at a time.
    """
    # Deferred: squidpy drags in scanpy/spatialdata/dask/numba (~1.4 s),
    # far too much to pay at GUI startup for a Run-time-only step.
    import squidpy as sq

    h5ad_path = Path(h5ad_path)
    adata = ad.read_h5ad(h5ad_path)

    use_layer = layer if layer in adata.layers else None
    if layer not in adata.layers:
        logger.warning(
            "%s: layer %r not found, falling back to raw .X for Moran's I",
            h5ad_path,
            layer,
        )

    sq.gr.spatial_neighbors_grid(adata, n_neighs=4)
    sq.gr.spatial_autocorr(
        adata,
        mode="moran",
        genes=list(adata.var_names),
        layer=use_layer,
        n_jobs=n_jobs,
        show_progress_bar=False,
    )

    adata.write_h5ad(h5ad_path)
