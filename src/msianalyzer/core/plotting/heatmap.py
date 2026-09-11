import anndata as ad
import matplotlib
import numpy as np


def _feature_column_index(adata: ad.AnnData, mz: float) -> int:
    """The `.var`/`.X` column index whose `mz` is closest to `mz`.

    `features.mz` (SQLite) and `adata.var["mz"]` both originate from the
    same aligned m/z set, but a SQLite REAL round-trip isn't guaranteed
    bit-exact, so this matches nearest rather than requiring equality.
    """
    mz_values = adata.var["mz"].to_numpy(dtype=float)
    return int(np.argmin(np.abs(mz_values - mz)))


def _layer_column(adata: ad.AnnData, layer: str, col_idx: int) -> np.ndarray:
    matrix = adata.layers[layer] if layer in adata.layers else adata.X
    column = matrix[:, col_idx]
    if hasattr(column, "toarray"):
        column = column.toarray()
    return np.asarray(column).reshape(-1).astype(float)


def feature_value_range(
    adata: ad.AnnData, mz: float, layer: str = "TIC"
) -> tuple[float, float]:
    """The (min, max) of one feature's per-pixel values — what
    `render_feature_heatmap` uses for `vmin`/`vmax` when autoscaling.

    Exposed separately so the GUI can show what autoscale actually used
    (and seed a manual vmin/vmax from it) without duplicating the
    column-lookup logic, and without rendering an image just to read two
    numbers off it.

    Args:
        adata: One sample's AnnData, already loaded (`ad.read_h5ad`).
        mz: The feature's consensus m/z — matched to the nearest `.var["mz"]`.
        layer: `"raw"` or `"TIC"` — which `adata.layers` entry to read.

    Returns:
        `(min, max)` of the finite values in that column, or `(0.0, 1.0)`
        if there are none.
    """
    col_idx = _feature_column_index(adata, mz)
    values = _layer_column(adata, layer, col_idx)
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return 0.0, 1.0
    return float(finite.min()), float(finite.max())


def render_feature_heatmap(
    adata: ad.AnnData,
    mz: float,
    layer: str = "TIC",
    colormap: str = "viridis",
    vmin: float | None = None,
    vmax: float | None = None,
) -> np.ndarray:
    """One feature's per-pixel intensity as an RGBA `uint8` raster.

    Reconstructs the pixel grid from `adata.obsm["spatial"]` (x, y columns,
    in that order — see `create_spatial_adata`) by mapping each observation
    onto the grid formed by the sorted set of unique x and unique y values
    (i.e. not by rounding/binning — every distinct coordinate gets its own
    row/column). Pixels the grid doesn't cover (missing/irregular spatial
    layouts) are rendered pure black, matching the black-background,
    imshow-style convention this section follows.

    Args:
        adata: One sample's AnnData, already loaded (`ad.read_h5ad`).
        mz: The feature's consensus m/z — matched to the nearest `.var["mz"]`.
        layer: `"raw"` or `"TIC"` — which `adata.layers` entry to read.
        colormap: Any registered Matplotlib colormap name.
        vmin: Lower color-scale bound. `None` autoscales to the data's min.
        vmax: Upper color-scale bound. `None` autoscales to the data's max.

    Returns:
        An `(n_unique_y, n_unique_x, 4)` `uint8` array (RGBA), row 0 at the
        smallest y (no vertical flip — the QML `Image` element flips as
        needed for display).
    """
    col_idx = _feature_column_index(adata, mz)
    values = _layer_column(adata, layer, col_idx)

    xy = np.asarray(adata.obsm["spatial"], dtype=float)
    x, y = xy[:, 0], xy[:, 1]

    unique_x = np.unique(x)
    unique_y = np.unique(y)
    x_index = np.searchsorted(unique_x, x)
    y_index = np.searchsorted(unique_y, y)

    grid = np.full((unique_y.size, unique_x.size), np.nan, dtype=float)
    grid[y_index, x_index] = values

    finite = np.isfinite(grid)
    if vmin is None:
        vmin = float(np.nanmin(grid)) if finite.any() else 0.0
    if vmax is None:
        vmax = float(np.nanmax(grid)) if finite.any() else 1.0
    if vmax <= vmin:
        vmax = vmin + 1.0

    norm = matplotlib.colors.Normalize(vmin=vmin, vmax=vmax, clip=True)
    cmap = matplotlib.colormaps[colormap]
    rgba = cmap(norm(np.where(finite, grid, vmin)))
    rgba_uint8 = (rgba * 255).astype(np.uint8)
    rgba_uint8[~finite] = (0, 0, 0, 255)

    return np.ascontiguousarray(rgba_uint8)
