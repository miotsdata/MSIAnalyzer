from __future__ import annotations

import logging

from PySide6.QtCore import QThread, Signal

from msianalyzer.gui.utils.heatmap_provider import HeatmapImageProvider

logger = logging.getLogger(__name__)


class HeatmapPrefetchWorker(QThread):
    """Loads samples into `HeatmapImageProvider`'s cache on a background
    thread, with the layer Visual Inspection is about to show. It runs
    ahead of switching to the Visual Inspection tab.

    Tiles themselves no longer render on the GUI thread (ADR 50: the
    provider is asynchronous), so this isn't needed to avoid a freeze
    any more. It still makes the tab open fully painted rather than
    filling in tile by tile. Once each sample's layer is in memory, a
    render is a ~10 ms column slice plus colorize, so the tiles appear
    together as soon as the loading overlay lifts.

    Concurrency: `HeatmapImageProvider.warm` holds the provider's lock
    per sample, the same lock every render and query takes, so this can
    safely run while tiles are rendering.

    One-shot per request, not reused — same lifecycle as
    `TableQueryWorker`/`MirrorPlotWorker`.
    """

    succeeded = Signal()
    failed = Signal(str)  # human-readable error message

    def __init__(
        self,
        heatmap_provider: HeatmapImageProvider,
        sample_names: list[str],
        layer: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._heatmap_provider = heatmap_provider
        self._sample_names = sample_names
        self._layer = layer

    def run(self) -> None:
        try:
            self._heatmap_provider.warm(self._sample_names, self._layer)
        except Exception as e:
            logger.exception("heatmap prefetch failed for %s", self._sample_names)
            self.failed.emit(str(e))
            return
        self.succeeded.emit()
