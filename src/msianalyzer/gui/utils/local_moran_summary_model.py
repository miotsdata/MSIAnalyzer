from __future__ import annotations

import math

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Property, Signal, Slot

from msianalyzer.core.spatial.local_moran import pct_above_threshold
from msianalyzer.gui.utils.spatial_correlation_model import _matches_mz


class LocalMoranSummaryModel(QAbstractTableModel):
    """Backs `SpatialCorrelationSection.qml`'s second `TableView` — one row
    per feature, one column per sample, cell value the percentage of that
    sample's pixels whose *local* Moran's I exceeds this model's own
    `threshold` property.

    Unlike `SpatialCorrelationModel` (whose cells are pre-computed scores,
    fixed once fetched), this model's `_all_rows` hold the *raw per-pixel*
    `local_moran_values` array for each (feature, sample) — read from
    `adata.layers["localMoranI"]` (`core.spatial.local_moran.compute_local_moran_layer`)
    — and `threshold` is mutable, settable live from QML. Every cell,
    every sort key, is computed from the raw array *at the current
    threshold* on demand, in `data()`/`_sort_key()`; changing `threshold`
    just re-applies both against the same already-loaded arrays (see
    `setThreshold`), no new fetch needed. This is the whole point of
    storing the full per-pixel arrays instead of one pre-thresholded
    number per feature, the way this pass' first version did: a
    poorly-chosen threshold (local Moran's I has no fixed range — see
    `SpatialCorrelationConfig.local_threshold`) used to mean "0 features
    above threshold" with no way to recover short of a full re-run.

    Otherwise a deliberate near-duplicate of `SpatialCorrelationModel`
    rather than a shared base class — see that class's own docstring for
    the full reasoning (owned by `AnalysisBridge`, survives this section's
    lazy `Loader` being torn down on tab switches, `TableView` over a
    `Repeater` for real virtualized rendering, only two of these tables
    exist so a shared abstraction wouldn't meaningfully reduce the code).
    """

    MzRole = Qt.UserRole + 1

    rowsLoadingChanged = Signal()
    queriesChanged = Signal()
    sortStateChanged = Signal()
    samplesChanged = Signal()
    thresholdChanged = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # [{"mz", "compound_name", <sample>: np.ndarray | None}, ...] — the
        # per-sample value is the *raw* per-pixel local Moran's I array,
        # not a pre-thresholded percentage.
        self._all_rows: list[dict] = []
        self._samples: list[str] = []
        self._visible: list[dict] = []
        self._sort_column: str = "mz"
        self._sort_ascending: bool = True
        self._name_query: str = ""
        self._mz_query: str = ""
        self._rows_loading: bool = False
        # Seeded from SpatialCorrelationConfig.local_threshold (the value
        # this analysis' pipeline run was configured with) by
        # AnalysisBridge.getLocalMoranSummaryModel, before any rows arrive
        # — see that method's own docstring. 1.0 here is only a fallback
        # for a model nobody has seeded yet (e.g. a fresh, disconnected
        # instance), matching SpatialCorrelationConfig's own dataclass
        # default.
        self._threshold: float = 1.0

    # ------------------------------------------------------------------
    # QAbstractTableModel overrides
    # ------------------------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._visible)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else 1 + len(self._samples)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._visible[index.row()]
        column = index.column()
        if role == self.MzRole:
            return row["mz"]
        if role != Qt.DisplayRole:
            return None
        if column == 0:
            return self._feature_label(row)
        values = row.get(self._samples[column - 1])
        if values is None:
            return "–"
        return f"{pct_above_threshold(values, self._threshold):.1f}%"

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if orientation != Qt.Horizontal or role != Qt.DisplayRole:
            return None
        return "Feature" if section == 0 else self._samples[section - 1]

    def roleNames(self) -> dict:
        return {Qt.DisplayRole: b"display", self.MzRole: b"mz"}

    # ------------------------------------------------------------------
    # Data plumbing
    # ------------------------------------------------------------------

    @staticmethod
    def _feature_label(row: dict) -> str:
        name = row.get("compound_name")
        return f"{row['mz']:.4f}: {name}" if name else f"m/z {row['mz']:.4f}"

    def _sort_key(self, column: str, ascending: bool):
        """See `SpatialCorrelationModel._sort_key` — same reasoning
        (missing values sort last in both directions), plus: the sort key
        for a sample column is *itself* a function of the current
        `threshold`, recomputed from that sample's raw array every time —
        sort order can genuinely change as the threshold changes."""
        if column == "mz":
            return lambda r: (0, r["mz"] if ascending else -r["mz"])

        def key(r):
            values = r.get(column)
            if values is None:
                return (1, 0.0)
            pct = pct_above_threshold(values, self._threshold)
            return (0, pct if ascending else -pct)

        return key

    def _apply_filter_and_sort(self) -> None:
        rows = self._all_rows
        if self._name_query.strip():
            q = self._name_query.strip().lower()
            rows = [r for r in rows if r.get("compound_name") and q in r["compound_name"].lower()]
        if self._mz_query.strip():
            rows = [r for r in rows if _matches_mz(self._mz_query, r["mz"])]
        self.beginResetModel()
        self._visible = sorted(
            rows, key=self._sort_key(self._sort_column, self._sort_ascending)
        )
        self.endResetModel()

    def setRows(self, rows: list[dict], samples: list[str]) -> None:
        """See `SpatialCorrelationModel.setRows` — same reasoning: sort/
        filter (and here, threshold) state is preserved across a fresh
        fetch, and `samples` is notified via a dedicated signal (not
        reused from `sortBy()`) so a binding made before the first fetch
        lands still re-evaluates once it does."""
        self._all_rows = rows
        samples_changed = samples != self._samples
        self._samples = samples
        self._apply_filter_and_sort()
        if samples_changed:
            self.samplesChanged.emit()

    # ------------------------------------------------------------------
    # QML-facing API
    # ------------------------------------------------------------------

    @Slot(str)
    def sortBy(self, column: str) -> None:
        if column == self._sort_column:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_column = column
            self._sort_ascending = True
        self._apply_filter_and_sort()
        self.sortStateChanged.emit()

    @Slot(int, result=float)
    def mzForRow(self, row: int) -> float:
        """The underlying feature m/z for a *visible* row index — for the
        right-click "Show spatial distribution" menu (same handoff as
        `SpatialCorrelationModel.mzForRow`, but also switches Visual
        Inspection's layer to Local Moran's I — see
        `SpatialCorrelationSection.qml`)."""
        if 0 <= row < len(self._visible):
            return float(self._visible[row]["mz"])
        return math.nan

    def _get_samples(self) -> list:
        return self._samples

    samples = Property("QVariantList", _get_samples, notify=samplesChanged)

    def _get_name_query(self) -> str:
        return self._name_query

    def _set_name_query(self, value: str) -> None:
        if value == self._name_query:
            return
        self._name_query = value
        self._apply_filter_and_sort()
        self.queriesChanged.emit()

    nameQuery = Property(str, _get_name_query, _set_name_query, notify=queriesChanged)

    def _get_mz_query(self) -> str:
        return self._mz_query

    def _set_mz_query(self, value: str) -> None:
        if value == self._mz_query:
            return
        self._mz_query = value
        self._apply_filter_and_sort()
        self.queriesChanged.emit()

    mzQuery = Property(str, _get_mz_query, _set_mz_query, notify=queriesChanged)

    def _get_rows_loading(self) -> bool:
        return self._rows_loading

    def setRowsLoading(self, value: bool) -> None:
        if value == self._rows_loading:
            return
        self._rows_loading = value
        self.rowsLoadingChanged.emit()

    rowsLoading = Property(bool, _get_rows_loading, notify=rowsLoadingChanged)

    def _get_threshold(self) -> float:
        return self._threshold

    def setThreshold(self, value: float) -> None:
        """Live re-threshold — every visible cell and every sample-column
        sort key is a function of `self._threshold`, so re-applying the
        current sort/filter is all that's needed to update the whole
        table; no new data fetch, no `.h5ad` I/O, just a resort/redisplay
        over the already-loaded raw arrays."""
        if value == self._threshold:
            return
        self._threshold = value
        self._apply_filter_and_sort()
        self.thresholdChanged.emit()

    threshold = Property(float, _get_threshold, setThreshold, notify=thresholdChanged)

    @Property(str, notify=sortStateChanged)
    def sortColumn(self) -> str:
        return self._sort_column

    @Property(bool, notify=sortStateChanged)
    def sortAscending(self) -> bool:
        return self._sort_ascending
