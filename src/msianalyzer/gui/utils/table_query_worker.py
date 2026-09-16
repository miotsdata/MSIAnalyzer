from __future__ import annotations

import logging
from typing import Callable

import pandas as pd
from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)


class TableQueryWorker(QThread):
    """Runs one single-argument `analysis_db.load_*` query on a background
    thread — the shared shape behind `AnalysisBridge.getAnnotationTable`
    and `getFeatureList`, both of which fetch a full table with no
    filtering (search/sort happens client-side in QML, see ADR 29/33).

    Same reasoning and lifecycle as `MirrorPlotWorker`/`FormulaPredictionWorker`:
    a real SQL query, run synchronously on the GUI thread (as these
    originally were, straight from a QML property binding), blocks Qt's
    whole event loop for as long as it takes — an outright freeze, not a
    normal wait, with no chance for a loading indicator to even paint
    first. One-shot per request, not reused.

    Emits the raw `DataFrame`, not already-QML-ready records — the
    `_dataframe_to_records` scrub happens in the handler on the main
    thread, same "finish on the main thread" pattern
    `_onMirrorPlotSucceeded` uses for its own reason (building the Plotly
    figure on the worker thread reproducibly crashed); untested here
    whether a DataFrame->records conversion would have the same problem,
    so it isn't asked to.
    """

    succeeded = Signal(object)  # a pandas DataFrame
    failed = Signal(str)  # human-readable error message

    def __init__(
        self,
        query_fn: Callable[[str], pd.DataFrame],
        analysis_db_path: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._query_fn = query_fn
        self._analysis_db_path = analysis_db_path

    def run(self) -> None:
        try:
            df = self._query_fn(self._analysis_db_path)
        except Exception as e:
            logger.exception(
                "table query %s failed for %s",
                getattr(self._query_fn, "__name__", self._query_fn),
                self._analysis_db_path,
            )
            self.failed.emit(str(e))
            return
        self.succeeded.emit(df)
