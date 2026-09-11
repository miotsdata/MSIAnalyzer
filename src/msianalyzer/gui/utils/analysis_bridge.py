import logging
import os
import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
from PySide6.QtCore import QObject, Signal, Slot

from msianalyzer.core import analysis_db
from msianalyzer.core.plotting.plotter import Plotter
from msianalyzer.core.spectra.average_spectra import load_aggregated_spectra
from msianalyzer.gui.utils.heatmap_provider import HeatmapImageProvider

logger = logging.getLogger(__name__)

_SPECTRUM_DIV_ID = "msi-spectrum-plot"

_EMPTY_SUMMARY = {
    "n_samples": 0,
    "n_features": 0,
    "n_ms2_associated_features": 0,
    "annotation_ran": False,
    "n_annotated_features": 0,
    "n_distinct_compounds": 0,
}


def _dataframe_to_records(df: pd.DataFrame) -> list:
    """`df.to_dict("records")`, scrubbed for QML: numpy scalars -> native
    Python (`.item()`), NaN -> `None`. Pandas reads (`sqlite3`/`read_sql_query`)
    otherwise leak `numpy.int64`/`numpy.float64` into the records, which QML
    doesn't reliably marshal (e.g. `String(numpy_value)` can misbehave)."""
    records = []
    for row in df.to_dict("records"):
        clean = {}
        for key, value in row.items():
            if value is None or (isinstance(value, float) and pd.isna(value)):
                clean[key] = None
            elif hasattr(value, "item"):
                clean[key] = value.item()
            else:
                clean[key] = value
        records.append(clean)
    return records


class AnalysisBridge(QObject):
    """Read-only data access for the Analysis workspace's sections.

    Stateless — every method takes the analysis' `analysis_db_path`/
    `out_dir` explicitly (from an `AnalysisModel`) rather than holding its
    own "current analysis" state. Local SQLite reads are fast enough to
    stay synchronous; no QThread/worker needed here, unlike `RunWorker`.
    Methods are called directly from QML (unlike `CoreBridge`, whose
    methods are only ever invoked from `Application`), so they're named to
    read naturally as QML calls — camelCase, matching `Router.toLocalPath`.

    `spectrumPointClicked` is the one exception to "QML calls in, nothing
    flows back out": the MS1 spectra section's `WebEngineView` registers
    this object on a `WebChannel`, and the JS embedded in
    `getSpectrumUrl`'s output calls `onSpectrumPointClicked` when the user
    clicks a point on the plot — QML listens for the resulting signal to
    update the feature-detail side panel.
    """

    spectrumPointClicked = Signal(float)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # Owned here (rather than constructed in main.py) so Application is
        # the single place that wires up an AnalysisBridge; main.py just
        # registers it with the engine (`engine.addImageProvider("heatmap",
        # application.analysis_bridge.heatmap_provider)`).
        self.heatmap_provider = HeatmapImageProvider()
        # Plot HTML -> temp file -> `WebEngineView.url`, not `.loadHtml()`.
        # `loadHtml`/`setHtml` silently fail past Qt's documented ~2MB
        # limit (it's an IPC message, capped); a plot with the full
        # Plotly.js library inlined (`include_plotlyjs=True`) is ~4.6MB on
        # its own before any data — every spectrum/mirror plot exceeded
        # the limit and simply never rendered. A `file://` navigation has
        # no such cap. Counters keep each URL unique (an unchanged QML
        # `url` binding doesn't reload, even if the file's content
        # changed) while bounding disk use to the current + one stale file.
        self._spectrum_counter = 0
        self._spectrum_prev_path: Path | None = None
        self._mirror_plot_counter = 0
        self._mirror_plot_prev_path: Path | None = None

    @Slot(float)
    def onSpectrumPointClicked(self, mz: float) -> None:
        """Called from JS (via the `WebChannel`) when a spectrum point is
        clicked; re-emitted as `spectrumPointClicked` for QML to connect to."""
        self.spectrumPointClicked.emit(mz)

    @Slot(str)
    def setHeatmapAnalysis(self, analysis_db_path: str) -> None:
        """Point the `image://heatmap/...` provider at this analysis.

        Called once when the Visual Inspection section loads (there's only
        ever one active analysis workspace at a time).
        """
        self.heatmap_provider.setAnalysisDbPath(analysis_db_path)

    @Slot(list, float, str, result=dict)
    def getFeatureValueRange(self, sample_names: list, mz: float, layer: str) -> dict:
        """The real (min, max) of one feature's values across `sample_names`
        — what autoscale is actually using for the Visual Inspection grid.
        See `HeatmapImageProvider.getFeatureValueRange`.

        Args:
            sample_names: Samples to combine — normally the currently
                visible ones.
            mz: The feature's consensus m/z.
            layer: `"raw"` or `"TIC"`.

        Returns:
            `{"vmin": ..., "vmax": ...}`.
        """
        return self.heatmap_provider.getFeatureValueRange(sample_names, mz, layer)

    @Slot(str, result=dict)
    def getSummary(self, analysis_db_path: str) -> dict:
        """Headline counts for the Summary section.

        Args:
            analysis_db_path: Path to the analysis' SQLite database, as
                resolved by `AnalysisModel.analysisDbPath`.

        Returns:
            See `analysis_db.load_summary_counts`; an all-zero dict when
            `analysis_db_path` is empty or doesn't exist (nothing crashes
            the section, it just reads as an empty analysis).
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return dict(_EMPTY_SUMMARY)
        return analysis_db.load_summary_counts(analysis_db_path)

    @Slot(str, result=list)
    def getAnnotationTable(self, analysis_db_path: str) -> list:
        """One row per feature — its single best (highest-score) compound hit.

        Args:
            analysis_db_path: The analysis' SQLite database.

        Returns:
            Records from `analysis_db.load_feature_compound_scores`, kept to
            the top-scoring row per `feature_id`. Empty when annotation
            never ran (or the db path doesn't exist).
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return []
        df = analysis_db.load_feature_compound_scores(analysis_db_path)
        if df.empty:
            return []
        # already ordered feature_id, best_score DESC — first row per group
        # is the feature's single best hit
        top = df.drop_duplicates(subset="feature_id", keep="first")
        return _dataframe_to_records(top)

    @Slot(str, int, result=list)
    def getAnnotationCandidates(self, analysis_db_path: str, feature_id: int) -> list:
        """Every candidate (sample, scan, library hit) for one feature.

        Args:
            analysis_db_path: The analysis' SQLite database.
            feature_id: The feature to fetch candidates for.

        Returns:
            Records from `analysis_db.load_ms2_annotations_for_feature`.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return []
        df = analysis_db.load_ms2_annotations_for_feature(analysis_db_path, feature_id)
        return _dataframe_to_records(df)

    def _write_spectrum_html(self, html: str) -> str:
        self._spectrum_counter += 1
        path = Path(tempfile.gettempdir()) / (
            f"msianalyzer_spectrum_{os.getpid()}_{self._spectrum_counter}.html"
        )
        path.write_text(html, encoding="utf-8")
        if self._spectrum_prev_path is not None:
            self._spectrum_prev_path.unlink(missing_ok=True)
        self._spectrum_prev_path = path
        return path.as_uri()

    def _write_mirror_plot_html(self, html: str) -> str:
        self._mirror_plot_counter += 1
        path = Path(tempfile.gettempdir()) / (
            f"msianalyzer_mirror_plot_{os.getpid()}_{self._mirror_plot_counter}.html"
        )
        path.write_text(html, encoding="utf-8")
        if self._mirror_plot_prev_path is not None:
            self._mirror_plot_prev_path.unlink(missing_ok=True)
        self._mirror_plot_prev_path = path
        return path.as_uri()

    @Slot(str, int, result=str)
    def getMirrorPlotUrl(self, analysis_db_path: str, annotation_id: int) -> str:
        """The empirical-vs-library mirror plot for one `ms2_annotations`
        row, as a `file://` URL for a `WebEngineView`'s `url` to load.

        Args:
            analysis_db_path: The analysis' SQLite database.
            annotation_id: Primary key in `ms2_annotations`.

        Returns:
            A `file://` URL to a self-contained HTML page (full inline
            Plotly.js — `include_plotlyjs=True` — so it stays viewable
            without network access). Written to disk rather than returned
            as HTML for `loadHtml()`/`setHtml()` to load directly: that
            API silently fails past Qt's ~2MB limit, and embedding
            Plotly.js alone is already ~4.6MB. On any failure (unknown id,
            no stored filtered spectra) the page is a short `<p>` error
            message instead — the popup stays usable rather than showing
            a blank/broken view.
        """
        try:
            fig = Plotter().plot_ms2_annotation(analysis_db_path, annotation_id)
        except (ValueError, OSError) as e:
            logger.warning("mirror plot for annotation %s failed: %s", annotation_id, e)
            html = f"<p style='font-family: sans-serif; color: #900;'>{e}</p>"
        else:
            html = fig.to_html(full_html=False, include_plotlyjs=True)
        return self._write_mirror_plot_html(html)

    @Slot(str, result=list)
    def getSamples(self, analysis_db_path: str) -> list:
        """Samples for the MS1 spectra section's sample selector.

        Args:
            analysis_db_path: The analysis' SQLite database.

        Returns:
            Records (`sample_id`, `name`, `polarity`) from
            `analysis_db.load_samples`.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return []
        return _dataframe_to_records(analysis_db.load_samples(analysis_db_path))

    @Slot(str, str, int, result=str)
    def getSpectrumUrl(self, analysis_db_path: str, run_id: str, sample_id: int) -> str:
        """One sample's filtered MS1 spectrum, as a `file://` URL for a
        `WebEngineView`'s `url` to load.

        The returned page registers itself on the page's `QWebChannel`
        (already wired to this object as `"analysisBridge"` by the QML side)
        and calls `onSpectrumPointClicked` whenever the plot is clicked.

        Args:
            analysis_db_path: The analysis' SQLite database.
            run_id: The run's id (== `AnalysisModel.runId`) — spectra are
                attributed to the run that produced them.
            sample_id: Row id in the analysis `samples` table.

        Returns:
            A `file://` URL to a self-contained, click-aware HTML page
            (full inline Plotly.js). Written to disk rather than returned
            as HTML for `loadHtml()`/`setHtml()` to load directly: that
            API silently fails past Qt's ~2MB limit, and embedding
            Plotly.js alone is already ~4.6MB — every spectrum plot was
            silently failing to render before this. A short `<p>` error
            page instead if no filtered spectrum was ever saved for that
            sample.
        """
        try:
            mz, intensity = load_aggregated_spectra(
                analysis_db_path,
                run_id=run_id,
                command_name="filter_spectra",
                sample_id=sample_id,
            )
        except (TypeError, sqlite3.OperationalError) as e:
            logger.warning(
                "spectrum for sample %s (run %s) not found: %s", sample_id, run_id, e
            )
            html = "<p style='font-family: sans-serif; color: #900;'>No spectrum found for this sample.</p>"
            return self._write_spectrum_html(html)

        fig = Plotter.plot_spectra(mz, intensity)
        body = fig.to_html(
            full_html=False, include_plotlyjs=True, div_id=_SPECTRUM_DIV_ID
        )
        click_script = f"""
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<script>
new QWebChannel(qt.webChannelTransport, function(channel) {{
    var bridge = channel.objects.analysisBridge;
    var plotDiv = document.getElementById('{_SPECTRUM_DIV_ID}');
    if (bridge && plotDiv) {{
        plotDiv.on('plotly_click', function(data) {{
            if (data.points.length > 0) {{
                bridge.onSpectrumPointClicked(data.points[0].x);
            }}
        }});
    }}
}});
</script>
"""
        return self._write_spectrum_html(body + click_script)

    @Slot(str, float, int, result=dict)
    def getFeatureDetail(self, analysis_db_path: str, mz: float, top_n: int) -> dict:
        """Everything the MS1 spectra section's side panel shows for one click.

        Maps `mz` to the nearest feature, then looks up which samples have
        it (present vs. filtered-out/absent — not distinguished), its MS2
        count, and its top-N annotation hits (best row per distinct
        compound, by rank/score; empty when annotation never ran).

        Args:
            analysis_db_path: The analysis' SQLite database.
            mz: The clicked m/z.
            top_n: How many annotation hits to include, user-selectable.

        Returns:
            `{"feature_id", "mz", "samples_present", "samples_absent",
            "n_ms2", "top_hits"}`, or `{}` when there are no features at all
            (or the db path doesn't exist).
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return {}

        feature = analysis_db.find_nearest_feature(analysis_db_path, mz)
        if feature is None:
            return {}

        members = feature["members"]
        samples_present = sorted(name for name, idx in members.items() if idx is not None)
        samples_absent = sorted(name for name, idx in members.items() if idx is None)
        n_ms2 = analysis_db.load_feature_ms2_count(analysis_db_path, feature["feature_id"])

        candidates = analysis_db.load_ms2_annotations_for_feature(
            analysis_db_path, feature["feature_id"]
        )
        top_hits = []
        if not candidates.empty:
            best_per_compound = candidates.drop_duplicates(
                subset="inchikey", keep="first"
            )
            top_hits = _dataframe_to_records(best_per_compound.head(max(top_n, 0)))

        return {
            "feature_id": feature["feature_id"],
            "mz": feature["mz"],
            "samples_present": samples_present,
            "samples_absent": samples_absent,
            "n_ms2": n_ms2,
            "top_hits": top_hits,
        }

    @Slot(str, result=list)
    def getFeatureList(self, analysis_db_path: str) -> list:
        """Every feature for the Visual Inspection section's feature selector.

        Args:
            analysis_db_path: The analysis' SQLite database.

        Returns:
            Records (`feature_id`, `mz`, `compound_name`) from
            `analysis_db.load_feature_list`, ordered by `mz`.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return []
        return _dataframe_to_records(analysis_db.load_feature_list(analysis_db_path))
