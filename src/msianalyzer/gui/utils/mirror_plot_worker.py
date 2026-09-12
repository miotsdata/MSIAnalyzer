from __future__ import annotations

import logging

from PySide6.QtCore import QThread, Signal

from msianalyzer.core.plotting.plotter import Plotter

logger = logging.getLogger(__name__)


class MirrorPlotWorker(QThread):
    """Resolves one annotation's mirror-plot data (`Plotter.get_annotation_spectra`)
    on a background thread.

    `emp_source`/`lib_source` == "raw" means reading straight from a file
    that isn't guaranteed local or fast: the owning sample's raw database,
    or — this worker's original motivation — a spectral library file
    living on a slow network mount. Doing that synchronously on the GUI
    thread (the original `AnalysisBridge.getMirrorPlotUrl` did) blocks
    Qt's whole event loop for as long as the I/O takes, which reads to
    the user as an outright app freeze ("python is not responding",
    eventually force-quit) rather than a normal wait — nothing else can
    repaint or respond meanwhile. One-shot per request, not reused —
    matches `RunWorker`'s own lifecycle.

    Deliberately stops at the *data*, not the Plotly figure: building the
    figure here too (tried first) reproducibly crashed — a segfault inside
    Plotly's own `_perform_plotly_relayout` while the main thread was mid
    garbage-collection, i.e. Plotly's object graph isn't safe to build
    concurrently with whatever else is happening on the main thread.
    `AnalysisBridge` calls `Plotter.build_mirror_figure` on the *main*
    thread once `succeeded` delivers this worker's data.
    """

    succeeded = Signal(object)  # a get_annotation_spectra() dict
    failed = Signal(str)  # human-readable error message

    def __init__(
        self,
        analysis_db_path: str,
        annotation_id: int,
        emp_source: str,
        lib_source: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._analysis_db_path = analysis_db_path
        self._annotation_id = annotation_id
        self._emp_source = emp_source
        self._lib_source = lib_source

    def run(self) -> None:
        try:
            data = Plotter().get_annotation_spectra(
                self._analysis_db_path,
                self._annotation_id,
                emp_source=self._emp_source,
                lib_source=self._lib_source,
            )
        except (ValueError, OSError) as e:
            logger.warning(
                "mirror plot data for annotation %s failed: %s", self._annotation_id, e
            )
            self.failed.emit(str(e))
            return
        self.succeeded.emit(data)
