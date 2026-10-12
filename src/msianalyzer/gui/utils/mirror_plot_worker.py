from __future__ import annotations

import logging

from PySide6.QtCore import QThread, Signal

from msianalyzer.core.plotting.plotter import Plotter, mirror_plot_data

logger = logging.getLogger(__name__)


class MirrorPlotWorker(QThread):
    """Resolves one annotation's mirror-plot data (`Plotter.get_annotation_spectra`,
    then `mirror_plot_data`) on a background thread.

    `emp_source`/`lib_source` == "raw" means reading straight from a file
    that isn't guaranteed local or fast: the owning sample's raw database,
    or — this worker's original motivation — a spectral library file
    living on a slow network mount. Doing that synchronously on the GUI
    thread (the original synchronous mirror-plot slot did) blocks
    Qt's whole event loop for as long as the I/O takes, which reads to
    the user as an outright app freeze ("python is not responding",
    eventually force-quit) rather than a normal wait — nothing else can
    repaint or respond meanwhile. One-shot per request, not reused —
    matches `RunWorker`'s own lifecycle.

    The result is plain lists (ADR 51: the GUI plots with Qt Graphs), so
    it's built here too. ADR 17's "build the Plotly figure on the main
    thread" rule no longer applies: no Plotly object exists in the GUI.
    """

    succeeded = Signal(object)  # a mirror_plot_data() dict
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
        self.succeeded.emit(mirror_plot_data(data))
