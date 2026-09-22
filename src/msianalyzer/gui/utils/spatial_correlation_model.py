from __future__ import annotations

from PySide6.QtCore import QObject, Property, Signal, Slot


class SpatialCorrelationModel(QObject):
    """Backs `SpatialCorrelationSection.qml`'s table — one row per feature,
    one column (moran_i) per sample folded into that row's dict — plus the
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
    A plain, long-lived `QObject` carries both the rows and the current
    sort/filter state together; re-binding the QML section's `model`
    property to the same instance on a later visit picks up exactly where
    the user left off, with no QML-side persistence logic at all.

    Deliberately a plain `QObject` exposing a `visibleRows` list
    property, not a `QAbstractTableModel` bound to a `TableView` — tried
    that first, and `TableView`/`HorizontalHeaderView` turned out not to
    instantiate any delegate items at all under this project's offscreen
    GUI test harness (`contentWidth`/`topRow`/`bottomRow` compute
    correctly, but zero delegate items are ever created, with no
    warnings) — a real, verified environment limitation, not a modelling
    mistake. Sorting/filtering here happens in Python instead of QML's
    `Array.sort`/`filter` (still a real win — it's what actually
    survives the Loader's destroy/rebuild cycle), but rendering stays on
    the already-working, already-tested `Repeater`-in-`Flickable`
    pattern this codebase's other sections use.
    """

    rowsLoadingChanged = Signal()
    queriesChanged = Signal()
    sortStateChanged = Signal()
    visibleRowsChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._all_rows: list[dict] = []  # [{"mz", "compound_name", <sample>: moran_i, ...}]
        self._samples: list[str] = []
        self._visible_rows: list[dict] = []
        self._sort_column: str = "mz"
        self._sort_ascending: bool = True
        self._name_query: str = ""
        self._mz_query: str = ""
        self._rows_loading: bool = False

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
        sorted_rows = sorted(rows, key=self._sort_key(self._sort_column, self._sort_ascending))
        self._visible_rows = [
            {**r, "label": self._feature_label(r)} for r in sorted_rows
        ]
        self.visibleRowsChanged.emit()

    def setRows(self, rows: list[dict], samples: list[str]) -> None:
        """Replace the model's data wholesale — the current sort/filter
        state is preserved and reapplied, not reset, so a fresh fetch
        (e.g. after re-running with `spatial_correlation.enabled: true`)
        doesn't silently undo what the user had set up.
        """
        self._all_rows = rows
        self._samples = samples
        self._apply_filter_and_sort()

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

    def _get_visible_rows(self) -> list:
        return self._visible_rows

    visibleRows = Property("QVariantList", _get_visible_rows, notify=visibleRowsChanged)

    def _get_samples(self) -> list:
        return self._samples

    samples = Property("QVariantList", _get_samples, notify=visibleRowsChanged)

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
