from __future__ import annotations

import logging

from PySide6.QtCore import QThread, Signal

from msianalyzer.gui.utils.heatmap_provider import HeatmapImageProvider

logger = logging.getLogger(__name__)


class HeatmapPrefetchWorker(QThread):
    """Loads one or more samples' `.h5ad` into `HeatmapImageProvider`'s
    cache on a background thread, ahead of actually switching to the
    Visual Inspection tab.

    Why this exists: `ZoomableImage.qml`'s `Image` is deliberately
    `asynchronous: false` (its own comment explains a real, reproduced
    hang with `asynchronous: true` and 2+ concurrent tiles — likely
    matplotlib global state under concurrent access). That's the right
    call for the *rendering* step (small rasters, genuinely fast), but it
    means the very first tile requested for a sample runs
    `HeatmapImageProvider._load_adata`'s `ad.read_h5ad()` **synchronously
    on the GUI thread** the moment `sectionStack.currentIndex` flips to
    Visual Inspection and its `ZoomableImage`s start requesting
    `image://heatmap/...` — a real, user-visible freeze on a cold cache
    (reported after the "Show spatial distribution" handoff, but it's
    the same cost on a plain first-time nav-button click too). Reading
    the same file on a background thread *before* switching, into the
    same `HeatmapImageProvider._cache`, means the GUI-thread cost once
    the tab actually activates is just a cache hit — no disk I/O. This
    worker only reads the `.h5ad` (`ad.read_h5ad`), never touches
    matplotlib, so it doesn't reintroduce the concurrency hazard
    `asynchronous: false` was guarding against.

    One-shot per request, not reused — same lifecycle as
    `TableQueryWorker`/`MirrorPlotWorker`.
    """

    succeeded = Signal()
    failed = Signal(str)  # human-readable error message

    def __init__(
        self,
        heatmap_provider: HeatmapImageProvider,
        sample_names: list[str],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._heatmap_provider = heatmap_provider
        self._sample_names = sample_names

    def run(self) -> None:
        try:
            for name in self._sample_names:
                self._heatmap_provider._load_adata(name)
        except Exception as e:
            logger.exception("heatmap prefetch failed for %s", self._sample_names)
            self.failed.emit(str(e))
            return
        self.succeeded.emit()
