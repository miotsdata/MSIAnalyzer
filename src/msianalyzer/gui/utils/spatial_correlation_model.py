from __future__ import annotations

import math

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Property, Signal, Slot


class SpatialCorrelationModel(QAbstractTableModel):
    """Backs `SpatialCorrelationSection.qml`'s `TableView` — one row per
    feature, one column per sample (column 0 is the feature itself:
    representative compound name when annotated, else `m/z <value>`,
    same convention as Visual Inspection's own feature picker) — plus the
    view state (sort column/order, name/m/z search text) that goes with
    it.

    Owned by `AnalysisBridge` (`getSpatialCorrelationModel`), cached one
    instance per analysis — **not** constructed by the QML section, and
    specifically **not** destroyed when that section's lazy `Loader`
    deactivates on a tab switch (`AnalysisPage.qml`'s `spatialCorrLoader`,
    `active` only while its tab is current). That's the whole point of
    this class: the QML section used to hold `rows`/`sortColumn`/
    `nameQuery` etc. as plain QML properties, all of which reset to
    defaults every time the section was torn down and rebuilt — reported
    directly ("if I go back I don't have the same table as before, I
    might have sorted for a column"). Caching just the fetched *rows* in
    Python (an earlier version of this fix) wasn't enough on its own,
    because the sort/filter *state* still lived in the doomed QML item.
    A `QAbstractTableModel` instance owned here, by this long-lived
    `AnalysisBridge`, carries both the rows and the current sort/filter
    state together; re-binding `TableView.model` to the same instance on
    a later visit picks up exactly where the user left off, with no
    QML-side persistence logic at all.

    **Second revision, same as the state-persistence fix but for actual
    table-rendering performance**: the first version of this table used a
    plain `QObject` (not `QAbstractTableModel`) exposing a whole
    `visibleRows` list, rendered via a `Repeater`-in-`Flickable` (the
    established pattern elsewhere in this codebase, e.g.
    `AnnotationsSection.qml`) because `TableView`/`HorizontalHeaderView`
    were found not to instantiate any delegate items at all under this
    project's *offscreen GUI test harness* (verified directly — see
    `tests/gui/utils/test_spatial_correlation_model.py`'s git history for
    the standalone repro). That rendering choice turned out to be a real
    performance problem in the actual running app, not just a test-only
    concern: every sort/filter destroyed and rebuilt *every* row's
    delegate (a nested `Repeater` per row for the sample columns too —
    hundreds to low thousands of `QQuickItem`s on a real project),
    reported directly as "ordering the table is still super slow." A
    `TableView` bound to a real `QAbstractTableModel` only asks `data()`
    for whatever cells are currently visible (tens, not thousands) —
    sorting/filtering here still fully re-sorts the Python-side list
    (`beginResetModel`/`endResetModel`), but nothing QML-side has to
    rebuild beyond the visible viewport. Traded away: automated GUI-level
    interaction tests for this specific page (click-to-sort, right-click)
    can't run in the offscreen harness, since no delegates render there
    at all; the model's own sort/filter logic is still fully covered by
    plain Python unit tests (no QML involved), and real-device
    verification in the actual app is expected before trusting the
    rendering side, per this project's usual GUI workflow.
    """

    MzRole = Qt.UserRole + 1

    rowsLoadingChanged = Signal()
    queriesChanged = Signal()
    sortStateChanged = Signal()
    samplesChanged = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._all_rows: list[dict] = []  # [{"mz", "compound_name", <sample>: moran_i, ...}]
        self._samples: list[str] = []
        self._visible: list[dict] = []
        self._sort_column: str = "mz"
        self._sort_ascending: bool = True
        self._name_query: str = ""
        self._mz_query: str = ""
        self._rows_loading: bool = False

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
        value = row.get(self._samples[column - 1])
        return "–" if value is None else f"{value:.3f}"

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
        """Bakes direction into the key itself (sign-flip the numeric
        part) rather than sorting once and reversing the whole list —
        missing values (`None`, this sample never scored this feature)
        need to sort last in *both* directions, and a whole-list reverse
        would instead flip them to first on descending, which reads as a
        bug ("why did the blank rows jump to the top").
        """
        if column == "mz":
            return lambda r: (0, r["mz"] if ascending else -r["mz"])

        def key(r):
            value = r.get(column)
            if value is None:
                return (1, 0.0)
            return (0, value if ascending else -value)

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
        """Replace the model's data wholesale — the current sort/filter
        state is preserved and reapplied, not reset, so a fresh fetch
        (e.g. after re-running with `spatial_correlation.enabled: true`)
        doesn't silently undo what the user had set up.

        Real bug this fixed: `samples`'s Property used to declare
        `notify=sortStateChanged`, which is only ever emitted by
        `sortBy()` — never by this method. QML reads `model.samples`
        once when the section first binds to it (before the background
        fetch finishes, so it reads `[]`), and with no notify signal
        firing when the real data lands, that binding never
        re-evaluates. Reported directly: the tab shows "no correlation
        run" on the very first visit, but works after leaving and coming
        back — because leaving and coming back destroys and rebuilds the
        whole section, which re-reads `model.samples` fresh (by then
        populated) rather than relying on a notification that was never
        going to fire.
        """
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
        right-click "Show spatial distribution" menu, which needs the
        real m/z regardless of which column/row the click landed on or
        how the table is currently sorted."""
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

    @Property(str, notify=sortStateChanged)
    def sortColumn(self) -> str:
        return self._sort_column

    @Property(bool, notify=sortStateChanged)
    def sortAscending(self) -> bool:
        return self._sort_ascending


def _matches_mz(query: str, mz: float) -> bool:
    """Same syntax as `Utils/SearchQuery.js`'s `matchesMz` (a value or a
    `min-max` range) — reimplemented here in Python since this model has
    no JS engine to call into; kept in exact sync by hand, there being no
    practical way to share one implementation across the language
    boundary for a function this small.
    """
    q = query.strip()
    if not q:
        return True
    if "-" in q[1:]:
        parts = q.split("-")
        if len(parts) == 2:
            try:
                a, b = float(parts[0]), float(parts[1])
            except ValueError:
                return False
            lo, hi = min(a, b), max(a, b)
            return lo <= mz <= hi
    try:
        value = float(q)
    except ValueError:
        return False
    return abs(mz - value) <= 0.01
