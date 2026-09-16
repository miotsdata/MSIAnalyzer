from __future__ import annotations

import logging

from PySide6.QtCore import QThread, Signal

from msianalyzer.core.annotation.formula_prediction import (
    FormulaPredictionSettings,
    run_formula_prediction,
)

logger = logging.getLogger(__name__)


class FormulaPredictionWorker(QThread):
    """Runs `run_formula_prediction` on a background thread.

    The very first invocation on a given machine downloads msbuddy's
    ~420MB reference database (see ADR 27) — running that synchronously
    on the call-in thread (the GUI thread, for an `AnalysisBridge` `@Slot`
    invoked from QML) would block Qt's whole event loop for as long as it
    took, reading to the user as an outright app freeze rather than a
    normal wait (same failure mode `MirrorPlotWorker` was built to avoid
    for slow/remote file I/O — this is that same fix for a slow/large
    one-time download instead). One-shot per request, not reused — same
    lifecycle as `RunWorker`/`MirrorPlotWorker`.
    """

    succeeded = Signal(str, list)  # analysis_db_path, list[PredictedFormula]
    failed = Signal(str, str)  # analysis_db_path, human-readable error message

    def __init__(
        self,
        analysis_db_path: str,
        feature_ids: list[int],
        settings: FormulaPredictionSettings,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._analysis_db_path = analysis_db_path
        self._feature_ids = feature_ids
        self._settings = settings

    def run(self) -> None:
        try:
            rows = run_formula_prediction(
                self._analysis_db_path, self._feature_ids, self._settings
            )
        except Exception as e:
            logger.exception("formula prediction for %s failed", self._analysis_db_path)
            self.failed.emit(self._analysis_db_path, str(e))
            return
        self.succeeded.emit(self._analysis_db_path, rows)
