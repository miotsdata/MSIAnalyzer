import base64
import logging
import os
import sqlite3
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from PySide6.QtCore import QObject, Signal, Slot

from msianalyzer.core import analysis_db
from msianalyzer.core.plotting.mirror_plot_raster import render_mirror_plot_png
from msianalyzer.core.plotting.plotter import Plotter
from msianalyzer.core.spectra.average_spectra import load_aggregated_spectra
from msianalyzer.gui.utils.heatmap_provider import HeatmapImageProvider
from msianalyzer.gui.utils.mirror_plot_worker import MirrorPlotWorker

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

_EMPTY_ANNOTATION_METADATA = {
    "compound_name": "",
    "compound_formula": "",
    "inchikey": "",
    "library_name": "",
    "score": None,
    "dot_product_score": None,
    "coverage_score": None,
    "lib_coverage": None,
    "emp_coverage": None,
    "n_matched_peaks": None,
    "n_lib_peaks": None,
    "n_emp_peaks_raw": None,
    "n_emp_peaks_filtered": None,
    "scan_id": None,
    "precursor_mz": None,
    "fragment_ppm_tolerance": None,
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


def _categorize_peaks(mz_array: np.ndarray, feature_categories: pd.DataFrame) -> np.ndarray:
    """Nearest-feature category (`"no_ms2"`/`"annotated"`/`"non_annotated"`)
    for each peak in `mz_array` — which of `feature_categories`'s `mz`
    values each peak is closest to, then that feature's `category`. Feeds
    `Plotter.plot_spectra`'s coloring of the MS1 Spectra section's plot.

    Args:
        mz_array: The spectrum's peak m/z values.
        feature_categories: `analysis_db.load_feature_categories`'s result
            — must be sorted by `mz` ascending (as that function returns
            it), since matching is a sorted-array binary search.

    Returns:
        An array parallel to `mz_array`. `"non_annotated"` for every peak
        when there are no features at all (nothing to match against).
    """
    mz_array = np.asarray(mz_array)
    if feature_categories.empty:
        return np.full(mz_array.shape, "non_annotated", dtype=object)

    feature_mz = feature_categories["mz"].to_numpy()
    feature_category = feature_categories["category"].to_numpy()

    idx = np.searchsorted(feature_mz, mz_array)
    idx = np.clip(idx, 1, len(feature_mz) - 1)
    left_closer = (mz_array - feature_mz[idx - 1]) <= (feature_mz[idx] - mz_array)
    idx = np.where(left_closer, idx - 1, idx)
    return feature_category[idx]


class AnalysisBridge(QObject):
    """Read-only data access for the Analysis workspace's sections.

    Stateless — every method takes the analysis' `analysis_db_path`/
    `out_dir` explicitly (from an `AnalysisModel`) rather than holding its
    own "current analysis" state. Local SQLite reads are fast enough to
    stay synchronous; no QThread/worker needed for most of them, unlike
    `RunWorker`. Methods are called directly from QML (unlike `CoreBridge`,
    whose methods are only ever invoked from `Application`), so they're
    named to read naturally as QML calls — camelCase, matching
    `Router.toLocalPath`.

    Two exceptions to "QML calls in, a value flows straight back out":

    - `spectrumPointClicked`: the MS1 spectra section's `WebEngineView`
      registers this object on a `WebChannel`, and the JS embedded in
      `getSpectrumUrl`'s output calls `onSpectrumPointClicked` when the
      user clicks a point on the plot — QML listens for the resulting
      signal to update the feature-detail side panel.
    - `mirrorPlotReady`: `requestMirrorPlot` (unlike every other method
      here) does *not* return its result directly — it can involve
      `emp_source`/`lib_source="raw"`, real file I/O against a raw
      per-sample database or a spectral library file that isn't
      guaranteed to be fast or even local (a network mount). Running that
      synchronously on the call-in thread — which for a `@Slot` invoked
      from QML is the GUI thread — blocked Qt's entire event loop for as
      long as the I/O took, reported as the app going fully unresponsive
      ("python is not responding") rather than a normal wait. Offloaded
      to `MirrorPlotWorker` (a `QThread`) instead; QML calls
      `requestMirrorPlot` and listens for `mirrorPlotReady` instead of
      using a return value.
    """

    spectrumPointClicked = Signal(float)
    mirrorPlotReady = Signal(str)

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
        # Kept alive while running — nothing else holds a reference to it
        # (same pattern as CoreBridge._run_worker). Replacing it (a new
        # request before the previous one finished) is also how a stale
        # result gets discarded: _onMirrorPlotSucceeded/_onMirrorPlotFailed
        # check `self.sender() is self._mirror_plot_worker` before
        # emitting, so toggling emp/lib source quickly can't let an
        # in-flight older (e.g. slower raw-source) request overwrite a
        # newer result.
        self._mirror_plot_worker: MirrorPlotWorker | None = None

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

    @Slot(list, result=list)
    def getObsColumns(self, sample_names: list) -> list:
        """`adata.obs` columns Visual Inspection can overlay instead of a
        feature — e.g. `tic`, `rt`. See `HeatmapImageProvider.getObsColumns`.

        Args:
            sample_names: Samples to look for a readable `.h5ad` in —
                normally every sample of the analysis (the schema is the
                same across all of them), not just the visible ones.

        Returns:
            `[{"name": ..., "numeric": bool}, ...]`.
        """
        return self.heatmap_provider.getObsColumns(sample_names)

    @Slot(list, str, result=dict)
    def getObsValueRange(self, sample_names: list, obs_column: str) -> dict:
        """The real (min, max) of one numeric `obs` column across
        `sample_names` — the `obs`-column analogue of `getFeatureValueRange`.
        See `HeatmapImageProvider.getObsValueRange`.

        Returns:
            `{"vmin": ..., "vmax": ...}`.
        """
        return self.heatmap_provider.getObsValueRange(sample_names, obs_column)

    @Slot(list, str, result=list)
    def getObsCategories(self, sample_names: list, obs_column: str) -> list:
        """Every distinct category of one discrete `obs` column, combined
        across `sample_names`, colored in the fixed order every tile's
        render call must also use. See
        `HeatmapImageProvider.getObsCategories`.

        Args:
            sample_names: Normally every sample of the analysis, not just
                the visible ones, so colors stay stable as visibility is
                toggled.

        Returns:
            `[{"category": ..., "color": "#rrggbb"}, ...]`, sorted.
        """
        return self.heatmap_provider.getObsCategories(sample_names, obs_column)

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

    @Slot(str, int, int, result=list)
    def getFeatureTopHits(self, analysis_db_path: str, feature_id: int, top_n: int) -> list:
        """Top-N annotation hits for one feature — best row per distinct
        compound, including which sample each hit came from.

        Same "best per compound" selection as the MS1 spectra section's
        `getFeatureDetail`'s `top_hits` (kept as a standalone method here
        since the Annotations section starts from a picked feature
        directly, not a clicked spectrum point).

        Args:
            analysis_db_path: The analysis' SQLite database.
            feature_id: The feature to fetch hits for.
            top_n: How many hits to return, user-selectable.

        Returns:
            Records from `analysis_db.load_ms2_annotations_for_feature`
            (so each includes `id`, `sample_name`, `library_name`, `score`,
            etc.), kept to the top-scoring row per `inchikey` and capped at
            `top_n`. Empty when the feature has no annotation rows.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return []
        df = analysis_db.load_ms2_annotations_for_feature(analysis_db_path, feature_id)
        if df.empty:
            return []
        best_per_compound = df.drop_duplicates(subset="inchikey", keep="first")
        return _dataframe_to_records(best_per_compound.head(max(top_n, 0)))

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
    def getBasicMirrorPlotImage(self, analysis_db_path: str, annotation_id: int) -> str:
        """A fast, static empirical-vs-library mirror plot for one
        `ms2_annotations` row, always from the stored *filtered* spectra
        — the Annotations section's inline default view.

        Always synchronous: unlike `requestMirrorPlot`, this never touches
        a raw per-sample database or a library file (only the already
        max-normalised, pre-computed arrays `ms2_annotations` itself
        stores), so there's no slow/remote I/O risk to move off the GUI
        thread, and no `WebEngineView`/Chromium renderer needed just to
        show it.

        Args:
            analysis_db_path: The analysis' SQLite database.
            annotation_id: Primary key in `ms2_annotations`.

        Returns:
            A `data:image/png;base64,...` URI for a QML `Image.source`.
            A 1x1 transparent PNG data URI on any failure (unknown id, no
            stored filtered spectra) instead — the panel's own stats
            text (from `getFeatureTopHits`' data) already explains why,
            so this just needs to not be a broken image.
        """
        try:
            data = Plotter().get_annotation_spectra(analysis_db_path, annotation_id)
            png = render_mirror_plot_png(
                data["empirical_mz"], data["empirical_intensity"],
                data["library_mz"], data["library_intensity"],
                data["fragment_ppm_tolerance"],
            )
        except (ValueError, OSError) as e:
            logger.warning("basic mirror plot for annotation %s failed: %s", annotation_id, e)
            # 1x1 transparent PNG — a valid image, just nothing to see.
            png = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
                "+A8AAQUBAScY42YAAAAASUVORK5CYII="
            )
        return "data:image/png;base64," + base64.b64encode(png).decode("ascii")

    @Slot(str, int, result=dict)
    def getAnnotationMetadata(self, analysis_db_path: str, annotation_id: int) -> dict:
        """Compound identity/score/coverage/peak-count fields for one
        `ms2_annotations` row — the mirror-plot detail window's side
        table (`MirrorPlotDetailWindow.qml`), kept next to the plot
        instead of packed into its title/corner annotation.

        Always synchronous, like `getBasicMirrorPlotImage`: these fields
        are plain columns (plus one resolved config value), independent
        of `emp_source`/`lib_source`, so nothing here touches a raw
        per-sample database or library file.

        Args:
            analysis_db_path: The analysis' SQLite database.
            annotation_id: Primary key in `ms2_annotations`.

        Returns:
            See `Plotter.get_annotation_metadata`; an all-empty/None dict
            (same shape) when `analysis_db_path` doesn't exist or
            `annotation_id` isn't found.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return dict(_EMPTY_ANNOTATION_METADATA)
        try:
            return Plotter().get_annotation_metadata(analysis_db_path, annotation_id)
        except ValueError as e:
            logger.warning("annotation metadata for id %s failed: %s", annotation_id, e)
            return dict(_EMPTY_ANNOTATION_METADATA)

    @Slot(str, int, str, str)
    def requestMirrorPlot(
        self, analysis_db_path: str, annotation_id: int, emp_source: str, lib_source: str
    ) -> None:
        """Start building the full interactive (Plotly) mirror plot for
        one `ms2_annotations` row on a background thread; the result
        (or an error page) arrives via `mirrorPlotReady(url)`.

        Fire-and-forget rather than a return value — see the class
        docstring for why (`emp_source`/`lib_source="raw"` can mean slow
        or remote file I/O, which must not block the GUI thread). Used by
        the Annotations section's "view details" popup, which shows a
        loading state (`LoadingOverlay`) until `mirrorPlotReady` fires.

        Args:
            analysis_db_path: The analysis' SQLite database.
            annotation_id: Primary key in `ms2_annotations`.
            emp_source: `"filtered"` or `"raw"` — which empirical spectrum
                to show (see `Plotter.plot_ms2_annotation`).
            lib_source: `"filtered"` or `"raw"` — which library spectrum
                to show.
        """
        worker = MirrorPlotWorker(analysis_db_path, annotation_id, emp_source, lib_source, self)
        worker.succeeded.connect(self._onMirrorPlotSucceeded)
        worker.failed.connect(self._onMirrorPlotFailed)
        self._mirror_plot_worker = worker
        worker.start()

    @Slot(object)
    def _onMirrorPlotSucceeded(self, data: dict) -> None:
        if self.sender() is not self._mirror_plot_worker:
            return  # superseded by a newer request — discard
        # Building the Plotly figure here (main thread), not inside
        # MirrorPlotWorker — see MirrorPlotWorker's own docstring for why
        # (a real crash) doing it on the worker thread reproduced.
        fig = Plotter().build_mirror_figure(data)
        # Fill the WebEngineView's viewport instead of the figure's own
        # fixed pixel height — same fix as MS1's spectrum plot
        # (getSpectrumUrl below).
        fig.update_layout(autosize=True)
        fig.layout.height = None
        html = fig.to_html(full_html=False, include_plotlyjs=True, config={"responsive": True})
        html += (
            "<style>html, body { margin: 0; padding: 0; "
            "height: 100%; overflow: hidden; }</style>"
        )
        self.mirrorPlotReady.emit(self._write_mirror_plot_html(html))

    @Slot(str)
    def _onMirrorPlotFailed(self, message: str) -> None:
        if self.sender() is not self._mirror_plot_worker:
            return  # superseded by a newer request — discard
        html = f"<p style='font-family: sans-serif; color: #900;'>{message}</p>"
        self.mirrorPlotReady.emit(self._write_mirror_plot_html(html))

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

        categories = _categorize_peaks(
            mz, analysis_db.load_feature_categories(analysis_db_path)
        )
        fig = Plotter.plot_spectra(mz, intensity, categories=categories)
        # Fill the WebEngineView's viewport instead of `plot_spectra`'s
        # fixed pixel height — left as-is, the plot rendered at a height
        # that rarely matched the actual container, showing a scrollbar
        # ("the top row... shouldn't be scrollable. Adapt the content to
        # the row"). `autosize` + clearing the explicit height lets
        # Plotly.js size to its container instead; `responsive` makes it
        # re-measure when the container itself resizes.
        fig.update_layout(autosize=True)
        fig.layout.height = None
        body = fig.to_html(
            full_html=False,
            include_plotlyjs=True,
            div_id=_SPECTRUM_DIV_ID,
            config={"responsive": True},
        )
        click_script = f"""
<style>html, body {{ margin: 0; padding: 0; height: 100%; overflow: hidden; }}</style>
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
