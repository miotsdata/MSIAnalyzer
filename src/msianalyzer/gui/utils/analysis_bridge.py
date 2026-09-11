import logging
from pathlib import Path

import pandas as pd
from PySide6.QtCore import QObject, Slot

from msianalyzer.core import analysis_db
from msianalyzer.core.plotting.plotter import Plotter

logger = logging.getLogger(__name__)

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
    """

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

    @Slot(str, int, result=str)
    def getMirrorPlotHtml(self, analysis_db_path: str, annotation_id: int) -> str:
        """A self-contained HTML `<div>` with the empirical-vs-library mirror
        plot for one `ms2_annotations` row, for a `WebEngineView` to load.

        Args:
            analysis_db_path: The analysis' SQLite database.
            annotation_id: Primary key in `ms2_annotations`.

        Returns:
            Full inline-Plotly.js HTML (`include_plotlyjs=True` — the popup
            loads this via `loadHtml`, not a file/CDN, so it must be fully
            self-contained). A short `<p>` error message instead, on any
            failure (unknown id, no stored filtered spectra) — the popup
            stays usable rather than showing a blank/broken view.
        """
        try:
            fig = Plotter().plot_ms2_annotation(analysis_db_path, annotation_id)
        except (ValueError, OSError) as e:
            logger.warning("mirror plot for annotation %s failed: %s", annotation_id, e)
            return f"<p style='font-family: sans-serif; color: #900;'>{e}</p>"
        return fig.to_html(full_html=False, include_plotlyjs=True)
