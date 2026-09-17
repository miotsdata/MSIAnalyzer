import base64
import logging
import os
import sqlite3
import tempfile
from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
from PySide6.QtCore import QObject, Signal, Slot

from msianalyzer.core import analysis_db
from msianalyzer.core import export as core_export
from msianalyzer.core.annotation.formula_prediction import FormulaPredictionSettings
from msianalyzer.core.plotting import roi
from msianalyzer.core.plotting.heatmap import category_color
from msianalyzer.core.plotting.mirror_plot_raster import render_mirror_plot_png
from msianalyzer.core.plotting.plotter import Plotter
from msianalyzer.core.spectra.average_spectra import load_aggregated_spectra
from msianalyzer.gui.utils.export_worker import ExportWorker
from msianalyzer.gui.utils.formula_prediction_worker import FormulaPredictionWorker
from msianalyzer.gui.utils.heatmap_provider import HeatmapImageProvider
from msianalyzer.gui.utils.mirror_plot_worker import MirrorPlotWorker
from msianalyzer.gui.utils.table_query_worker import TableQueryWorker

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
    - `formulaPredictionFinished`/`formulaPredictionFailed`:
      `predictFormulas` (see ADR 27) is fire-and-forget for the same
      reason — the *first* invocation on a machine downloads msbuddy's
      ~420MB reference database, real multi-second I/O that must not
      block the GUI thread. Offloaded to `FormulaPredictionWorker`;
      `PredictFormulaWindow.qml` calls `predictFormulas` and listens for
      these instead of using a return value.
    - `annotationTableReady`/`featureListReady`: `requestAnnotationTable`/
      `requestFeatureList` are fire-and-forget too, for the same reason —
      both queries filter `ms2_annotations` (which can hold many rows;
      one per scored candidate per scan per feature per library), and ran
      long enough on a real analysis to freeze the GUI outright (see
      ADR 33 for the query-side half of that fix; this is the other half).
      Offloaded to `TableQueryWorker`. `AnnotationsSection.qml`/
      `HeatmapControlsPanel.qml` call these and listen for the `*Ready`
      signals instead of using a return value.
    """

    spectrumPointClicked = Signal(float)
    mirrorPlotReady = Signal(str)
    formulaPredictionFinished = Signal(str)  # analysis_db_path
    formulaPredictionFailed = Signal(str, str)  # analysis_db_path, message
    annotationTableReady = Signal(list)
    featureListReady = Signal(list)
    exportFinished = Signal(str)  # human-readable "done" message
    exportFailed = Signal(str)  # human-readable error message

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
        # Same "kept alive while running, replacing it discards a stale
        # result" pattern as _mirror_plot_worker above.
        self._formula_prediction_worker: FormulaPredictionWorker | None = None
        # Same pattern again, one worker slot each — a new request (e.g.
        # the user reopening a tab) discards whatever the previous one
        # was still fetching.
        self._annotation_table_worker: TableQueryWorker | None = None
        self._feature_list_worker: TableQueryWorker | None = None
        # Same pattern once more — one slot shared by every Export menu
        # action, since only one export can sensibly run at a time from
        # one workspace.
        self._export_worker: ExportWorker | None = None

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

    @Slot(str)
    def ensureSchemaCurrent(self, analysis_db_path: str) -> None:
        """Bring an existing analysis database's schema up to date —
        called once by `AnalysisPage.qml` whenever an analysis workspace
        opens, so an analysis run before a schema change (e.g. ADR 33's
        `idx_ann_rank_feature_1` index) benefits from it without needing a
        full re-run.

        Just `analysis_db.init_analysis_db` (`connect` + `create_analysis_schema`
        + `commit`) — every statement in `create_analysis_schema` is
        `CREATE TABLE`/`CREATE INDEX IF NOT EXISTS`, so this is a cheap,
        safe no-op on an already-current schema and only ever adds
        structure, never touches data. `connect`'s own "callers issue no
        DDL" note is about concurrent per-sample worker processes racing a
        schema lock *during a pipeline run* — irrelevant here, this only
        ever runs against an already-finished analysis from a single GUI
        connection.

        Args:
            analysis_db_path: The analysis' SQLite database.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return
        analysis_db.init_analysis_db(analysis_db_path).close()

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

    @Slot(str)
    def requestAnnotationTable(self, analysis_db_path: str) -> None:
        """Start fetching one row per feature (its representative compound
        hit) on a background thread; the result arrives via
        `annotationTableReady(rows)`.

        Fire-and-forget rather than a return value — see the class
        docstring for why. Used by the Annotations section's table, which
        shows a loading state (`LoadingOverlay`, via `AnalysisPage.qml`'s
        `currentSectionReady`) until `annotationTableReady` fires.

        Args:
            analysis_db_path: The analysis' SQLite database.

        `annotationTableReady`'s rows come from
        `analysis_db.load_feature_representative_annotations` — not
        necessarily the single highest-`score` compound, see
        `AnnotateConfig.representative_score_tolerance`. Empty when
        annotation never ran (or the db path doesn't exist).
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            self.annotationTableReady.emit([])
            return
        worker = TableQueryWorker(
            analysis_db.load_feature_representative_annotations, analysis_db_path, self
        )
        worker.succeeded.connect(self._onAnnotationTableSucceeded)
        worker.failed.connect(self._onAnnotationTableFailed)
        self._annotation_table_worker = worker
        worker.start()

    @Slot(object)
    def _onAnnotationTableSucceeded(self, df: pd.DataFrame) -> None:
        if self.sender() is not self._annotation_table_worker:
            return  # superseded by a newer request — discard
        self.annotationTableReady.emit([] if df.empty else _dataframe_to_records(df))

    @Slot(str)
    def _onAnnotationTableFailed(self, message: str) -> None:
        if self.sender() is not self._annotation_table_worker:
            return  # superseded by a newer request — discard
        logger.warning("annotation table load failed: %s", message)
        self.annotationTableReady.emit([])

    @Slot(str, result=list)
    def getFeaturesForPrediction(self, analysis_db_path: str) -> list:
        """Every feature, with its current representative identity (if
        any) — backs the "Predict formula" window's feature picker.

        Args:
            analysis_db_path: The analysis' SQLite database.

        Returns:
            Records from `analysis_db.load_all_features_for_prediction`:
            `feature_id`, `mz`, `compound_name` (`None` when nothing
            matched yet — the picker's "select all unannotated" reads
            this), `best_score`. Empty when the db path doesn't exist or
            there are no features.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return []
        return _dataframe_to_records(analysis_db.load_all_features_for_prediction(analysis_db_path))

    @Slot(str, list, dict)
    def predictFormulas(
        self, analysis_db_path: str, feature_ids: list, settings_dict: dict
    ) -> None:
        """Start on-demand formula prediction for `feature_ids` on a
        background thread; completion arrives via
        `formulaPredictionFinished(analysis_db_path)` or
        `formulaPredictionFailed(analysis_db_path, message)`.

        Fire-and-forget rather than a return value — see the class
        docstring for why (the first invocation on a machine can mean a
        real, multi-second ~420MB download). Used by
        `PredictFormulaWindow.qml`'s "Run" button, which shows a
        `LoadingOverlay` until one of those two signals fires.

        Args:
            analysis_db_path: The analysis' SQLite database.
            feature_ids: Which features to predict for — user-selected in
                the window's feature picker.
            settings_dict: `{"adducts", "error_ppm", "top_n", "halogen"}`
                — see `formula_prediction.FormulaPredictionSettings`.
        """
        try:
            settings = FormulaPredictionSettings(
                adducts=list(settings_dict["adducts"]),
                error_ppm=float(settings_dict.get("error_ppm", 10.0)),
                top_n=int(settings_dict.get("top_n", 5)),
                halogen=bool(settings_dict.get("halogen", False)),
            )
        except (KeyError, ValueError, TypeError) as e:
            self.formulaPredictionFailed.emit(analysis_db_path, str(e))
            return

        worker = FormulaPredictionWorker(
            analysis_db_path, [int(f) for f in feature_ids], settings, self
        )
        worker.succeeded.connect(self._onFormulaPredictionSucceeded)
        worker.failed.connect(self._onFormulaPredictionFailed)
        self._formula_prediction_worker = worker
        worker.start()

    @Slot(str, list)
    def _onFormulaPredictionSucceeded(self, analysis_db_path: str, rows: list) -> None:
        if self.sender() is not self._formula_prediction_worker:
            return  # superseded by a newer request — discard
        self.formulaPredictionFinished.emit(analysis_db_path)

    @Slot(str, str)
    def _onFormulaPredictionFailed(self, analysis_db_path: str, message: str) -> None:
        if self.sender() is not self._formula_prediction_worker:
            return  # superseded by a newer request — discard
        self.formulaPredictionFailed.emit(analysis_db_path, message)

    @Slot(str, int, int, result=list)
    def getFeatureTopHits(self, analysis_db_path: str, feature_id: int, top_n: int) -> list:
        """Top-N annotation hits for one feature — best row per distinct
        compound (MS2), plus, if the feature has any, its top-N predicted
        formulas (see ADR 27) appended after them, regardless of which
        adduct produced each one.

        Same "best per compound" MS2 selection as the MS1 spectra
        section's `getFeatureDetail`'s `top_hits` (kept as a standalone
        method here since the Annotations section starts from a picked
        feature directly, not a clicked spectrum point).

        Args:
            analysis_db_path: The analysis' SQLite database.
            feature_id: The feature to fetch hits for.
            top_n: How many hits to return per kind, user-selectable.

        Returns:
            Records from `analysis_db.load_ms2_annotations_for_feature`
            (`kind: "ms2"`, kept to the top-scoring row per `inchikey`,
            capped at `top_n`), followed by records from
            `analysis_db.load_top_predicted_formulas_for_feature`
            (`kind: "predicted"`, `id` negated so it can never collide
            with a real `ms2_annotations.id` — this project has no
            "annotation" for a predicted row, it's a mass-decomposition
            guess, not a scored match). Empty when the feature has
            neither.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return []

        hits: list = []
        ms2_df = analysis_db.load_ms2_annotations_for_feature(analysis_db_path, feature_id)
        if not ms2_df.empty:
            best_per_compound = ms2_df.drop_duplicates(subset="inchikey", keep="first")
            best_per_compound = best_per_compound.head(max(top_n, 0)).copy()
            best_per_compound["kind"] = "ms2"
            hits.extend(_dataframe_to_records(best_per_compound))

        predicted_df = analysis_db.load_top_predicted_formulas_for_feature(
            analysis_db_path, feature_id, top_n
        )
        if not predicted_df.empty:
            predicted_df = predicted_df.copy()
            predicted_df["id"] = -predicted_df["id"]
            predicted_df["kind"] = "predicted"
            hits.extend(_dataframe_to_records(predicted_df))

        return hits

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

    @Slot(str)
    def requestFeatureList(self, analysis_db_path: str) -> None:
        """Start fetching every feature for the Visual Inspection section's
        feature selector on a background thread; the result arrives via
        `featureListReady(features)`.

        Fire-and-forget rather than a return value — see the class
        docstring for why. Used by `HeatmapControlsPanel.qml`, which shows
        a loading state until `featureListReady` fires.

        Args:
            analysis_db_path: The analysis' SQLite database.

        `featureListReady`'s features (`feature_id`, `mz`, `compound_name`)
        come from `analysis_db.load_feature_list`, ordered by `mz`.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            self.featureListReady.emit([])
            return
        worker = TableQueryWorker(analysis_db.load_feature_list, analysis_db_path, self)
        worker.succeeded.connect(self._onFeatureListSucceeded)
        worker.failed.connect(self._onFeatureListFailed)
        self._feature_list_worker = worker
        worker.start()

    @Slot(object)
    def _onFeatureListSucceeded(self, df: pd.DataFrame) -> None:
        if self.sender() is not self._feature_list_worker:
            return  # superseded by a newer request — discard
        self.featureListReady.emit([] if df.empty else _dataframe_to_records(df))

    @Slot(str)
    def _onFeatureListFailed(self, message: str) -> None:
        if self.sender() is not self._feature_list_worker:
            return  # superseded by a newer request — discard
        logger.warning("feature list load failed: %s", message)
        self.featureListReady.emit([])

    # -----------------------------------------------------------------
    # ROI Design
    # -----------------------------------------------------------------

    @staticmethod
    def _sample_h5ad_path(analysis_db_path: str, sample_name: str) -> Path:
        """Where one sample's `.h5ad` lives — same convention as
        `HeatmapImageProvider._load_adata`, factored out here since every
        ROI slot below needs it (and a drifted duplicate would silently
        defeat `HeatmapImageProvider.invalidate`'s cache eviction)."""
        return Path(analysis_db_path).parent / f"{sample_name}.h5ad"

    @staticmethod
    def _merged_h5ad_path(analysis_db_path: str) -> Path:
        return Path(analysis_db_path).parent / "merged.h5ad"

    @Slot(str, result=list)
    def getRois(self, analysis_db_path: str) -> list:
        """The analysis-wide ROI catalog — every registered name/color,
        for ROI Design's name picker and a "Show ROIs" legend.

        Args:
            analysis_db_path: The analysis' SQLite database.

        Returns:
            Records (`id`, `name`, `color`, `created_at`) from
            `analysis_db.load_rois`, ordered by name. Empty when the db
            path doesn't exist or there are no ROIs yet.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return []
        return _dataframe_to_records(analysis_db.load_rois(analysis_db_path))

    @Slot(str, result=str)
    def nextRoiColor(self, analysis_db_path: str) -> str:
        """A suggested default color for a brand-new ROI —
        `heatmap.category_color` at the catalog's current row count, so
        successive new ROIs cycle through the same tab20 palette discrete
        `obs` categories already use. Purely a suggestion: ROI Design seeds
        its color picker from this but the user can override it before
        saving (see `saveRoi`'s "existing name wins" rule for what actually
        gets persisted).

        Args:
            analysis_db_path: The analysis' SQLite database.

        Returns:
            `"#rrggbb"`.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return category_color(0)
        existing = analysis_db.load_rois(analysis_db_path)
        return category_color(len(existing))

    @Slot(str, str, result=list)
    def getSampleRois(self, analysis_db_path: str, sample_name: str) -> list:
        """One sample's saved ROI borders — for ROI Design's "already
        drawn" list and Visual Inspection's read-only "Show ROIs" overlay.

        Args:
            analysis_db_path: The analysis' SQLite database.
            sample_name: Which sample to read.

        Returns:
            `[{"name", "color", "vertices": [[col, row], ...]}, ...]`, from
            that sample's own `.h5ad` (`roi.load_sample_rois` — not the
            analysis-wide catalog, since geometry is per-sample). `[]` if
            the sample's h5ad doesn't exist or has no ROIs.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return []
        h5ad_path = self._sample_h5ad_path(analysis_db_path, sample_name)
        if not h5ad_path.exists():
            return []
        rois = roi.load_sample_rois(h5ad_path)
        return [
            {"name": name, "color": info["color"], "vertices": info["vertices"]}
            for name, info in rois.items()
        ]

    @Slot(str, str, str, str, list, result=dict)
    def saveRoi(
        self, analysis_db_path: str, sample_name: str, name: str, color: str,
        vertices: list,
    ) -> dict:
        """Save one ROI on one sample: writes the polygon + per-pixel mask
        into that sample's own `.h5ad` (`roi.save_roi_to_sample`), keeps
        `merged.h5ad` in sync if it exists (`roi.sync_roi_to_merged`), and
        registers `name`/`color` in the analysis-wide catalog the first
        time this name is used.

        `name` may already exist in the catalog (drawing the same named
        ROI on a second sample) — in that case its registered `color`
        wins over whatever `color` this call was passed, keeping the
        name's color consistent across every sample it's drawn on.

        Args:
            analysis_db_path: The analysis' SQLite database.
            sample_name: Which sample's `.h5ad` to write into.
            name: The ROI's name.
            color: `"#rrggbb"` — only takes effect if `name` is new.
            vertices: `[[col, row], ...]`, grid-index space, >= 3 points.

        Returns:
            `{"ok": True, "pixel_count": int}` on success, or
            `{"ok": False, "error": "<message>"}` (e.g. fewer than 3
            vertices, sample h5ad missing) — surfaced by ROI Design as an
            inline error rather than a silent no-op.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return {"ok": False, "error": "No analysis database."}
        h5ad_path = self._sample_h5ad_path(analysis_db_path, sample_name)
        if not h5ad_path.exists():
            return {"ok": False, "error": f"Sample {sample_name!r} has no .h5ad file."}

        # `create_analysis_schema` is idempotent (every statement is
        # `CREATE TABLE IF NOT EXISTS`) — this is a no-op for an analysis
        # database that already has the `rois` table, and adds it for free
        # on the first ROI ever saved against an analysis run before this
        # feature existed (there is no migration system; every existing
        # analysis DB predates `rois`).
        analysis_db.init_analysis_db(analysis_db_path).close()

        existing = analysis_db.load_rois(analysis_db_path)
        existing_row = existing[existing["name"] == name] if not existing.empty else existing
        effective_color = existing_row["color"].iloc[0] if len(existing_row) else color

        try:
            pixel_count = roi.save_roi_to_sample(h5ad_path, name, effective_color, vertices)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        if existing_row.empty:
            try:
                analysis_db.register_roi(analysis_db_path, name, effective_color)
            except sqlite3.IntegrityError:
                pass  # raced with another save of the same new name — fine, row exists now

        # Recomputed rather than threaded through save_roi_to_sample's
        # return value (which is just the pixel count) — a second, cheap
        # read+mask against the just-written file, kept separate so
        # save_roi_to_sample's own signature/tests stay merged.h5ad-agnostic.
        sample_adata_mask = roi.polygon_pixel_mask(ad.read_h5ad(h5ad_path), vertices)
        roi.sync_roi_to_merged(
            self._merged_h5ad_path(analysis_db_path), sample_name, name,
            effective_color, vertices, sample_adata_mask,
        )
        self.heatmap_provider.invalidate(sample_name)
        return {"ok": True, "pixel_count": pixel_count}

    @Slot(str, str, str, result=bool)
    def deleteRoiFromSample(self, analysis_db_path: str, sample_name: str, name: str) -> bool:
        """Remove one ROI from one sample only (and, if it exists,
        `merged.h5ad`) — the catalog entry and every other sample's own
        copy are untouched; use `deleteRoiEverywhere` to also drop the
        catalog row across every sample.

        Returns:
            True if the sample actually had this ROI.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return False
        h5ad_path = self._sample_h5ad_path(analysis_db_path, sample_name)
        if not h5ad_path.exists():
            return False
        removed = roi.delete_roi_from_sample(h5ad_path, name)
        if removed:
            roi.sync_roi_deletion_to_merged(
                self._merged_h5ad_path(analysis_db_path), sample_name, name
            )
            self.heatmap_provider.invalidate(sample_name)
        return removed

    @Slot(str, list, str, result=dict)
    def deleteRoiEverywhere(self, analysis_db_path: str, sample_names: list, name: str) -> dict:
        """Remove one ROI's catalog entry and its per-sample data from
        every sample in `sample_names` — "delete this ROI" in ROI Design's
        management list, which only has the analysis' full sample list on
        hand, not which samples actually carry this ROI.

        Args:
            analysis_db_path: The analysis' SQLite database.
            sample_names: Every sample name to check/clean — normally
                every sample of the analysis, not just the one currently
                open in ROI Design.
            name: The ROI to remove.

        Returns:
            `{"catalog_removed": bool, "samples_removed": [names...]}`.
        """
        removed_samples = [
            sample_name for sample_name in sample_names
            if self.deleteRoiFromSample(analysis_db_path, sample_name, name)
        ]
        catalog_removed = False
        if analysis_db_path and Path(analysis_db_path).exists():
            catalog_removed = analysis_db.delete_roi_catalog_entry(analysis_db_path, name)
        return {"catalog_removed": catalog_removed, "samples_removed": removed_samples}

    # -----------------------------------------------------------------
    # Export menu
    # -----------------------------------------------------------------

    def _run_export(self, export_fn) -> None:
        """Shared fire-and-forget plumbing for every Export menu action —
        see the class docstring's own note on `exportFinished`/
        `exportFailed` for why this is threaded rather than a return
        value, and `ExportWorker`'s docstring for why it's generic over
        which export function runs.
        """
        worker = ExportWorker(export_fn, self)
        worker.succeeded.connect(self._onExportSucceeded)
        worker.failed.connect(self._onExportFailed)
        self._export_worker = worker
        worker.start()

    @Slot(str)
    def _onExportSucceeded(self, message: str) -> None:
        if self.sender() is not self._export_worker:
            return  # superseded by a newer request — discard
        self.exportFinished.emit(message)

    @Slot(str)
    def _onExportFailed(self, message: str) -> None:
        if self.sender() is not self._export_worker:
            return  # superseded by a newer request — discard
        logger.warning("export failed: %s", message)
        self.exportFailed.emit(message)

    @Slot(str, str)
    def exportAnnotationTable(self, analysis_db_path: str, dest_path: str) -> None:
        """Start writing the Annotation export (one row per feature,
        every tier's identity fields, including features with none at
        all) on a background thread; completion arrives via
        `exportFinished(message)`/`exportFailed(message)`.

        Args:
            analysis_db_path: The analysis' SQLite database.
            dest_path: Where to write the export, as chosen in the save
                dialog — `.txt` for tab-delimited, anything else
                (`.csv` included) for comma-delimited (see
                `core.export.export_annotation_table`).
        """
        def _do_export() -> str:
            core_export.export_annotation_table(analysis_db_path, dest_path)
            return f"Annotation table exported to {dest_path}"

        self._run_export(_do_export)

    @Slot(str, str, str, str)
    def exportIntegrationTables(
        self, analysis_db_path: str, dest_folder: str, layer: str, file_format: str
    ) -> None:
        """Start writing the Integration export (one pixel x feature
        matrix per sample) on a background thread; completion arrives via
        `exportFinished(message)`/`exportFailed(message)`.

        Args:
            analysis_db_path: The analysis' SQLite database.
            dest_folder: Destination folder, as chosen in the folder
                dialog — one `<sample_name>_integration.<ext>` file per
                sample is written into it.
            layer: `"raw"` or `"TIC"`.
            file_format: `"csv"` (comma) or `"txt"` (tab) — a folder
                dialog gives no filename to key a suffix off, so this is
                its own explicit choice (see
                `core.export.export_integration_tables`).
        """
        def _do_export() -> str:
            n = core_export.export_integration_tables(
                analysis_db_path, dest_folder, layer, file_format
            )
            return f"Integration tables exported for {n} sample(s) to {dest_folder}"

        self._run_export(_do_export)

    @Slot(str, str, str, str, str, str, str, str, str, str, bool)
    def exportVisualInspectionImages(
        self,
        analysis_db_path: str,
        dest_folder: str,
        image_format: str,
        mode: str,
        mz_str: str,
        obs_column: str,
        layer: str,
        colormap: str,
        vmin_token: str,
        vmax_token: str,
        show_rois: bool,
    ) -> None:
        """Start writing the Image export (one PNG/PDF/SVG per sample,
        matching Visual Inspection's own current display settings) on a
        background thread; completion arrives via
        `exportFinished(message)`/`exportFailed(message)`.

        Args:
            analysis_db_path: The analysis' SQLite database.
            dest_folder: Destination folder, as chosen in the folder
                dialog — one `<sample_name>.<image_format>` file per
                sample is written into it.
            image_format: `"png"`, `"pdf"`, or `"svg"`.
            mode: `"feature"` or `"obs"` — mirrors
                `HeatmapControlsPanel.inspectionMode`.
            mz_str: The selected feature's m/z as a string (`mode="feature"`
                only) — `""` otherwise.
            obs_column: The selected obs column (`mode="obs"` only) — `""`
                otherwise.
            layer: `"raw"` or `"TIC"` — mirrors
                `HeatmapControlsPanel.dataLayer`.
            colormap: Mirrors `HeatmapControlsPanel.colormap`.
            vmin_token: `HeatmapControlsPanel.vminToken()`'s own value —
                `"auto"` or a numeric string.
            vmax_token: `HeatmapControlsPanel.vmaxToken()`'s own value.
            show_rois: Mirrors Visual Inspection's "Show ROIs" checkbox.
        """
        def _do_export() -> str:
            mz = float(mz_str) if mz_str else None
            vmin = None if vmin_token == "auto" else float(vmin_token)
            vmax = None if vmax_token == "auto" else float(vmax_token)
            n = core_export.export_visual_inspection_images(
                analysis_db_path,
                dest_folder,
                image_format,
                mode,
                mz=mz,
                obs_column=obs_column or None,
                layer=layer,
                colormap=colormap,
                vmin=vmin,
                vmax=vmax,
                show_rois=show_rois,
            )
            return f"Images exported for {n} sample(s) to {dest_folder}"

        self._run_export(_do_export)
