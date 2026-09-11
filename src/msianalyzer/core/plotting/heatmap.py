import anndata as ad
import matplotlib
import numpy as np
import pandas as pd

# x/y: obsm["spatial"]'s own coordinates, not a value to overlay on it.
# scan_id: the raw per-pixel scan-id join string (one distinct value per
# pixel after scan averaging) — as a "discrete" column its category count
# is effectively the pixel count, and render_obs_categories_heatmap colors
# by category position (O(categories) work per pixel), so this alone was
# slow enough to freeze the UI ("quite slow (and freezes)"). polarity: a
# single constant value per sample, nothing to see spatially.
_EXCLUDED_OBS_COLUMNS = {"x", "y", "scan_id", "polarity"}
# Fixed qualitative palette for discrete `obs` columns — categories aren't
# ordered, so a continuous colormap/vmin/vmax makes no sense for them;
# every tile and the legend index into this one palette by the same
# category order so colors agree across tiles.
_CATEGORY_PALETTE = "tab20"


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


def _reconstruct_grid(adata: ad.AnnData, values: np.ndarray) -> np.ndarray:
    """Map per-pixel `values` (in `adata.obs` row order) onto the grid
    formed by the sorted set of unique x and unique y values in
    `adata.obsm["spatial"]` — every distinct coordinate gets its own
    row/column, not rounding/binning. Cells the grid doesn't cover
    (missing/irregular spatial layouts) are `NaN`.

    Returns an `(n_unique_y, n_unique_x)` array, row 0 at the smallest y
    (no vertical flip — the QML `Image` element flips as needed).
    """
    xy = np.asarray(adata.obsm["spatial"], dtype=float)
    x, y = xy[:, 0], xy[:, 1]

    unique_x = np.unique(x)
    unique_y = np.unique(y)
    x_index = np.searchsorted(unique_x, x)
    y_index = np.searchsorted(unique_y, y)

    grid = np.full((unique_y.size, unique_x.size), np.nan, dtype=float)
    grid[y_index, x_index] = values
    return grid


def _colorize_grid(
    grid: np.ndarray,
    colormap: str,
    vmin: float | None,
    vmax: float | None,
) -> np.ndarray:
    """A `_reconstruct_grid` result as an RGBA `uint8` raster. `NaN` cells
    render pure black, matching the black-background, imshow-style
    convention this section follows."""
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


def render_feature_heatmap(
    adata: ad.AnnData,
    mz: float,
    layer: str = "TIC",
    colormap: str = "viridis",
    vmin: float | None = None,
    vmax: float | None = None,
) -> np.ndarray:
    """One feature's per-pixel intensity as an RGBA `uint8` raster.

    See `_reconstruct_grid` for how the pixel grid is built from
    `adata.obsm["spatial"]`.

    Args:
        adata: One sample's AnnData, already loaded (`ad.read_h5ad`).
        mz: The feature's consensus m/z — matched to the nearest `.var["mz"]`.
        layer: `"raw"` or `"TIC"` — which `adata.layers` entry to read.
        colormap: Any registered Matplotlib colormap name.
        vmin: Lower color-scale bound. `None` autoscales to the data's min.
        vmax: Upper color-scale bound. `None` autoscales to the data's max.

    Returns:
        An `(n_unique_y, n_unique_x, 4)` `uint8` array (RGBA).
    """
    col_idx = _feature_column_index(adata, mz)
    values = _layer_column(adata, layer, col_idx)
    grid = _reconstruct_grid(adata, values)
    return _colorize_grid(grid, colormap, vmin, vmax)


def is_numeric_obs_column(adata: ad.AnnData, obs_column: str) -> bool:
    series = adata.obs[obs_column]
    return bool(
        pd.api.types.is_numeric_dtype(series)
        and not pd.api.types.is_bool_dtype(series)
    )


def list_obs_columns(adata: ad.AnnData) -> list[dict]:
    """`adata.obs` columns Visual Inspection can overlay, beyond the m/z
    features themselves — e.g. `tic`, `rt`.

    Args:
        adata: One sample's AnnData, already loaded.

    Returns:
        `[{"name": ..., "numeric": bool}, ...]`, in `adata.obs`'s column
        order, excluding `_EXCLUDED_OBS_COLUMNS` (`x`/`y`/`scan_id`/
        `polarity` — not useful to overlay, and `scan_id` in particular is
        slow: it's effectively unique per pixel, and the discrete render
        path is O(categories) per pixel). `numeric` is `True` for numeric
        dtypes (int/float, excluding `bool`) — rendered as a color scale;
        `False` (object/category/bool) is the discrete case, rendered as a
        fixed-palette legend instead.
    """
    return [
        {"name": name, "numeric": is_numeric_obs_column(adata, name)}
        for name in adata.obs.columns
        if name not in _EXCLUDED_OBS_COLUMNS
    ]


def obs_value_range(adata: ad.AnnData, obs_column: str) -> tuple[float, float]:
    """The (min, max) of one numeric `obs` column's per-pixel values — the
    `obs`-column analogue of `feature_value_range`.

    Args:
        adata: One sample's AnnData, already loaded.
        obs_column: A numeric column in `adata.obs`.

    Returns:
        `(min, max)` of the finite values, or `(0.0, 1.0)` if there are none.
    """
    values = adata.obs[obs_column].to_numpy(dtype=float)
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return 0.0, 1.0
    return float(finite.min()), float(finite.max())


def render_obs_heatmap(
    adata: ad.AnnData,
    obs_column: str,
    colormap: str = "viridis",
    vmin: float | None = None,
    vmax: float | None = None,
) -> np.ndarray:
    """One numeric `obs` column's per-pixel value as an RGBA `uint8`
    raster — the `obs`-column analogue of `render_feature_heatmap`.

    Args:
        adata: One sample's AnnData, already loaded.
        obs_column: A numeric column in `adata.obs`.
        colormap: Any registered Matplotlib colormap name.
        vmin: Lower color-scale bound. `None` autoscales to the data's min.
        vmax: Upper color-scale bound. `None` autoscales to the data's max.

    Returns:
        An `(n_unique_y, n_unique_x, 4)` `uint8` array (RGBA).
    """
    values = adata.obs[obs_column].to_numpy(dtype=float)
    grid = _reconstruct_grid(adata, values)
    return _colorize_grid(grid, colormap, vmin, vmax)


def obs_categories(adata: ad.AnnData, obs_column: str) -> list[str]:
    """Distinct values of one discrete `obs` column, as strings, sorted.

    Args:
        adata: One sample's AnnData, already loaded.
        obs_column: A discrete (non-numeric) column in `adata.obs`.

    Returns:
        Sorted, de-duplicated `str(value)` for every non-null entry.
    """
    series = adata.obs[obs_column]
    return sorted({str(v) for v in series.dropna().unique()})


def category_color(index: int) -> str:
    """The hex color one category renders as, by its position in a
    caller-supplied category order — the same order `getObsCategories`
    (combined across every visible sample) and `render_obs_categories_heatmap`
    (one sample's raster) must both use, so a category means the same
    color everywhere it's drawn."""
    r, g, b, _a = _category_rgba(index)
    return "#{:02x}{:02x}{:02x}".format(r, g, b)


def _category_rgba(index: int) -> tuple[int, int, int, int]:
    cmap = matplotlib.colormaps[_CATEGORY_PALETTE]
    r, g, b, a = cmap(index % cmap.N)
    return int(r * 255), int(g * 255), int(b * 255), int(a * 255)


def render_obs_categories_heatmap(
    adata: ad.AnnData, obs_column: str, categories: list[str]
) -> np.ndarray:
    """One discrete `obs` column's per-pixel category as an RGBA `uint8`
    raster — each category colored by its position in `categories`
    (`category_color`), not by its own local order in this sample, so
    every sample's tile (and a legend built from `categories`) agree on
    what color means which category.

    Args:
        adata: One sample's AnnData, already loaded.
        obs_column: A discrete (non-numeric) column in `adata.obs`.
        categories: The full, ordered category list to color by — normally
            the union across every visible sample (see `getObsCategories`),
            so a category missing from this particular sample still isn't
            reused for a different one here.

    Returns:
        An `(n_unique_y, n_unique_x, 4)` `uint8` array (RGBA). Pixels whose
        value isn't in `categories` (missing/null, or outside the grid)
        render pure black.
    """
    series = adata.obs[obs_column]
    values = series.astype(str).to_numpy()
    is_null = series.isna().to_numpy()

    codes = np.full(values.shape, -1.0, dtype=float)
    for i, cat in enumerate(categories):
        codes[(values == cat) & (~is_null)] = i

    grid = _reconstruct_grid(adata, codes)

    rgba = np.zeros((*grid.shape, 4), dtype=np.uint8)
    rgba[..., 3] = 255
    for i in range(len(categories)):
        rgba[grid == i] = _category_rgba(i)

    return np.ascontiguousarray(rgba)
