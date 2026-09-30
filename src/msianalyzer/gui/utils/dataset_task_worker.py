from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import QThread, Signal

from msianalyzer.core.imported_dataset import ImportedDatasetError

logger = logging.getLogger(__name__)


class DatasetTaskWorker(QThread):
    """Runs one long imported-dataset job (ADR 49): the `.h5ad` import
    split, or Moran's I over an imported dataset's samples. It runs on a
    background thread and reports per-sample progress.

    It has the same one-shot lifecycle as `ExportWorker`. The difference
    is that `task_fn` is handed a `progress(done, total, sample_name)`
    callback, re-emitted here as `progress`. The signal is queued across
    threads, so QML's progress bar updates on the GUI thread. The caller
    binds every other argument with `functools.partial` beforehand.

    `failed` carries the user-facing message of an `ImportedDatasetError`
    as-is. Any other exception is logged with its traceback and reported
    by its message.
    """

    progress = Signal(int, int, str)  # done, total, sample name
    succeeded = Signal(object)  # whatever task_fn returned
    failed = Signal(str)  # human-readable error message

    def __init__(
        self, task_fn: Callable[[Callable[[int, int, str], None]], object], parent=None
    ) -> None:
        super().__init__(parent)
        self._task_fn = task_fn

    def run(self) -> None:
        try:
            result = self._task_fn(self.progress.emit)
        except ImportedDatasetError as e:
            logger.warning("dataset task failed: %s", e)
            self.failed.emit(str(e))
            return
        except Exception as e:
            logger.exception("dataset task failed")
            self.failed.emit(str(e) or type(e).__name__)
            return
        self.succeeded.emit(result)
