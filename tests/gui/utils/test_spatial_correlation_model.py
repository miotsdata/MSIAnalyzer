import math

from PySide6.QtCore import QModelIndex, Qt

from msianalyzer.gui.utils.spatial_correlation_model import SpatialCorrelationModel


def _rows():
    return [
        {"mz": 100.0, "compound_name": "Caffeine", "s1": 0.9, "s2": 0.8},
        {"mz": 200.0, "compound_name": None, "s1": 0.1, "s2": 0.5},
        {"mz": 300.0, "compound_name": "Glucose", "s1": 0.5},  # missing s2
    ]


def _index(model, row, col):
    return model.index(row, col, QModelIndex())


def _labels(model):
    return [model.data(_index(model, i, 0)) for i in range(model.rowCount())]


def test_shape_and_headers():
    model = SpatialCorrelationModel()
    model.setRows(_rows(), ["s1", "s2"])

    assert model.rowCount() == 3
    assert model.columnCount() == 3  # Feature + s1 + s2
    assert model.samples == ["s1", "s2"]
    assert model.headerData(0, Qt.Horizontal) == "Feature"
    assert model.headerData(1, Qt.Horizontal) == "s1"
    assert model.headerData(2, Qt.Horizontal) == "s2"


def test_visible_rows_default_sort_by_mz_ascending():
    model = SpatialCorrelationModel()
    model.setRows(_rows(), ["s1", "s2"])

    assert _labels(model) == [
        "100.0000: Caffeine",
        "m/z 200.0000",
        "300.0000: Glucose",
    ]


def test_missing_value_renders_as_dash():
    model = SpatialCorrelationModel()
    model.setRows(_rows(), ["s1", "s2"])

    row = next(i for i in range(3) if model.data(_index(model, i, 0)).startswith("300"))
    assert model.data(_index(model, row, 2)) == "–"


def test_sort_by_sample_column_missing_values_sort_last_both_directions():
    model = SpatialCorrelationModel()
    model.setRows(_rows(), ["s1", "s2"])

    model.sortBy("s2")
    assert model.sortColumn == "s2"
    assert model.sortAscending is True
    assert _labels(model)[-1] == "300.0000: Glucose"  # missing s2, sorts last ascending

    model.sortBy("s2")  # same column again -> flips direction
    assert model.sortAscending is False
    assert _labels(model)[-1] == "300.0000: Glucose"  # still last, not first, descending


def test_sort_state_persists_across_setrows():
    # Simulates a fresh fetch overwriting rows (e.g. a re-run) — the
    # user's chosen sort must survive, not silently reset to the default.
    model = SpatialCorrelationModel()
    model.setRows(_rows(), ["s1", "s2"])
    model.sortBy("s1")
    model.sortBy("s1")  # descending

    assert model.sortColumn == "s1"
    assert model.sortAscending is False

    model.setRows(_rows(), ["s1", "s2"])  # re-fetch, same shape
    assert model.sortColumn == "s1"
    assert model.sortAscending is False
    # s1 descending: 0.9 (mz 100), 0.5 (mz 300), 0.1 (mz 200)
    assert _labels(model) == [
        "100.0000: Caffeine",
        "300.0000: Glucose",
        "m/z 200.0000",
    ]


def test_name_query_filters_and_persists():
    model = SpatialCorrelationModel()
    model.setRows(_rows(), ["s1", "s2"])

    model.nameQuery = "gluc"
    assert _labels(model) == ["300.0000: Glucose"]

    # Persistence check: re-fetching rows must not clear the query.
    model.setRows(_rows(), ["s1", "s2"])
    assert model.nameQuery == "gluc"
    assert _labels(model) == ["300.0000: Glucose"]


def test_mz_query_single_value_is_prefix_match():
    model = SpatialCorrelationModel()
    model.setRows(
        [{"mz": m, "compound_name": None, "s1": 0.5} for m in (178.99, 179.0561, 179.9, 1179.1)],
        ["s1"],
    )

    model.mzQuery = "179"
    assert _labels(model) == ["m/z 179.0561", "m/z 179.9000"]

    model.mzQuery = "179.05"
    assert _labels(model) == ["m/z 179.0561"]


def test_mz_query_range_filters():
    model = SpatialCorrelationModel()
    model.setRows(_rows(), ["s1", "s2"])

    model.mzQuery = "150-250"
    assert _labels(model) == ["m/z 200.0000"]


def test_mz_for_row_uses_visible_not_all_rows():
    model = SpatialCorrelationModel()
    model.setRows(_rows(), ["s1", "s2"])
    model.nameQuery = "gluc"

    assert model.mzForRow(0) == 300.0
    assert math.isnan(model.mzForRow(1))  # out of range once filtered to 1 row


def test_rows_loading_property_and_signal():
    model = SpatialCorrelationModel()
    received = []
    model.rowsLoadingChanged.connect(lambda: received.append(model.rowsLoading))

    model.setRowsLoading(True)
    assert model.rowsLoading is True
    assert received == [True]

    model.setRowsLoading(True)  # no-op, no duplicate signal
    assert received == [True]


def test_moran_i_for_mz_exact_match():
    model = SpatialCorrelationModel()
    model.setRows(_rows(), ["s1", "s2"])

    assert model.moranIForMz(100.0) == {"s1": 0.9, "s2": 0.8}


def test_moran_i_for_mz_nearest_within_tolerance():
    model = SpatialCorrelationModel()
    model.setRows(_rows(), ["s1", "s2"])

    assert model.moranIForMz(100.005) == {"s1": 0.9, "s2": 0.8}


def test_moran_i_for_mz_no_match_returns_empty():
    model = SpatialCorrelationModel()
    model.setRows(_rows(), ["s1", "s2"])

    assert model.moranIForMz(150.0) == {}


def test_moran_i_for_mz_empty_model_returns_empty():
    model = SpatialCorrelationModel()

    assert model.moranIForMz(100.0) == {}


def test_moran_i_for_mz_omits_unscored_samples():
    model = SpatialCorrelationModel()
    model.setRows(_rows(), ["s1", "s2"])

    # mz 300 (Glucose) has no s2 score at all.
    assert model.moranIForMz(300.0) == {"s1": 0.5}


def test_model_reset_emitted_on_setrows_and_filter():
    model = SpatialCorrelationModel()
    received = []
    model.modelReset.connect(lambda: received.append(None))

    model.setRows(_rows(), ["s1", "s2"])
    assert len(received) == 1

    model.nameQuery = "caff"
    assert len(received) == 2
