import logging
from collections import OrderedDict
from pathlib import Path
from urllib.parse import unquote

import anndata as ad
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider

from msianalyzer.core.plotting.heatmap import (
    category_color,
    feature_value_range,
    is_numeric_obs_column,
    list_obs_columns,
    obs_categories,
    obs_value_range,
    render_colorbar,
    render_feature_heatmap,
    render_obs_categories_heatmap,
    render_obs_heatmap,
)

logger = logging.getLogger(__name__)

_CACHE_SIZE = 4


class HeatmapImageProvider(QQuickImageProvider):
    """Serves Visual Inspection's spatial heatmaps as `image://heatmap/...`.

    Three request-id shapes:
      - Feature: `sampleName|mz|layer|colormap|vmin|vmax`, `vmin`/`vmax`
        either a float or the literal `auto` (autoscale to that sample's
        data).
      - `obs` column (second field starts with `"obs:"`):
        `sampleName|obs:columnName|colormap|vmin|vmax` for a numeric
        column (same `vmin`/`vmax` convention as above), or
        `sampleName|obs:columnName|categoriesCsv` for a discrete one —
        `categoriesCsv` is the full, comma-joined category order every
        tile colors by (see `render_obs_categories_heatmap`), normally
        produced once by `getObsCategories` and reused for every tile.
      - Colorbar legend (first field is the literal `"colorbar"`, no
        sample involved): `colorbar|colormap` — a flat gradient strip for
        the color-scale legend under vmin/vmax, see `render_colorbar`.

    Resolving `sampleName` to a `.h5ad` path needs the current analysis'
    database path, set once via `setAnalysisDbPath` when the Visual
    Inspection section loads (see `AnalysisBridge.setHeatmapAnalysis`) —
    there is only ever one active analysis workspace at a time.

    Keeps a small LRU cache of opened `AnnData` objects (keyed by `.h5ad`
    path) so switching feature/colormap/range within the same sample set
    doesn't re-read from disk on every request; Qt's own `Image` caching
    (by the full id string) already avoids re-requesting an unchanged tile.
    """

    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._analysis_db_path: str | None = None
        self._cache: OrderedDict[str, ad.AnnData] = OrderedDict()

    def setAnalysisDbPath(self, analysis_db_path: str) -> None:
        if analysis_db_path != self._analysis_db_path:
            self._cache.clear()
        self._analysis_db_path = analysis_db_path

    def _load_adata(self, sample_name: str) -> ad.AnnData | None:
        if not self._analysis_db_path:
            return None
        h5ad_path = Path(self._analysis_db_path).parent / f"{sample_name}.h5ad"
        key = str(h5ad_path)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        if not h5ad_path.exists():
            return None
        adata = ad.read_h5ad(h5ad_path)
        self._cache[key] = adata
        if len(self._cache) > _CACHE_SIZE:
            self._cache.popitem(last=False)
        return adata

    def invalidate(self, sample_name: str) -> None:
        """Evict `sample_name`'s cached AnnData, if present — call this
        after any write to that sample's `.h5ad` (e.g.
        `AnalysisBridge.saveRoi`/`deleteRoiFromSample`) so the next heatmap
        tile request re-reads the file instead of serving the stale
        in-memory obs/uns this LRU cache is still holding.

        A no-op if the sample was never cached, or no analysis is set.
        """
        if not self._analysis_db_path:
            return
        h5ad_path = Path(self._analysis_db_path).parent / f"{sample_name}.h5ad"
        self._cache.pop(str(h5ad_path), None)

    def getFeatureValueRange(
        self, sample_names: list[str], mz: float, layer: str
    ) -> dict:
        """The real (min, max) of one feature's values across several
        samples — what autoscale is actually using, so a manual vmin/vmax
        control can start from a range that means something for the
        current layer instead of a fixed guess (TIC and raw live on
        completely different scales).

        Args:
            sample_names: Samples to combine — normally the currently
                visible ones, so the range covers what's actually shown.
            mz: The feature's consensus m/z.
            layer: `"raw"` or `"TIC"`.

        Returns:
            `{"vmin": ..., "vmax": ...}`, the min of every sample's min
            and the max of every sample's max. `{"vmin": 0.0, "vmax":
            1.0}` if no sample resolves to real data (matches
            `render_feature_heatmap`'s own all-missing fallback).
        """
        mins = []
        maxes = []
        for name in sample_names:
            adata = self._load_adata(name)
            if adata is None:
                continue
            try:
                vmin, vmax = feature_value_range(adata, mz, layer)
            except Exception:
                logger.exception(
                    "feature value range failed for sample %r, mz %r", name, mz
                )
                continue
            mins.append(vmin)
            maxes.append(vmax)
        if not mins:
            return {"vmin": 0.0, "vmax": 1.0}
        return {"vmin": min(mins), "vmax": max(maxes)}

    def getObsColumns(self, sample_names: list[str]) -> list:
        """`adata.obs` columns available to overlay, from the first
        sample in `sample_names` that actually resolves to a `.h5ad` —
        every sample in one analysis shares the same pipeline-produced
        schema, so one sample's columns stand in for the whole set.

        Returns:
            See `list_obs_columns`; `[]` if no sample resolves.
        """
        for name in sample_names:
            adata = self._load_adata(name)
            if adata is not None:
                return list_obs_columns(adata)
        return []

    def getObsValueRange(self, sample_names: list[str], obs_column: str) -> dict:
        """The `obs`-column analogue of `getFeatureValueRange` — the real
        (min, max) of one numeric `obs` column across `sample_names`.

        Returns:
            `{"vmin": ..., "vmax": ...}`, `{"vmin": 0.0, "vmax": 1.0}` if
            no sample resolves to real data.
        """
        mins = []
        maxes = []
        for name in sample_names:
            adata = self._load_adata(name)
            if adata is None:
                continue
            try:
                vmin, vmax = obs_value_range(adata, obs_column)
            except Exception:
                logger.exception(
                    "obs value range failed for sample %r, column %r",
                    name, obs_column,
                )
                continue
            mins.append(vmin)
            maxes.append(vmax)
        if not mins:
            return {"vmin": 0.0, "vmax": 1.0}
        return {"vmin": min(mins), "vmax": max(maxes)}

    def getObsCategories(self, sample_names: list[str], obs_column: str) -> list:
        """Every distinct category of one discrete `obs` column, combined
        across `sample_names`, in the fixed order every tile's own render
        call colors by (`render_obs_categories_heatmap` colors a category
        by its position in this exact list) — so the legend and every
        tile agree on which color means which category, regardless of
        which samples are currently hidden (callers should pass every
        sample, not just the visible ones, so colors stay stable when
        visibility is toggled).

        Returns:
            `[{"category": ..., "color": "#rrggbb"}, ...]`, sorted.
        """
        categories = set()
        for name in sample_names:
            adata = self._load_adata(name)
            if adata is None:
                continue
            try:
                categories.update(obs_categories(adata, obs_column))
            except Exception:
                logger.exception(
                    "obs categories failed for sample %r, column %r",
                    name, obs_column,
                )
                continue
        ordered = sorted(categories)
        return [
            {"category": cat, "color": category_color(i)}
            for i, cat in enumerate(ordered)
        ]

    def requestImage(self, id: str, size: QSize, requestedSize: QSize) -> QImage:
        try:
            # QML's Image element treats `source` as a URL: assigning
            # "image://heatmap/name|mz|..." percent-encodes the "|" (not a
            # valid raw character in a URL path) to "%7C" before this
            # provider ever sees it, so `id` arrives still encoded — every
            # single request failed on this until unquoted. Sample names
            # with spaces or other reserved characters need this too.
            parts = unquote(id).split("|")
            sample_name, target = parts[0], parts[1]

            if sample_name == "colorbar":
                rgba = render_colorbar(target)
                height, width, _ = rgba.shape
                image = QImage(
                    rgba.data, width, height, width * 4, QImage.Format.Format_RGBA8888
                )
                return image.copy()

            adata = self._load_adata(sample_name)
            if adata is None:
                return QImage()

            if target.startswith("obs:"):
                obs_column = target[len("obs:") :]
                if is_numeric_obs_column(adata, obs_column):
                    colormap, vmin_str, vmax_str = parts[2], parts[3], parts[4]
                    vmin = None if vmin_str == "auto" else float(vmin_str)
                    vmax = None if vmax_str == "auto" else float(vmax_str)
                    rgba = render_obs_heatmap(
                        adata, obs_column, colormap=colormap, vmin=vmin, vmax=vmax,
                    )
                else:
                    categories = parts[2].split(",") if parts[2] else []
                    rgba = render_obs_categories_heatmap(adata, obs_column, categories)
            else:
                mz_str, layer, colormap, vmin_str, vmax_str = parts[1:6]
                vmin = None if vmin_str == "auto" else float(vmin_str)
                vmax = None if vmax_str == "auto" else float(vmax_str)
                rgba = render_feature_heatmap(
                    adata, float(mz_str), layer=layer, colormap=colormap,
                    vmin=vmin, vmax=vmax,
                )
        except Exception:
            logger.exception("heatmap render failed for request id %r", id)
            return QImage()

        height, width, _ = rgba.shape
        image = QImage(
            rgba.data, width, height, width * 4, QImage.Format.Format_RGBA8888
        )
        return image.copy()
