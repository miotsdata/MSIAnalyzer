import math

import numpy as np
from PySide6.QtCore import QModelIndex, Qt

from msianalyzer.gui.utils.local_moran_summary_model import LocalMoranSummaryModel


def _pixels(n_above: int, n_total: int = 10) -> np.ndarray:
    """`n_total` values, exactly `n_above` of them above the model's
    default threshold (1.0) — lets tests reason about exact percentages
    (`n_above / n_total * 100`) without hardcoding real local Moran's I
    values."""
    return np.array([2.0] * n_above + [0.0] * (n_total - n_above))


def _rows():
    return [
        {"mz": 100.0, "compound_name": "Caffeine", "s1": _pixels(9), "s2": _pixels(8)},
        {"mz": 200.0, "compound_name": None, "s1": _pixels(1), "s2": _pixels(5)},
        {"mz": 300.0, "compound_name": "Glucose", "s1": _pixels(5)},  # missing s2
    ]


def _index(model, row, col):
    return model.index(row, col, QModelIndex())


def _labels(model):
    return [model.data(_index(model, i, 0)) for i in range(model.rowCount())]


def test_shape_and_headers():
    model = LocalMoranSummaryModel()
    model.setRows(_rows(), ["s1", "s2"])

    assert model.rowCount() == 3
    assert model.columnCount() == 3  # Feature + s1 + s2
    assert model.samples == ["s1", "s2"]
    assert model.headerData(0, Qt.Horizontal) == "Feature"
    assert model.headerData(1, Qt.Horizontal) == "s1"
    assert model.headerData(2, Qt.Horizontal) == "s2"


def test_visible_rows_default_sort_by_mz_ascending():
    model = LocalMoranSummaryModel()
    model.setRows(_rows(), ["s1", "s2"])

    assert _labels(model) == [
        "100.0000: Caffeine",
        "m/z 200.0000",
        "300.0000: Glucose",
    ]


def test_cell_value_renders_as_percentage_at_default_threshold():
    model = LocalMoranSummaryModel()
    model.setRows(_rows(), ["s1", "s2"])

    row = next(i for i in range(3) if model.data(_index(model, i, 0)).startswith("100"))
    assert model.data(_index(model, row, 1)) == "90.0%"  # 9/10 pixels above 1.0


def test_missing_value_renders_as_dash():
    model = LocalMoranSummaryModel()
    model.setRows(_rows(), ["s1", "s2"])

    row = next(i for i in range(3) if model.data(_index(model, i, 0)).startswith("300"))
    assert model.data(_index(model, row, 2)) == "–"


def test_sort_by_sample_column_missing_values_sort_last_both_directions():
    model = LocalMoranSummaryModel()
    model.setRows(_rows(), ["s1", "s2"])

    model.sortBy("s2")
    assert model.sortColumn == "s2"
    assert model.sortAscending is True
    assert _labels(model)[-1] == "300.0000: Glucose"  # missing s2, sorts last ascending

    model.sortBy("s2")  # same column again -> flips direction
    assert model.sortAscending is False
    assert _labels(model)[-1] == "300.0000: Glucose"  # still last, not first, descending


def test_sort_state_persists_across_setrows():
    model = LocalMoranSummaryModel()
    model.setRows(_rows(), ["s1", "s2"])
    model.sortBy("s1")
    model.sortBy("s1")  # descending

    assert model.sortColumn == "s1"
    assert model.sortAscending is False

    model.setRows(_rows(), ["s1", "s2"])  # re-fetch, same shape
    assert model.sortColumn == "s1"
    assert model.sortAscending is False
    # s1 descending at threshold=1.0: 90% (mz 100), 50% (mz 300), 10% (mz 200)
    assert _labels(model) == [
        "100.0000: Caffeine",
        "300.0000: Glucose",
        "m/z 200.0000",
    ]


def test_name_query_filters_and_persists():
    model = LocalMoranSummaryModel()
    model.setRows(_rows(), ["s1", "s2"])

    model.nameQuery = "gluc"
    assert _labels(model) == ["300.0000: Glucose"]

    model.setRows(_rows(), ["s1", "s2"])
    assert model.nameQuery == "gluc"
    assert _labels(model) == ["300.0000: Glucose"]


def test_mz_query_range_filters():
    model = LocalMoranSummaryModel()
    model.setRows(_rows(), ["s1", "s2"])

    model.mzQuery = "150-250"
    assert _labels(model) == ["m/z 200.0000"]


def test_mz_for_row_uses_visible_not_all_rows():
    model = LocalMoranSummaryModel()
    model.setRows(_rows(), ["s1", "s2"])
    model.nameQuery = "gluc"

    assert model.mzForRow(0) == 300.0
    assert math.isnan(model.mzForRow(1))  # out of range once filtered to 1 row


def test_rows_loading_property_and_signal():
    model = LocalMoranSummaryModel()
    received = []
    model.rowsLoadingChanged.connect(lambda: received.append(model.rowsLoading))

    model.setRowsLoading(True)
    assert model.rowsLoading is True
    assert received == [True]

    model.setRowsLoading(True)  # no-op, no duplicate signal
    assert received == [True]


def test_samples_changed_emitted_only_when_sample_list_actually_changes():
    model = LocalMoranSummaryModel()
    received = []
    model.samplesChanged.connect(lambda: received.append(model.samples))

    model.setRows(_rows(), ["s1", "s2"])
    assert received == [["s1", "s2"]]

    model.setRows(_rows(), ["s1", "s2"])  # same sample list — no new signal
    assert received == [["s1", "s2"]]

    model.setRows(_rows(), ["s1", "s2", "s3"])
    assert received == [["s1", "s2"], ["s1", "s2", "s3"]]


def test_model_reset_emitted_on_setrows_and_filter():
    model = LocalMoranSummaryModel()
    received = []
    model.modelReset.connect(lambda: received.append(None))

    model.setRows(_rows(), ["s1", "s2"])
    assert len(received) == 1

    model.nameQuery = "caff"
    assert len(received) == 2


# ----------------------------------------------------------------------
# threshold: the live re-threshold this whole model exists for
# ----------------------------------------------------------------------


def test_threshold_defaults_to_one():
    model = LocalMoranSummaryModel()
    assert model.threshold == 1.0


def test_set_threshold_updates_displayed_percentages_without_a_new_fetch():
    model = LocalMoranSummaryModel()
    model.setRows(_rows(), ["s1", "s2"])

    row = next(i for i in range(3) if model.data(_index(model, i, 0)).startswith("100"))
    assert model.data(_index(model, row, 1)) == "90.0%"  # 9/10 pixels are 2.0, above 1.0

    # Raise the threshold above every pixel's value except none of the
    # "2.0" ones qualify anymore either — 0% above, same underlying data,
    # no re-fetch.
    model.threshold = 3.0
    row = next(i for i in range(3) if model.data(_index(model, i, 0)).startswith("100"))
    assert model.data(_index(model, row, 1)) == "0.0%"


def test_set_threshold_emits_threshold_changed_only_on_real_change():
    model = LocalMoranSummaryModel()
    received = []
    model.thresholdChanged.connect(lambda: received.append(model.threshold))

    model.threshold = 2.0
    assert received == [2.0]

    model.threshold = 2.0  # no-op, no duplicate signal
    assert received == [2.0]

    model.threshold = 3.0
    assert received == [2.0, 3.0]


def test_set_threshold_can_change_sort_order():
    # The real reason threshold needs to be part of the sort key, not just
    # the display: at threshold=1.0, s1 ranks mz 100 (90%) above mz 300
    # (50%) above mz 200 (10%). Raising the threshold so only mz 200's
    # pixels (all exactly 2.0) still qualify should flip that ordering.
    rows = [
        {"mz": 100.0, "compound_name": None, "s1": np.array([2.0] * 9 + [0.0])},
        {"mz": 200.0, "compound_name": None, "s1": np.array([5.0] * 1 + [0.0] * 9)},
        {"mz": 300.0, "compound_name": None, "s1": np.array([2.0] * 5 + [0.0] * 5)},
    ]
    model = LocalMoranSummaryModel()
    model.setRows(rows, ["s1"])
    model.sortBy("s1")  # ascending by default

    assert [model.mzForRow(i) for i in range(3)] == [200.0, 300.0, 100.0]

    model.threshold = 3.0  # only mz 200's pixels (value 5.0) still clear this
    assert [model.mzForRow(i) for i in range(3)] == [100.0, 300.0, 200.0]
