import logging
from pathlib import Path
from urllib.parse import unquote

import anndata as ad
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider

from msianalyzer.core import analysis_db
from msianalyzer.core.plotting.heatmap import render_heatmap_by_target
from msianalyzer.core.registration import load_registration, warp_heatmap_to_he_space

logger = logging.getLogger(__name__)


class HEOverlayImageProvider(QQuickImageProvider):
    """Serves the MSI heatmap warped into one sample's H&E pixel space,
    as `image://he_overlay/...` — `CoregistrationWindow`'s opacity-
    blended overlay showing where the currently selected feature/obs
    column falls on the higher-resolution H&E image.

    Request id: the same shape `image://heatmap/...` uses
    (`sampleName|mz|layer|colormap|vmin|vmax` or
    `sampleName|obs:col|...`, see `HeatmapImageProvider`) — reuses
    `core/plotting/heatmap.py::render_heatmap_by_target` so both parse it
    identically; `HeatmapControlsPanel.qml::tileTarget` builds the shared
    `sampleName|...` tail both `image://heatmap/...` and this provider's
    URLs are built from.

    Needs the same analysis DB path `HEImageProvider` does (to resolve
    `sampleName`'s raw database and registration) PLUS that sample's own
    `.h5ad` (to render the requested heatmap before warping it) — read
    directly here rather than through `HeatmapImageProvider`'s cache,
    since this is requested far less often (only while
    `CoregistrationWindow`'s overlay is visible) and staying independent
    avoids coupling the two providers' lifecycles.
    """

    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._analysis_db_path: str | None = None

    def setAnalysisDbPath(self, analysis_db_path: str) -> None:
        self._analysis_db_path = analysis_db_path

    def requestImage(self, id: str, size: QSize, requestedSize: QSize) -> QImage:
        try:
            parts = unquote(id).split("|")
            sample_name, target = parts[0], parts[1]
            if not self._analysis_db_path:
                return QImage()

            raw_db_path = analysis_db.get_sample_raw_db_path(
                self._analysis_db_path, sample_name
            )
            if not raw_db_path:
                return QImage()
            info = load_registration(raw_db_path)
            if info is None or info.fit is None:
                return QImage()

            h5ad_path = Path(self._analysis_db_path).parent / f"{sample_name}.h5ad"
            if not h5ad_path.exists():
                return QImage()
            adata = ad.read_h5ad(h5ad_path)

            rgba = render_heatmap_by_target(adata, target, parts)
            warped = warp_heatmap_to_he_space(
                rgba, info.fit.matrix, info.image.width, info.image.height,
            )
        except Exception:
            logger.exception("H&E overlay render failed for request id %r", id)
            return QImage()

        height, width, _ = warped.shape
        image = QImage(
            warped.data, width, height, width * 4, QImage.Format.Format_RGBA8888
        )
        return image.copy()
