from __future__ import annotations

import logging
from typing import Any, Callable

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)


class HeatmapQueryWorker(QThread):
    """Runs one arbitrary `HeatmapImageProvider` query
    (`getFeatureValueRange`/`getObsColumns`/`getObsValueRange`/
    `getObsCategories`) on a background thread.

    These four calls were originally synchronous `AnalysisBridge` `@Slot`s,
    called straight from `HeatmapControlsPanel.qml` property bindings/
    imperative refreshes — each one real `.h5ad`-backed work (same class
    of GUI-thread-blocking cost as `TableQueryWorker`'s SQL queries, just
    against `HeatmapImageProvider`'s own cached `AnnData` instead of
    sqlite), reported as a lingering freeze on opening Visual Inspection
    even after tile rendering itself was fixed (ADR 48). One generic
    worker rather than four near-identical typed ones, since the shape is
    identical in all four cases: call a bound method with some positional
    args, emit whatever it returns.
    """

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[..., Any], *args: Any, parent=None) -> None:
        super().__init__(parent)
        self._fn = fn
        self._args = args

    def run(self) -> None:
        try:
            result = self._fn(*self._args)
        except Exception as e:
            logger.exception(
                "heatmap query %s failed", getattr(self._fn, "__name__", self._fn)
            )
            self.failed.emit(str(e))
            return
        self.succeeded.emit(result)
