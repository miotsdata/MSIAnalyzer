import logging
from urllib.parse import unquote

import numpy as np
from PIL import Image as PILImage
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider

from msianalyzer.core import analysis_db
from msianalyzer.core.registration import load_registration, resolve_image_path

logger = logging.getLogger(__name__)


class HEImageProvider(QQuickImageProvider):
    """Serves one sample's attached H&E/brightfield image (see
    `core/registration/image_registration.py`) as `image://he_image/...`,
    for `CoregistrationWindow`'s landmark-picking canvas.

    Request id: `sampleName|revision` — `revision` is never interpreted
    here, it's only a cache-buster the QML side bumps after attaching or
    replacing an image (an unchanged `Image.source` string never
    re-triggers a request, so re-attaching the same sample's image would
    otherwise keep showing the old one).

    Resolving `sampleName` to its raw database (and therefore its
    attached image) needs the current analysis' database path, set once
    via `setAnalysisDbPath` when Visual Inspection loads — mirrors
    `HeatmapImageProvider`. Unlike that provider, nothing is cached here:
    an H&E image is requested once per sample per coregistration-window
    open/re-attach, not on every colormap/vmin/vmax tweak.
    """

    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._analysis_db_path: str | None = None

    def setAnalysisDbPath(self, analysis_db_path: str) -> None:
        self._analysis_db_path = analysis_db_path

    def requestImage(self, id: str, size: QSize, requestedSize: QSize) -> QImage:
        try:
            # See HeatmapImageProvider.requestImage's identical comment —
            # QML's Image element percent-encodes "|" before this provider
            # ever sees the id.
            sample_name = unquote(id).split("|")[0]
            if not self._analysis_db_path:
                return QImage()

            raw_db_path = analysis_db.get_sample_raw_db_path(
                self._analysis_db_path, sample_name
            )
            if not raw_db_path:
                return QImage()

            info = load_registration(raw_db_path)
            if info is None:
                return QImage()

            image_path = resolve_image_path(raw_db_path, info.image)
            if not image_path.exists():
                return QImage()

            with PILImage.open(image_path) as img:
                rgba = np.ascontiguousarray(np.array(img.convert("RGBA")))
        except Exception:
            logger.exception("H&E image render failed for request id %r", id)
            return QImage()

        height, width, _ = rgba.shape
        image = QImage(
            rgba.data, width, height, width * 4, QImage.Format.Format_RGBA8888
        )
        return image.copy()
