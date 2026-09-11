import logging
from collections import OrderedDict
from pathlib import Path
from urllib.parse import unquote

import anndata as ad
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider

from msianalyzer.core.plotting.heatmap import render_feature_heatmap

logger = logging.getLogger(__name__)

_CACHE_SIZE = 4


class HeatmapImageProvider(QQuickImageProvider):
    """Serves Visual Inspection's spatial heatmaps as `image://heatmap/...`.

    Request ids are `sampleName|mz|layer|colormap|vmin|vmax`, `vmin`/`vmax`
    either a float or the literal `auto` (autoscale to that sample's data).
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

    def requestImage(self, id: str, size: QSize, requestedSize: QSize) -> QImage:
        try:
            # QML's Image element treats `source` as a URL: assigning
            # "image://heatmap/name|mz|..." percent-encodes the "|" (not a
            # valid raw character in a URL path) to "%7C" before this
            # provider ever sees it, so `id` arrives still encoded — every
            # single request failed on this until unquoted. Sample names
            # with spaces or other reserved characters need this too.
            sample_name, mz_str, layer, colormap, vmin_str, vmax_str = (
                unquote(id).split("|")
            )
            vmin = None if vmin_str == "auto" else float(vmin_str)
            vmax = None if vmax_str == "auto" else float(vmax_str)
            adata = self._load_adata(sample_name)
            if adata is None:
                return QImage()
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
