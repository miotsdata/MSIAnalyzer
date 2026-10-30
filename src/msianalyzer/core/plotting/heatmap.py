from __future__ import annotations

from collections.abc import Collection
import matplotlib
import matplotlib.cm
import matplotlib.figure
import matplotlib.patches
import matplotlib.ticker
import numpy as np
import pandas as pd

from msianalyzer.core.plotting.feature_overlay import Channel, composite, parse_channels
from msianalyzer.core.utils.lazy_import import lazy_module

ad = lazy_module("anndata")

# x/y: obsm["spatial"]'s own coordinates, not a value to overlay on it.
# scan_id: the raw per-pixel scan-id join string (one distinct value per
# pixel after scan averaging) — as a "discrete" column its category count
# is effectively the pixel count, and render_obs_categories_heatmap colors
# by category position (O(categories) work per pixel), so this alone was
# slow enough to freeze the UI ("quite slow (and freezes)"). polarity: a
# single constant value per sample, nothing to see spatially.
# sample / source_obs_name: bookkeeping columns of an imported analyzed
# dataset (ADR 49) — one constant value per sample file, and the user's
# own ~unique-per-pixel obs name respectively (same problem as scan_id).
_EXCLUDED_OBS_COLUMNS = {"x", "y", "scan_id", "polarity", "sample", "source_obs_name"}
#: `adata.uns` key holding a sample's *full* pixel-grid axes
#: (`{"x": unique_x, "y": unique_y}`), written by
#: `core.imported_dataset` into every imported per-sample file (ADR 49).
#: A notebook QC step can drop a whole edge row/column of pixels; the
#: grid rebuilt from the *remaining* coordinates would then shrink, and
#: every ROI polygon / H&E registration (both stored in grid-index space)
#: would silently shift. Pipeline files never have it and are unaffected.
GRID_UNS_KEY = "msianalyzer_grid"
#: How far (ppm) a requested m/z may be from the nearest `.var["mz"]` for
#: a *rendered* feature of an imported dataset to count as that feature.
#: Pipeline files always contain every feature (and keep plain
#: nearest-match), but an imported notebook-filtered dataset may not —
#: nearest-match alone would then silently show a neighbor.
_RENDER_MZ_TOLERANCE_PPM = 10.0
# Fixed qualitative palette for discrete `obs` columns — categories aren't
# ordered, so a continuous colormap/vmin/vmax makes no sense for them;
# every tile and the legend index into this one palette by the same
# category order so colors agree across tiles.
_CATEGORY_PALETTE = "tab20"
#: Reserved `layer` value (never a real `.h5ad` layer name) meaning
#: "render this feature's per-pixel local Moran's I instead of its raw
#: intensity" — see `render_heatmap_by_target`'s dispatch and
#: `core.spatial.local_moran`.
_LOCAL_MORAN_LAYER = "localMoran"


def _feature_column_index(adata: ad.AnnData, mz: float) -> int:
    """The `.var`/`.X` column index whose `mz` is closest to `mz`.

    `features.mz` (SQLite) and `adata.var["mz"]` both originate from the
    same aligned m/z set, but a SQLite REAL round-trip isn't guaranteed
    bit-exact, so this matches nearest rather than requiring equality.
    """
    mz_values = adata.var["mz"].to_numpy(dtype=float)
    return int(np.argmin(np.abs(mz_values - mz)))


def _rendered_feature_values(adata: ad.AnnData, mz: float, layer: str) -> np.ndarray:
    """One feature's per-pixel values for *display* — like
    `_layer_column(adata, layer, _feature_column_index(adata, mz))`, but,
    for an imported dataset only (it carries `uns[GRID_UNS_KEY]`),
    all-`NaN` (renders black) when no `.var["mz"]` is within
    `_RENDER_MZ_TOLERANCE_PPM` of `mz` — a feature filtered out in the
    notebook — instead of showing whichever neighbor is nearest.
    """
    col_idx = _feature_column_index(adata, mz)
    if GRID_UNS_KEY not in adata.uns:
        return _layer_column(adata, layer, col_idx)
    nearest = float(adata.var["mz"].to_numpy(dtype=float)[col_idx])
    if abs(nearest - mz) > abs(mz) * _RENDER_MZ_TOLERANCE_PPM * 1e-6:
        return np.full(adata.n_obs, np.nan)
    return _layer_column(adata, layer, col_idx)


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
    values = _rendered_feature_values(adata, mz, layer)
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return 0.0, 1.0
    return float(finite.min()), float(finite.max())


def pixel_grid_indices(
    adata: ad.AnnData,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Per-pixel (x_index, y_index) into the grid formed by the sorted set
    of unique x/y values in `adata.obsm["spatial"]` — the same grid
    `_reconstruct_grid` paints values onto, exposed standalone so ROI
    polygon/mask code (`core/plotting/roi.py`) can test membership against
    exactly the coordinate space the heatmap raster uses (1 image pixel ==
    1 spatial pixel, no vertical flip — row 0 is the smallest y).

    Args:
        adata: One sample's AnnData, already loaded.

    When `adata.uns[GRID_UNS_KEY]` is present (an imported, possibly
    pixel-filtered dataset — ADR 49), its stored full-grid axes are used
    instead of the unique values actually present, so the grid keeps the
    original sample's exact shape and indices.

    Returns:
        `(x_index, y_index, unique_x, unique_y)` — `x_index`/`y_index` are
        parallel to `adata.obs` row order (same length, same order):
        `x_index[i]`/`y_index[i]` is pixel *i*'s column/row in the grid.
        `unique_x`/`unique_y` are ascending, `searchsorted`-ready.
    """
    xy = np.asarray(adata.obsm["spatial"], dtype=float)
    x, y = xy[:, 0], xy[:, 1]

    grid_axes = adata.uns.get(GRID_UNS_KEY)
    if grid_axes is not None:
        unique_x = np.asarray(grid_axes["x"], dtype=float)
        unique_y = np.asarray(grid_axes["y"], dtype=float)
    else:
        unique_x = np.unique(x)
        unique_y = np.unique(y)
    x_index = np.searchsorted(unique_x, x)
    y_index = np.searchsorted(unique_y, y)
    return x_index, y_index, unique_x, unique_y


def _reconstruct_grid(adata: ad.AnnData, values: np.ndarray) -> np.ndarray:
    """Map per-pixel `values` (in `adata.obs` row order) onto the grid
    formed by the sorted set of unique x and unique y values in
    `adata.obsm["spatial"]` — every distinct coordinate gets its own
    row/column, not rounding/binning. Cells the grid doesn't cover
    (missing/irregular spatial layouts) are `NaN`.

    Returns an `(n_unique_y, n_unique_x)` array, row 0 at the smallest y
    (no vertical flip — the QML `Image` element flips as needed).
    """
    x_index, y_index, unique_x, unique_y = pixel_grid_indices(adata)
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


def render_colorbar(colormap: str, width: int = 256, height: int = 16) -> np.ndarray:
    """A flat gradient strip for one colormap, low->high left-to-right —
    the Visual Inspection color-scale legend ("min"/"max" labels either
    side, see `HeatmapControlsPanel.qml`). Same colormap resolution
    (`matplotlib.colormaps[colormap]`) and RGBA `uint8` construction as
    `_colorize_grid`, so the legend always matches what the tiles
    themselves actually render with — no sample/AnnData involved, unlike
    every other render function here.

    Args:
        colormap: Any registered Matplotlib colormap name.
        width: Strip width in pixels.
        height: Strip height in pixels — the gradient is uniform vertically,
            just tiled to this many rows.

    Returns:
        An `(height, width, 4)` `uint8` array (RGBA).
    """
    cmap = matplotlib.colormaps[colormap]
    row = cmap(np.linspace(0.0, 1.0, width))
    rgba_uint8 = (row * 255).astype(np.uint8)
    return np.ascontiguousarray(np.tile(rgba_uint8, (height, 1, 1)))


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
    values = _rendered_feature_values(adata, mz, layer)
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


def category_colors(categories: list[str], overrides: dict | None = None) -> list[str]:
    """One `"#rrggbb"` per entry of `categories`: the user's custom color
    for that category (ADR 57, `analysis_db.load_obs_category_colors`) or
    else `category_color` by position. The one place defaults and
    overrides combine, so tiles, legend and image export all agree."""
    overrides = overrides or {}
    return [overrides.get(cat) or category_color(i) for i, cat in enumerate(categories)]


def _hex_rgba(color: str) -> tuple[int, int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16), 255


def render_obs_categories_heatmap(
    adata: ad.AnnData, obs_column: str, categories: list[str],
    colors: list[str] | None = None,
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
        colors: `"#rrggbb"` per entry of `categories` (`category_colors`);
            None = the default palette.

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
    colors = colors or category_colors(categories)
    for i in range(len(categories)):
        rgba[grid == i] = _hex_rgba(colors[i])

    return np.ascontiguousarray(rgba)


def parse_category_colors(field: str) -> tuple[list[str], list[str]]:
    """Split a request id's categories field, `cat1=rrggbb,cat2=rrggbb`
    (ADR 57), into categories and `"#rrggbb"` colors. The hex has no `#`:
    in a QML `Image.source` URL a `#` starts the fragment and everything
    after it never reaches the provider. An entry without a valid
    `=rrggbb` suffix is a plain category with its default color."""
    categories: list[str] = []
    colors: list[str] = []
    for i, item in enumerate(field.split(",") if field else []):
        cat, sep, hex6 = item.rpartition("=")
        if sep and len(hex6) == 6 and all(c in "0123456789abcdefABCDEF" for c in hex6):
            categories.append(cat)
            colors.append("#" + hex6.lower())
        else:
            categories.append(item)
            colors.append(category_color(i))
    return categories, colors


def render_feature_overlay(
    adata: ad.AnnData, channels: list[Channel], layer: str = "TIC", mode: str = "add"
) -> np.ndarray:
    """Several features in one RGBA `uint8` raster, one color each (ADR 67;
    see `feature_overlay.composite`), on the same grid as
    `render_feature_heatmap`. Automatic contrast is per sample."""
    grids = [_reconstruct_grid(adata, _rendered_feature_values(adata, c.mz, layer)) for c in channels]
    return composite(grids, channels, mode)


def render_heatmap_by_target(adata: ad.AnnData, target: str, parts: list[str]) -> np.ndarray:
    """Dispatch to `render_feature_heatmap`/`render_obs_heatmap`/
    `render_obs_categories_heatmap` from an `image://heatmap/...`-style
    request id's already-`unquote`d-and-split parts — the exact id shape
    `HeatmapImageProvider` and (for warping onto an H&E image, see
    `core/registration/overlay.py`) `HEOverlayImageProvider` both parse,
    factored out here so they parse it identically instead of each
    duplicating this dispatch.

    Args:
        adata: One sample's AnnData, already loaded.
        target: `parts[1]` — an m/z string, or `"obs:<column>"`.
        parts: The full split request id (`parts[0]`, the sample name, is
            unused here — callers have already used it to resolve `adata`).

    Returns:
        An RGBA `uint8` array, as returned by whichever `render_*` this
        dispatches to.
    """
    if target.startswith("obs:"):
        obs_column = target[len("obs:") :]
        if is_numeric_obs_column(adata, obs_column):
            colormap, vmin_str, vmax_str = parts[2], parts[3], parts[4]
            vmin = None if vmin_str == "auto" else float(vmin_str)
            vmax = None if vmax_str == "auto" else float(vmax_str)
            return render_obs_heatmap(adata, obs_column, colormap=colormap, vmin=vmin, vmax=vmax)
        categories, colors = parse_category_colors(parts[2])
        return render_obs_categories_heatmap(adata, obs_column, categories, colors)

    if target == "overlay":
        # "<sample>|overlay|<layer>|<mode>|<channels>" (ADR 67)
        return render_feature_overlay(adata, parse_channels(parts[4]), layer=parts[2], mode=parts[3])

    mz_str, layer, colormap, vmin_str, vmax_str = parts[1:6]
    vmin = None if vmin_str == "auto" else float(vmin_str)
    vmax = None if vmax_str == "auto" else float(vmax_str)
    if layer == _LOCAL_MORAN_LAYER:
        # Local import: core.spatial.local_moran pulls in squidpy (heavy —
        # scanpy/numba), and this module is on the GUI's startup path via
        # heatmap_provider.py; deferring the import keeps that cost paid
        # only when a local Moran's I render is actually requested. See
        # that module for the computation itself.
        from msianalyzer.core.spatial.local_moran import render_local_moran_heatmap

        return render_local_moran_heatmap(
            adata, float(mz_str), colormap=colormap, vmin=vmin, vmax=vmax
        )
    return render_feature_heatmap(
        adata, float(mz_str), layer=layer, colormap=colormap, vmin=vmin, vmax=vmax,
    )


#: Above this magnitude, the exported colorbar's tick labels switch to
#: scientific notation — confirmed with the user ("if the value of vmin
#: and vmax are > 10^3, use scientific notation in legend").
_SCIENTIFIC_NOTATION_THRESHOLD = 1000.0


def scale_bar_spec(settings: dict, sample_name: str) -> dict | None:
    """One sample's scale bar (ADR 53) from `analysis_db.load_scale_bar`'s
    settings: `{"length_px", "label", "color"}`, or `None` when disabled
    or that sample has no pixel size set. Pixels are assumed square.
    """
    pixel_um = settings.get("pixel_um", {}).get(sample_name)
    if not settings.get("enabled") or not pixel_um or pixel_um <= 0:
        return None
    bar_um = float(settings["bar_um"])
    return {
        "length_px": bar_um / pixel_um,
        "label": f"{bar_um:g} µm",
        "color": settings["color"],
    }


def render_heatmap_figure(
    adata: ad.AnnData,
    *,
    mode: str,
    mz: float | None = None,
    obs_column: str | None = None,
    categories: list[str] | None = None,
    colors: list[str] | None = None,
    layer: str = "TIC",
    colormap: str = "viridis",
    vmin: float | None = None,
    vmax: float | None = None,
    rois: dict | None = None,
    title: str | None = None,
    scale_bar: dict | None = None,
    hidden_categories: Collection[str] = (),
) -> matplotlib.figure.Figure:
    """A real Matplotlib `Figure` for one sample's spatial heatmap —
    what `export_visual_inspection_images` writes to PNG/PDF/SVG, unlike
    `render_feature_heatmap`/`render_obs_heatmap`/
    `render_obs_categories_heatmap`'s bare RGBA arrays (built for the
    GUI's own lightweight `Image` tiles, no tick-labelled legend).

    Reuses those three functions to build the exact same RGBA raster the
    live GUI tile shows for the same arguments — `imshow`ing an
    already-colorized array, not re-normalizing independently — so a
    colorbar/legend built alongside it is guaranteed to describe the same
    pixels the user actually saw, not a second, potentially-diverging
    color computation.

    Args:
        adata: One sample's AnnData, already loaded.
        mode: `"feature"`, `"obs_numeric"`, or `"obs_categorical"`.
        mz: Required for `mode="feature"`.
        obs_column: Required for `mode="obs_numeric"`/`"obs_categorical"`.
        categories: Required for `mode="obs_categorical"` — the full,
            shared category order (see `render_obs_categories_heatmap`),
            normally the union across every sample being exported so a
            category always gets the same color/legend entry.
        colors: `"#rrggbb"` per entry of `categories` (`category_colors`,
            with the user's custom colors applied); None = default palette.
        layer:`"raw"` or `"TIC"` — `mode="feature"` only.
        colormap: Any registered Matplotlib colormap name.
        vmin: Lower color-scale bound. `None` autoscales to this sample's
            own data (matching what an actual autoscaled GUI tile shows).
        vmax: Upper color-scale bound. `None` autoscales similarly.
        rois: `{name: {"color": "#rrggbb", "vertices": [[col, row], ...]}}`
            (see `core.plotting.roi.load_sample_rois`) — each drawn as an
            unfilled polygon outline on top of the raster, in the same
            grid-index coordinate space `imshow`'s default pixel
            centers already use (no extra transform needed). `None`/`{}`
            draws nothing.
        title: Optional figure title (e.g. the sample name).
        scale_bar: `scale_bar_spec`'s `{"length_px", "label", "color"}` —
            drawn bottom-right on the raster. `None` draws nothing.
        hidden_categories: `mode="obs_categorical"` only — categories
            switched off in Visual Inspection: drawn black, like pixels
            with no value, and left out of the legend.

    Returns:
        A standalone `Figure` (not tied to `pyplot`'s global current-figure
        state) — the caller is responsible for `fig.savefig(...)` and
        closing it (`matplotlib.pyplot.close(fig)`) once done, same as any
        `Figure` built directly rather than via `pyplot.figure()`.
    """
    fig = matplotlib.figure.Figure()
    ax = fig.add_subplot(111)
    ax.set_axis_off()

    legend_kind: str
    eff_vmin = eff_vmax = None

    if mode == "feature":
        if mz is None:
            raise ValueError("mode='feature' requires mz")
        rgba = render_feature_heatmap(
            adata, mz, layer=layer, colormap=colormap, vmin=vmin, vmax=vmax
        )
        eff_vmin, eff_vmax = vmin, vmax
        if eff_vmin is None or eff_vmax is None:
            data_min, data_max = feature_value_range(adata, mz, layer)
            eff_vmin = data_min if eff_vmin is None else eff_vmin
            eff_vmax = data_max if eff_vmax is None else eff_vmax
        legend_kind = "colorbar"
    elif mode == "obs_numeric":
        if not obs_column:
            raise ValueError("mode='obs_numeric' requires obs_column")
        rgba = render_obs_heatmap(
            adata, obs_column, colormap=colormap, vmin=vmin, vmax=vmax
        )
        eff_vmin, eff_vmax = vmin, vmax
        if eff_vmin is None or eff_vmax is None:
            data_min, data_max = obs_value_range(adata, obs_column)
            eff_vmin = data_min if eff_vmin is None else eff_vmin
            eff_vmax = data_max if eff_vmax is None else eff_vmax
        legend_kind = "colorbar"
    elif mode == "obs_categorical":
        if not obs_column:
            raise ValueError("mode='obs_categorical' requires obs_column")
        colors = [
            "#000000" if cat in hidden_categories else color
            for cat, color in zip(categories or [], colors or category_colors(categories or []))
        ]
        rgba = render_obs_categories_heatmap(adata, obs_column, categories or [], colors)
        legend_kind = "categories"
    else:
        raise ValueError(f"unknown mode {mode!r}")

    ax.imshow(rgba, origin="upper")

    for info in (rois or {}).values():
        vertices = info["vertices"]
        if len(vertices) >= 3:
            ax.add_patch(
                matplotlib.patches.Polygon(
                    vertices, closed=True, fill=False,
                    edgecolor=info["color"], linewidth=1.5,
                )
            )

    if scale_bar:
        # imshow's extent is [-0.5, w-0.5] x [h-0.5 (bottom), -0.5 (top)].
        height, width = rgba.shape[:2]
        pad = 0.04 * width
        thickness = max(0.5, 0.015 * height)
        right, bottom = width - 0.5 - pad, height - 0.5 - pad
        ax.add_patch(
            matplotlib.patches.Rectangle(
                (right - scale_bar["length_px"], bottom - thickness),
                scale_bar["length_px"], thickness,
                facecolor=scale_bar["color"], edgecolor="none",
            )
        )
        ax.text(
            right - scale_bar["length_px"] / 2, bottom - thickness * 1.5,
            scale_bar["label"], color=scale_bar["color"], ha="center", va="bottom",
        )

    if legend_kind == "colorbar":
        if eff_vmax <= eff_vmin:
            eff_vmax = eff_vmin + 1.0
        norm = matplotlib.colors.Normalize(vmin=eff_vmin, vmax=eff_vmax)
        mappable = matplotlib.cm.ScalarMappable(norm=norm, cmap=matplotlib.colormaps[colormap])
        cbar = fig.colorbar(mappable, ax=ax)
        if max(abs(eff_vmin), abs(eff_vmax)) > _SCIENTIFIC_NOTATION_THRESHOLD:
            formatter = matplotlib.ticker.ScalarFormatter(useMathText=True)
            formatter.set_powerlimits((0, 0))
            cbar.ax.yaxis.set_major_formatter(formatter)
    else:
        handles = [
            matplotlib.patches.Patch(facecolor=colors[i], label=cat)
            for i, cat in enumerate(categories or [])
            if cat not in hidden_categories
        ]
        if handles:
            ax.legend(handles=handles, loc="center left", bbox_to_anchor=(1.0, 0.5))

    if title:
        ax.set_title(title)

    return fig
