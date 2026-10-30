from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)


class ExportWorker(QThread):
    """Runs one Export-menu function (`core.export.export_annotation_table`
    and friends) on a background thread.

    Same shape and reasoning as `TableQueryWorker`/`MirrorPlotWorker`:
    writing a real export file (a full annotation table, a per-sample
    pixel x feature matrix, a rendered figure) is not guaranteed fast
    enough to run straight on the GUI thread without freezing it. One-shot
    per request, not reused.

    Generic over which export function runs — `export_fn` takes only a
    `report(status)` callable for progress (the caller binds the rest
    beforehand),
    since the three export functions have entirely different signatures
    (a destination file vs. a destination folder vs. a folder plus display
    settings) and there is no shared argument shape worth forcing them
    into.
    """

    succeeded = Signal(str)  # human-readable "done" message/path
    failed = Signal(str)  # human-readable error message
    progress = Signal(str)  # human-readable status while running

    def __init__(self, export_fn: Callable[[Callable[[str], None]], str], parent=None) -> None:
        super().__init__(parent)
        self._export_fn = export_fn

    def run(self) -> None:
        try:
            result = self._export_fn(self.progress.emit)
        except Exception as e:
            logger.exception("export failed")
            self.failed.emit(str(e))
            return
        self.succeeded.emit(result)
