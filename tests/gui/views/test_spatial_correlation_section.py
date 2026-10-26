from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
from PySide6.QtCore import QObject, Qt

# NOTE on coverage in this file: SpatialCorrelationSection.qml renders its
# table via TableView/HorizontalHeaderView (not the Repeater-in-Flickable
# pattern most of this codebase's other tables use) — chosen for real
# rendering performance ("ordering the table is still super slow" with the
# Repeater version), at a real, verified cost: TableView/HorizontalHeaderView
# never instantiate any delegate items at all under this project's offscreen
# GUI test harness (contentWidth/rows/columns compute correctly, but zero
# delegate QQuickItems are ever created, no warnings — see
# SpatialCorrelationModel's own docstring). That means click-to-sort,
# right-click-a-row, and anything else requiring an actual rendered cell
# CANNOT be exercised here. What *can* still be tested, and is below:
# - the model itself is fetched/bound correctly (AnalysisBridge.getSpatialCorrelationModel)
# - the model's sort/filter/row logic (fully covered separately, no QML
#   involved at all, in tests/gui/utils/test_spatial_correlation_model.py)
# - the model's identity (and therefore its state) survives this section's
#   Loader being destroyed and rebuilt on a tab switch
# - the cross-tab handoff signal, invoked directly (standing in for an
#   actual right-click, which can't render) — this real. app confirms with
#   the user tests before we trust the rendering side.


def _open_spatial_correlation_tab(view, root, find_visual_child, qtbot):
    nav_button = find_visual_child(root, "navButton_4")
    center = nav_button.mapToScene(nav_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)
    section = find_visual_child(root, "spatialCorrelationSection")
    qtbot.waitUntil(lambda: not section.property("rowsLoading"), timeout=2000)
    return section


def _open_summary_tab(view, root, find_visual_child, qtbot):
    nav_button = find_visual_child(root, "navButton_0")
    center = nav_button.mapToScene(nav_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)


def _seed_moran_h5ad(analysis_model, sample_name="s1", features=((123.4567, 0.75),)):
    """A minimal `.h5ad` with one or more features' Moran's I already
    computed — same shape `core.spatial.moran.compute_spatial_autocorr`
    writes (`uns["moranI"]`, indexed by `var_names`, an `I` column)."""
    out_dir = Path(analysis_model.analysisDbPath).parent
    var_names = [f"mz_test_{i}" for i in range(len(features))]
    mzs = [f[0] for f in features]
    moran_is = [f[1] for f in features]
    var = pd.DataFrame({"mz": mzs}, index=var_names)
    adata = ad.AnnData(X=np.zeros((1, len(features))), var=var)
    adata.uns["moranI"] = pd.DataFrame({"I": moran_is}, index=var_names)
    adata.write_h5ad(out_dir / f"{sample_name}.h5ad")


#: Pixel count for the synthetic localMoranI fixture below — divisible
#: enough (200) that every percentage these tests use (42.5, 5.0) is
#: realizable as an exact whole number of pixels.
_LOCAL_MORAN_FIXTURE_N_PIXELS = 200


def _seed_local_moran_h5ad(analysis_model, sample_name="s1", features=((123.4567, 42.5),)):
    """Same shape as `_seed_moran_h5ad`, for the local summary table —
    `features`: `[(mz, pct_above_threshold), ...]`, realized as a
    synthetic `adata.layers["localMoranI"]` column per feature (some
    pixels at `2.0`, above the model's default `1.0` threshold, the rest
    at `0.0`), as written by
    `core.spatial.local_moran.compute_local_moran_layer` — not a stored
    scalar summary; see `LocalMoranSummaryModel`'s own docstring for why.
    `pct_above_threshold` is realized exactly, assuming the model reading
    it back uses the default `1.0` threshold (true here — none of these
    tests seed a `commands` row overriding it)."""
    n = _LOCAL_MORAN_FIXTURE_N_PIXELS
    out_dir = Path(analysis_model.analysisDbPath).parent
    var_names = [f"mz_test_{i}" for i in range(len(features))]
    mzs = [f[0] for f in features]
    var = pd.DataFrame({"mz": mzs}, index=var_names)
    adata = ad.AnnData(X=np.zeros((n, len(features))), var=var)
    layer = np.zeros((n, len(features)))
    for col, (_, pct) in enumerate(features):
        n_above = round(pct / 100.0 * n)
        layer[:n_above, col] = 2.0
    adata.layers["localMoranI"] = layer
    adata.write_h5ad(out_dir / f"{sample_name}.h5ad")


def test_spatial_correlation_empty_state_without_h5ad(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    section = _open_spatial_correlation_tab(view, root, find_visual_child, qtbot)

    assert section.property("rowCountVisible") == 0
    empty_label = find_visual_child(root, "spatialCorrelationEmptyStateLabel")
    assert empty_label.property("visible") is True

    local_empty_label = find_visual_child(root, "localMoranSummaryEmptyStateLabel")
    assert local_empty_label.property("visible") is True


def test_spatial_correlation_model_bound_with_moran_scores_and_compound_name(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    # annotated_analysis_model already has feature_id=7, mz=123.4567
    # annotated as "Caffeine" — confirms the name join (matching Visual
    # Inspection's own feature-picker convention) actually resolves, and
    # that AnalysisBridge.getSpatialCorrelationModel's fetch reaches the
    # QML-bound model correctly.
    _seed_moran_h5ad(annotated_analysis_model)

    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    section = _open_spatial_correlation_tab(view, root, find_visual_child, qtbot)

    model = section.property("model")
    assert model.rowCount() == 1
    assert model.data(model.index(0, 0)) == "123.4567: Caffeine"
    assert model.data(model.index(0, 1)) == "0.750"


def test_spatial_correlation_name_search_filters_table(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    _seed_moran_h5ad(annotated_analysis_model, features=((123.4567, 0.75), (999.0, 0.2)))

    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    section = _open_spatial_correlation_tab(view, root, find_visual_child, qtbot)

    assert section.property("model").rowCount() == 2

    search_field = find_visual_child(root, "spatialCorrelationNameSearchField")
    search_field.setProperty("text", "caff")
    qtbot.wait(50)

    model = section.property("model")
    assert model.rowCount() == 1
    assert model.mzForRow(0) == 123.4567


def test_sort_and_search_state_survive_switching_tabs_away_and_back(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    # The actual bug report this whole redesign is for: "if I go back I
    # don't have the same table as before, I might have sorted for a
    # column" — sort column/direction AND search text must both still be
    # there after leaving this tab (which destroys the section's QML
    # item, since it's a lazy Loader) and coming back. Sorting is
    # triggered by calling the model directly (sortBy), standing in for
    # a header click, since no header delegate renders under this
    # harness — see this file's top-of-module note.
    _seed_moran_h5ad(annotated_analysis_model, features=((123.4567, 0.75), (50.0, 0.2)))

    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    section = _open_spatial_correlation_tab(view, root, find_visual_child, qtbot)
    model = section.property("model")

    # Default sort is by mz ascending: 50.0 before 123.4567.
    assert model.mzForRow(0) == 50.0
    assert model.mzForRow(1) == 123.4567

    model.sortBy("mz")  # already sorted by mz -> flips to descending
    assert section.property("sortColumn") == "mz"
    assert section.property("sortAscending") is False

    search_field = find_visual_child(root, "spatialCorrelationNameSearchField")
    search_field.setProperty("text", "caff")
    qtbot.wait(50)

    # Leave the tab — the section's Loader deactivates, destroying this
    # exact QML item (verified: a fresh find_visual_child for the same
    # objectName below returns a genuinely new instance), while the
    # model itself (owned by AnalysisBridge, not this item) survives.
    _open_summary_tab(view, root, find_visual_child, qtbot)

    section_2 = _open_spatial_correlation_tab(view, root, find_visual_child, qtbot)
    assert section_2 is not section
    model_2 = section_2.property("model")
    assert model_2 is model

    assert section_2.property("sortColumn") == "mz"
    assert section_2.property("sortAscending") is False
    assert search_field is not find_visual_child(root, "spatialCorrelationNameSearchField")
    assert find_visual_child(root, "spatialCorrelationNameSearchField").property("text") == "caff"
    assert model_2.rowCount() == 1
    assert model_2.mzForRow(0) == 123.4567


def test_show_spatial_distribution_jumps_to_visual_inspection(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    # End-to-end, same shape as
    # test_inspect_visually_switches_to_visual_tab_with_feature_selected
    # (test_analysis_page.py) for Annotations' identical handoff (ADR 32)
    # — but the signal is emitted directly (standing in for a right-click
    # + "Show spatial distribution" click, which can't render under this
    # harness) rather than driven through the actual context menu.
    _seed_moran_h5ad(annotated_analysis_model)

    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    section = _open_spatial_correlation_tab(view, root, find_visual_child, qtbot)

    section.showSpatialDistributionRequested.emit(123.4567)

    stack = root.findChild(object, "sectionStack")
    qtbot.waitUntil(lambda: stack.property("currentIndex") == 3, timeout=2000)

    controls = find_visual_child(root, "controlsFlickable")
    qtbot.waitUntil(lambda: not controls.property("featuresLoading"), timeout=2000)
    assert controls.property("inspectionMode") == "feature"
    assert controls.property("selectedFeature")["mz"] == 123.4567


def test_local_moran_summary_model_bound_with_percentages_and_compound_name(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    _seed_local_moran_h5ad(annotated_analysis_model)

    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    section = _open_spatial_correlation_tab(view, root, find_visual_child, qtbot)

    model = section.property("localModel")
    assert model.rowCount() == 1
    assert model.data(model.index(0, 0)) == "123.4567: Caffeine"
    assert model.data(model.index(0, 1)) == "42.5%"


def test_local_moran_threshold_field_reapplies_threshold_live(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    # The actual point of storing the full per-pixel layer instead of a
    # pre-thresholded summary: the user can change the threshold in the
    # GUI and see the table update immediately, no re-run.
    _seed_local_moran_h5ad(annotated_analysis_model)

    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    section = _open_spatial_correlation_tab(view, root, find_visual_child, qtbot)

    model = section.property("localModel")
    assert model.data(model.index(0, 1)) == "42.5%"

    threshold_field = find_visual_child(root, "localMoranThresholdField")
    # No commands row seeded for this analysis — falls back to the
    # dataclass default (1.0), matching this fixture's own assumption.
    assert threshold_field.property("text") == "1.00"

    threshold_field.setProperty("text", "3.0")
    threshold_field.editingFinished.emit()

    assert model.threshold == 3.0
    # This fixture's "above-threshold" pixels are all exactly 2.0 — none
    # clear a threshold of 3.0 anymore, recomputed live from the same
    # already-loaded array, no new fetch.
    assert model.data(model.index(0, 1)) == "0.0%"


def test_local_moran_summary_name_search_filters_table(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    _seed_local_moran_h5ad(
        annotated_analysis_model, features=((123.4567, 42.5), (999.0, 5.0))
    )

    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    section = _open_spatial_correlation_tab(view, root, find_visual_child, qtbot)

    assert section.property("localModel").rowCount() == 2

    search_field = find_visual_child(root, "localMoranSummaryNameSearchField")
    search_field.setProperty("text", "caff")
    qtbot.wait(50)

    model = section.property("localModel")
    assert model.rowCount() == 1
    assert model.mzForRow(0) == 123.4567


def test_local_moran_summary_sort_and_search_state_survive_switching_tabs_away_and_back(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    # Same real bug this whole page's model-ownership design is for
    # (test_sort_and_search_state_survive_switching_tabs_away_and_back
    # above), now for the second table.
    _seed_local_moran_h5ad(
        annotated_analysis_model, features=((123.4567, 42.5), (50.0, 5.0))
    )

    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    section = _open_spatial_correlation_tab(view, root, find_visual_child, qtbot)
    model = section.property("localModel")

    assert model.mzForRow(0) == 50.0
    assert model.mzForRow(1) == 123.4567

    model.sortBy("mz")  # already sorted by mz -> flips to descending
    assert section.property("localSortColumn") == "mz"
    assert section.property("localSortAscending") is False

    search_field = find_visual_child(root, "localMoranSummaryNameSearchField")
    search_field.setProperty("text", "caff")
    qtbot.wait(50)

    _open_summary_tab(view, root, find_visual_child, qtbot)

    section_2 = _open_spatial_correlation_tab(view, root, find_visual_child, qtbot)
    assert section_2 is not section
    model_2 = section_2.property("localModel")
    assert model_2 is model

    assert section_2.property("localSortColumn") == "mz"
    assert section_2.property("localSortAscending") is False
    assert find_visual_child(root, "localMoranSummaryNameSearchField").property("text") == "caff"
    assert model_2.rowCount() == 1
    assert model_2.mzForRow(0) == 123.4567


def test_show_local_spatial_distribution_jumps_to_visual_inspection_with_local_layer(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    # Same handoff as test_show_spatial_distribution_jumps_to_visual_inspection
    # above, but from the local table's own signal — also switches
    # dataLayer to "localMoran", not just the selected feature.
    _seed_local_moran_h5ad(annotated_analysis_model)

    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    section = _open_spatial_correlation_tab(view, root, find_visual_child, qtbot)

    section.showLocalSpatialDistributionRequested.emit(123.4567)

    stack = root.findChild(object, "sectionStack")
    qtbot.waitUntil(lambda: stack.property("currentIndex") == 3, timeout=2000)

    controls = find_visual_child(root, "controlsFlickable")
    qtbot.waitUntil(lambda: not controls.property("featuresLoading"), timeout=2000)
    assert controls.property("inspectionMode") == "feature"
    assert controls.property("selectedFeature")["mz"] == 123.4567
    assert controls.property("dataLayer") == "localMoran"


def test_compute_moran_asks_before_overwriting_existing_scores(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    _seed_moran_h5ad(annotated_analysis_model)
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    section = _open_spatial_correlation_tab(view, root, find_visual_child, qtbot)
    qtbot.waitUntil(lambda: len(section.property("sampleNames")) > 0, timeout=3000)

    find_visual_child(root, "datasetComputeMoranButton").clicked.emit()
    qtbot.wait(50)

    dialog = section.findChild(QObject, "overwriteMoranDialog")
    assert dialog.property("visible") is True
    assert section.property("computing") is False  # nothing started yet
