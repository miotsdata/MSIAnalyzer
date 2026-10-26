import logging
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import unquote

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickAsyncImageProvider, QQuickImageResponse

from msianalyzer.core.analysis_db import load_obs_category_colors
from msianalyzer.core.imported_dataset import sample_h5ad_path
from msianalyzer.core.plotting.heatmap import (
    category_colors,
    feature_value_range,
    obs_categories,
    obs_value_range,
    render_colorbar,
    render_heatmap_by_target,
)
from msianalyzer.core.plotting.light_sample import LightSample
from msianalyzer.gui.utils.async_image import new_render_pool, start_render

logger = logging.getLogger(__name__)

#: Rendered tiles are small (one pixel per spatial coordinate); this many
#: covers several screens' worth of recently viewed tiles for a 16-sample
#: grid.
_RENDER_CACHE_SIZE = 128
_FALLBACK_BUDGET_BYTES = 4 * 1024**3


def default_cache_budget_bytes() -> int:
    """How much memory the per-sample data cache may hold: a quarter of
    physical RAM (about 16 GB on a 64 GB machine), so every sample of a
    typical project stays warm on the layer being viewed."""
    try:
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") * 0.25)
    except (ValueError, OSError, AttributeError):
        return _FALLBACK_BUDGET_BYTES


def rgba_to_qimage(rgba: np.ndarray) -> QImage:
    height, width, _ = rgba.shape
    image = QImage(rgba.data, width, height, width * 4, QImage.Format.Format_RGBA8888)
    return image.copy()


def load_light_sample(path: Path) -> LightSample:
    """The one place a sample file is opened for rendering. It's a module
    function so tests can count reads by patching it."""
    return LightSample(path)


class HeatmapImageProvider(QQuickAsyncImageProvider):
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
        Each entry is `category=rrggbb` (ADR 57, see
        `parse_category_colors`), so a color change is a new id and
        the tile cache never serves the old color.
      - Colorbar legend (first field is the literal `"colorbar"`, no
        sample involved): `colorbar|colormap` — a flat gradient strip for
        the color-scale legend under vmin/vmax, see `render_colorbar`.
    Fields after these (e.g. `HeatmapControlsPanel.tileTarget`'s `|ds<id>`
    dataset token) are ignored by the parsers.

    **Asynchronous (ADR 50).** Qt calls `requestImageResponse`, which
    only queues the render on `render_pool`: a single-thread pool, so
    renders are serial but never run on the GUI thread. Before this, every
    tile rendered synchronously on the GUI thread, and a cold tile meant a
    full `.h5ad` read (0.35–0.8 s) there. Across a 16-sample grid that
    froze the app for 5–14 s per feature or dataset switch. `requestImage`
    is still the synchronous render itself, used by the queued job and by
    tests.

    **Cache.** Each sample is a `LightSample`: `var`, coordinates, and
    only the layer and obs columns actually viewed, with the matrix in
    CSC, so a feature column is sub-millisecond. The cache is bounded by
    a memory budget (`default_cache_budget_bytes`) rather than an entry
    count; the old 4-entry cache thrashed on every feature switch as soon
    as a project had more than 4 samples. A second LRU holds rendered RGBA
    tiles, keyed by the full request id.

    **Thread safety.** The render thread, `HeatmapQueryWorker`s and
    `HeatmapPrefetchWorker` all touch the cache, so every cache access
    and every render holds `lock` (re-entrant).

    Resolving `sampleName` to a `.h5ad` path needs the current analysis'
    database path (and, for an imported dataset, its folder), set via
    `setAnalysisDbPath`. There is only ever one active analysis workspace.
    """

    def __init__(self, cache_budget_bytes: int | None = None) -> None:
        super().__init__()
        self._analysis_db_path: str | None = None
        # Imported dataset folder (ADR 49), relative to the analysis
        # folder; None = the pipeline's own per-sample files.
        self._rel_dir: str | None = None
        self._budget = cache_budget_bytes or default_cache_budget_bytes()
        self._cache: OrderedDict[str, LightSample] = OrderedDict()
        self._render_cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self.lock = threading.RLock()
        self.render_pool = new_render_pool()

    def setAnalysisDbPath(self, analysis_db_path: str | None, rel_dir: str | None = None) -> None:
        """Point this provider at one analysis' pipeline output
        (`rel_dir=None`) or one of its imported datasets (`rel_dir` =
        `imported_datasets.rel_dir`, see `AnalysisBridge.setActiveDataset`).
        """
        rel_dir = rel_dir or None
        with self.lock:
            if analysis_db_path != self._analysis_db_path or rel_dir != self._rel_dir:
                self._cache.clear()
                # A request id (e.g. "s1|100.0|raw|viridis|0|15") says
                # nothing about which analysis it came from — without also
                # clearing this, switching to a different analysis that
                # happens to share a sample name and request the same tile
                # would silently serve the OLD analysis's rendered pixels.
                self._render_cache.clear()
            self._analysis_db_path = analysis_db_path
            self._rel_dir = rel_dir

    def clearCaches(self) -> None:
        """Drop every cached sample and render — after the files of the
        current source were rewritten on disk (e.g. Moran's I computed
        over an imported dataset)."""
        with self.lock:
            self._cache.clear()
            self._render_cache.clear()

    def _h5ad_path(self, sample_name: str) -> Path:
        return sample_h5ad_path(self._analysis_db_path, sample_name, self._rel_dir)

    # -- sample cache (callers hold self.lock) -----------------------------

    def _sample(self, sample_name: str) -> LightSample | None:
        if not self._analysis_db_path:
            return None
        h5ad_path = self._h5ad_path(sample_name)
        key = str(h5ad_path)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        if not h5ad_path.exists():
            return None
        sample = load_light_sample(h5ad_path)
        self._cache[key] = sample
        return sample

    def _enforce_budget(self) -> None:
        """Evict least-recently-used samples until the cache fits the
        budget. The most recent one is always kept."""
        total = sum(s.nbytes() for s in self._cache.values())
        while total > self._budget and len(self._cache) > 1:
            _key, evicted = self._cache.popitem(last=False)
            total -= evicted.nbytes()

    def _prepared_sample(self, sample_name: str, parts: list[str]) -> LightSample | None:
        sample = self._sample(sample_name)
        if sample is not None:
            sample.prepare(parts)
            self._enforce_budget()
        return sample

    def warm(self, sample_names: list[str], layer: str) -> None:
        """Load `sample_names` with `layer` into the cache — what
        `HeatmapPrefetchWorker` runs before Visual Inspection opens."""
        for name in sample_names:
            with self.lock:
                sample = self._sample(name)
                if sample is not None:
                    sample.ensure_layer(layer)
                    self._enforce_budget()

    def invalidate(self, sample_name: str) -> None:
        """Evict `sample_name`'s cached data, if present. Call this after
        any write to that sample's `.h5ad` (e.g.
        `AnalysisBridge.saveRoi`/`deleteRoiFromSample`), so the next tile
        request re-reads the file instead of serving stale obs/uns from
        this cache.

        It also drops every cached *render* for that sample
        (`_render_cache` keys start with `"<sample_name>|"`). Otherwise a
        stale tile would keep being served after the underlying data
        changed (e.g. a new ROI drawn).

        A no-op if the sample was never cached, or no analysis is set.
        """
        with self.lock:
            if not self._analysis_db_path:
                return
            self._cache.pop(str(self._h5ad_path(sample_name)), None)
            prefix = f"{sample_name}|"
            for key in [k for k in self._render_cache if k.startswith(prefix)]:
                del self._render_cache[key]

    def _render_target(self, sample_name: str, target: str, parts: list[str]) -> np.ndarray | None:
        """Render (or return the cached render of) one tile. Shared by
        `requestImage` and `HEOverlayImageProvider`, so both populate and
        consume the same caches under the same key (the full request id).
        """
        cache_key = "|".join(parts)
        with self.lock:
            if cache_key in self._render_cache:
                self._render_cache.move_to_end(cache_key)
                return self._render_cache[cache_key]

            if sample_name == "colorbar":
                rgba = render_colorbar(target)
            else:
                t0 = time.perf_counter()
                sample = self._prepared_sample(sample_name, parts)
                if sample is None:
                    return None
                t1 = time.perf_counter()
                rgba = render_heatmap_by_target(sample.adata, target, parts)
                t2 = time.perf_counter()
                log = logger.warning if t2 - t0 > 0.5 else logger.debug
                log(
                    "heatmap tile %s: load %.3f s, render %.3f s (%s)",
                    cache_key, t1 - t0, t2 - t1, threading.current_thread().name,
                )

            self._render_cache[cache_key] = rgba
            if len(self._render_cache) > _RENDER_CACHE_SIZE:
                self._render_cache.popitem(last=False)
            return rgba

    # -- queries (HeatmapQueryWorker threads) ------------------------------

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
            layer: `"raw"`, `"TIC"`, `"X"`, or an imported dataset's layer.

        Returns:
            `{"vmin": ..., "vmax": ...}`, the min of every sample's min
            and the max of every sample's max. `{"vmin": 0.0, "vmax":
            1.0}` if no sample resolves to real data (matches
            `render_feature_heatmap`'s own all-missing fallback).
        """
        mins = []
        maxes = []
        for name in sample_names:
            with self.lock:
                try:
                    sample = self._sample(name)
                    if sample is None:
                        continue
                    sample.ensure_layer(layer)
                    self._enforce_budget()
                    vmin, vmax = feature_value_range(sample.adata, mz, layer)
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
        """Overlayable `obs` columns, from the first sample in
        `sample_names` that resolves to a `.h5ad`. Every sample in one
        analysis (or one imported dataset) shares the same schema, so one
        sample stands in for the whole set. Read from file metadata only.

        Returns:
            See `list_obs_columns`; `[]` if no sample resolves.
        """
        for name in sample_names:
            with self.lock:
                sample = self._sample(name)
                if sample is not None:
                    return sample.obs_columns()
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
            with self.lock:
                try:
                    sample = self._sample(name)
                    if sample is None:
                        continue
                    sample.ensure_obs(obs_column)
                    vmin, vmax = obs_value_range(sample.adata, obs_column)
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
            `[{"category": ..., "color": "#rrggbb", "custom": bool}, ...]`,
            sorted. `custom`: the color is the user's own (ADR 57), not
            the default palette.
        """
        categories = set()
        for name in sample_names:
            with self.lock:
                try:
                    sample = self._sample(name)
                    if sample is None:
                        continue
                    sample.ensure_obs(obs_column)
                    categories.update(obs_categories(sample.adata, obs_column))
                except Exception:
                    logger.exception(
                        "obs categories failed for sample %r, column %r",
                        name, obs_column,
                    )
                    continue
        ordered = sorted(categories)
        overrides = (
            load_obs_category_colors(self._analysis_db_path, obs_column)
            if self._analysis_db_path else {}
        )
        return [
            {"category": cat, "color": color, "custom": cat in overrides}
            for cat, color in zip(ordered, category_colors(ordered, overrides))
        ]

    # -- Qt entry points -----------------------------------------------------

    def requestImageResponse(self, id: str, requestedSize: QSize) -> QQuickImageResponse:
        return start_render(self.render_pool, lambda: self.requestImage(id, None, requestedSize))

    def requestImage(self, id: str, size: QSize, requestedSize: QSize) -> QImage:
        """The synchronous render behind every tile: runs on
        `render_pool`'s thread when Qt asks (`requestImageResponse`), or
        directly from tests."""
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
        return rgba_to_qimage(rgba)
