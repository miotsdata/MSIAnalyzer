from msianalyzer.gui.utils.spatial_correlation_model import SpatialCorrelationModel


def _rows():
    return [
        {"mz": 100.0, "compound_name": "Caffeine", "s1": 0.9, "s2": 0.8},
        {"mz": 200.0, "compound_name": None, "s1": 0.1, "s2": 0.5},
        {"mz": 300.0, "compound_name": "Glucose", "s1": 0.5},  # missing s2
    ]


def _labels(model):
    return [r["label"] for r in model.visibleRows]


def test_visible_rows_default_sort_by_mz_ascending():
    model = SpatialCorrelationModel()
    model.setRows(_rows(), ["s1", "s2"])

    assert model.samples == ["s1", "s2"]
    assert _labels(model) == [
        "100.0000: Caffeine",
        "m/z 200.0000",
        "300.0000: Glucose",
    ]


def test_missing_value_present_in_row_dict_as_none():
    model = SpatialCorrelationModel()
    model.setRows(_rows(), ["s1", "s2"])

    glucose_row = next(r for r in model.visibleRows if r["mz"] == 300.0)
    assert glucose_row.get("s2") is None


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


def test_mz_query_range_filters():
    model = SpatialCorrelationModel()
    model.setRows(_rows(), ["s1", "s2"])

    model.mzQuery = "150-250"
    assert _labels(model) == ["m/z 200.0000"]


def test_rows_loading_property_and_signal():
    model = SpatialCorrelationModel()
    received = []
    model.rowsLoadingChanged.connect(lambda: received.append(model.rowsLoading))

    model.setRowsLoading(True)
    assert model.rowsLoading is True
    assert received == [True]

    model.setRowsLoading(True)  # no-op, no duplicate signal
    assert received == [True]


def test_visible_rows_changed_emitted_on_setrows_and_filter():
    model = SpatialCorrelationModel()
    received = []
    model.visibleRowsChanged.connect(lambda: received.append(None))

    model.setRows(_rows(), ["s1", "s2"])
    assert len(received) == 1

    model.nameQuery = "caff"
    assert len(received) == 2
