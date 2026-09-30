import logging
from collections import OrderedDict
from pathlib import Path
from urllib.parse import unquote

import anndata as ad
import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider

from msianalyzer.core.imported_dataset import sample_h5ad_path
from msianalyzer.core.plotting.heatmap import (
    category_color,
    feature_value_range,
    list_obs_columns,
    obs_categories,
    obs_value_range,
    render_colorbar,
    render_heatmap_by_target,
)

logger = logging.getLogger(__name__)

_CACHE_SIZE = 4
_RENDER_CACHE_SIZE = 16


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
    doesn't re-read from disk on every request. A second, separate LRU
    cache holds already-*rendered* RGBA arrays, keyed by the exact
    request id — `ZoomableImage.qml`'s `Image` sets `cache: false`
    (Qt's own image cache is disabled there), so without this, re-showing
    a tile you've already viewed (e.g. flipping back to a feature) redid
    the full matplotlib render every time. It also lets
    `AnalysisBridge.prefetchHeatmapSamples` pre-render the default tile
    on a background thread (`_render_target`, shared with `requestImage`)
    before Visual Inspection's `ZoomableImage`s ever request it — the
    render cost (not just the `.h5ad` read `_load_adata` alone covers) is
    what was still causing several seconds of a blank grid after the
    loading overlay disappeared.
    """

    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._analysis_db_path: str | None = None
        # Imported dataset folder (ADR 49), relative to the analysis
        # folder; None = the pipeline's own per-sample files.
        self._rel_dir: str | None = None
        self._cache: OrderedDict[str, ad.AnnData] = OrderedDict()
        self._render_cache: OrderedDict[str, np.ndarray] = OrderedDict()

    def setAnalysisDbPath(self, analysis_db_path: str, rel_dir: str | None = None) -> None:
        """Point this provider at one analysis' pipeline output
        (`rel_dir=None`) or one of its imported datasets (`rel_dir` =
        `imported_datasets.rel_dir`, see `AnalysisBridge.setActiveDataset`).
        """
        rel_dir = rel_dir or None
        if analysis_db_path != self._analysis_db_path or rel_dir != self._rel_dir:
            self._cache.clear()
            # A request id (e.g. "s1|100.0|raw|viridis|0|15") says nothing
            # about which analysis (or dataset) it came from — without
            # also clearing this, switching to a different analysis that
            # happens to share a sample name and request the same tile
            # would silently serve the OLD analysis's rendered pixels.
            self._render_cache.clear()
        self._analysis_db_path = analysis_db_path
        self._rel_dir = rel_dir

    def clearCaches(self) -> None:
        """Drop every cached AnnData and render — after the files of the
        current source were rewritten on disk (e.g. Moran's I computed
        over an imported dataset)."""
        self._cache.clear()
        self._render_cache.clear()

    def _h5ad_path(self, sample_name: str) -> Path:
        return sample_h5ad_path(self._analysis_db_path, sample_name, self._rel_dir)

    def _load_adata(self, sample_name: str) -> ad.AnnData | None:
        if not self._analysis_db_path:
            return None
        h5ad_path = self._h5ad_path(sample_name)
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

        Also drops every cached *render* for that sample (`_render_cache`
        keys start with `"<sample_name>|"`) — a stale rendered tile would
        otherwise keep being served from cache even after the underlying
        data changed (e.g. a new ROI drawn).

        A no-op if the sample was never cached, or no analysis is set.
        """
        if not self._analysis_db_path:
            return
        self._cache.pop(str(self._h5ad_path(sample_name)), None)
        prefix = f"{sample_name}|"
        for key in [k for k in self._render_cache if k.startswith(prefix)]:
            del self._render_cache[key]

    def _render_target(self, sample_name: str, target: str, parts: list[str]) -> np.ndarray | None:
        """Render (or return the cached render of) one tile — shared by
        `requestImage` and `AnalysisBridge.prefetchHeatmapSamples`'s
        background pre-render, so both populate/consume the exact same
        cache under the exact same key (the full request id).
        """
        cache_key = "|".join(parts)
        if cache_key in self._render_cache:
            self._render_cache.move_to_end(cache_key)
            return self._render_cache[cache_key]

        if sample_name == "colorbar":
            rgba = render_colorbar(target)
        else:
            adata = self._load_adata(sample_name)
            if adata is None:
                return None
            rgba = render_heatmap_by_target(adata, target, parts)

        self._render_cache[cache_key] = rgba
        if len(self._render_cache) > _RENDER_CACHE_SIZE:
            self._render_cache.popitem(last=False)
        return rgba

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
            rgba = self._render_target(sample_name, target, parts)
            if rgba is None:
                return QImage()
        except Exception:
            logger.exception("heatmap render failed for request id %r", id)
            return QImage()

        height, width, _ = rgba.shape
        image = QImage(
            rgba.data, width, height, width * 4, QImage.Format.Format_RGBA8888
        )
        return image.copy()
