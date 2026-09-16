from __future__ import annotations

import logging

from PySide6.QtCore import QThread, Signal

from msianalyzer.core.config.config import Config
from msianalyzer.core.run.run import Run

logger = logging.getLogger(__name__)


class RunWorker(QThread):
    """Runs `Run.start()` on a background thread, forwarding step progress.

    One `RunWorker` per analysis run (single-shot, not reused). Signal
    emission from `run()` — which executes on this thread — is safe: Qt
    auto-queues connections whose receiver lives on a different thread.
    """

    stepChanged = Signal(str, str)
    sampleProgress = Signal(int, int)
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        run: Run,
        config: Config,
        config_path: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._run = run
        self._config = config
        self._config_path = config_path

    def run(self) -> None:
        try:
            self._run.start(
                config=self._config,
                config_path=self._config_path,
                on_step=lambda step, status: self.stepChanged.emit(step, status),
                on_sample_progress=lambda done, total: self.sampleProgress.emit(
                    done, total
                ),
            )
        except Exception as e:
            logger.exception("run %s: failed", self._run.id)
            self.failed.emit(str(e))
        else:
            self.finished_ok.emit(self._run.id)
