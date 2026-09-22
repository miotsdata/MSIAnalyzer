from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
from PySide6.QtCore import Qt


def _open_spatial_correlation_tab(view, root, find_visual_child, qtbot):
    nav_button = find_visual_child(root, "navButton_4")
    center = nav_button.mapToScene(nav_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)
    section = find_visual_child(root, "spatialCorrelationSection")
    qtbot.waitUntil(lambda: not section.property("rowsLoading"), timeout=2000)
    return section


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


def test_spatial_correlation_empty_state_without_h5ad(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    section = _open_spatial_correlation_tab(view, root, find_visual_child, qtbot)

    assert section.property("rows") == []
    empty_label = find_visual_child(root, "spatialCorrelationEmptyStateLabel")
    assert empty_label.property("visible") is True


def test_spatial_correlation_table_shows_moran_scores_with_compound_name(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    # annotated_analysis_model already has feature_id=7, mz=123.4567
    # annotated as "Caffeine" — confirms the name join (matching Visual
    # Inspection's own feature-picker convention) actually resolves.
    _seed_moran_h5ad(annotated_analysis_model)

    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    section = _open_spatial_correlation_tab(view, root, find_visual_child, qtbot)

    rows = section.property("rows")
    assert len(rows) == 1
    assert rows[0]["sample"] == "s1"
    assert rows[0]["mz"] == 123.4567
    assert rows[0]["moran_i"] == 0.75
    assert rows[0]["compound_name"] == "Caffeine"

    feature_rows = section.property("featureRows").toVariant()
    assert feature_rows[0]["s1"] == 0.75
    assert feature_rows[0]["compound_name"] == "Caffeine"

    assert section.featureLabel(feature_rows[0]) == "123.4567: Caffeine"


def test_spatial_correlation_name_search_filters_table(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    _seed_moran_h5ad(annotated_analysis_model, features=((123.4567, 0.75), (999.0, 0.2)))

    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    section = _open_spatial_correlation_tab(view, root, find_visual_child, qtbot)

    assert len(section.property("sortedFeatureRows").toVariant()) == 2

    search_field = find_visual_child(root, "spatialCorrelationNameSearchField")
    search_field.setProperty("text", "caff")
    qtbot.wait(50)

    filtered = section.property("sortedFeatureRows").toVariant()
    assert len(filtered) == 1
    assert filtered[0]["mz"] == 123.4567


def test_show_spatial_distribution_jumps_to_visual_inspection(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    # End-to-end, same shape as
    # test_inspect_visually_switches_to_visual_tab_with_feature_selected
    # (test_analysis_page.py) for Annotations' identical handoff (ADR 32).
    _seed_moran_h5ad(annotated_analysis_model)

    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    _open_spatial_correlation_tab(view, root, find_visual_child, qtbot)

    row = find_visual_child(root, "spatialCorrelationRow_123.4567")
    assert row is not None
    menu_item = row.findChild(object, "spatialCorrelationShowDistribution_123.4567")
    assert menu_item is not None
    menu_item.click()
    qtbot.wait(50)

    stack = root.findChild(object, "sectionStack")
    assert stack.property("currentIndex") == 3

    controls = find_visual_child(root, "controlsFlickable")
    qtbot.waitUntil(lambda: not controls.property("featuresLoading"), timeout=2000)
    assert controls.property("inspectionMode") == "feature"
    assert controls.property("selectedFeature")["mz"] == 123.4567
