import logging
from urllib.parse import unquote

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickAsyncImageProvider, QQuickImageResponse

from msianalyzer.core import analysis_db
from msianalyzer.core.registration import load_registration, warp_heatmap_to_he_space
from msianalyzer.gui.utils.async_image import start_render
from msianalyzer.gui.utils.heatmap_provider import HeatmapImageProvider, rgba_to_qimage

logger = logging.getLogger(__name__)


class HEOverlayImageProvider(QQuickAsyncImageProvider):
    """Serves the MSI heatmap warped into one sample's H&E pixel space,
    as `image://he_overlay/...` — `CoregistrationWindow`'s opacity-
    blended overlay showing where the currently selected feature/obs
    column falls on the higher-resolution H&E image.

    Request id: the same shape `image://heatmap/...` uses
    (`sampleName|mz|layer|colormap|vmin|vmax` or
    `sampleName|obs:col|...`, see `HeatmapImageProvider`); trailing
    fields (dataset token, cache-buster counter) are ignored.

    The heatmap itself comes from `heatmap_provider` (ADR 50): the same
    sample cache, the same render lock, and the same single render thread.
    Before, every overlay request did a full `ad.read_h5ad` on the GUI
    thread. It needs the analysis DB path to resolve `sampleName`'s raw
    database, which holds the registration.
    """

    def __init__(self, heatmap_provider: HeatmapImageProvider | None = None) -> None:
        super().__init__()
        # Standalone use (tests) gets a private provider; the app passes
        # AnalysisBridge's shared one.
        self._owns_heatmap_provider = heatmap_provider is None
        self._heatmap = heatmap_provider or HeatmapImageProvider()
        self._analysis_db_path: str | None = None

    def setAnalysisDbPath(self, analysis_db_path: str, rel_dir: str | None = None) -> None:
        """Same contract as `HeatmapImageProvider.setAnalysisDbPath`. The
        registration is per sample (raw DB), so only the `.h5ad` the warped
        heatmap is rendered from follows `rel_dir`. A shared heatmap
        provider is kept in sync by `AnalysisBridge` itself."""
        self._analysis_db_path = analysis_db_path
        if self._owns_heatmap_provider:
            self._heatmap.setAnalysisDbPath(analysis_db_path, rel_dir)

    def requestImageResponse(self, id: str, requestedSize: QSize) -> QQuickImageResponse:
        return start_render(
            self._heatmap.render_pool, lambda: self.requestImage(id, None, requestedSize)
        )

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

            rgba = self._heatmap._render_target(sample_name, target, parts)
            if rgba is None:
                return QImage()
            warped = warp_heatmap_to_he_space(
                rgba, info.fit.matrix, info.image.width, info.image.height,
            )
        except Exception:
            logger.exception("H&E overlay render failed for request id %r", id)
            return QImage()
        return rgba_to_qimage(warped)
