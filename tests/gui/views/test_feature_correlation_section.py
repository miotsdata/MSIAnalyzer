"""Feature Correlation tab (ADR 66): empty state, picking a feature,
top/bottom N, search, the handoff to Visual Inspection, state across
tab switches, and the post-run compute bar."""

import sqlite3
from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
from PySide6.QtCore import QMetaObject, QObject, Qt, Q_ARG
from scipy.sparse import csr_matrix

MZS = (123.4567, 200.0, 300.0)
# 123.4567 (Caffeine) vs 200: -0.5, vs 300: 0.9
MATRIX = [[1.0, -0.5, 0.9], [-0.5, 1.0, -0.1], [0.9, -0.1, 1.0]]


def _add_features(analysis_model):
    with sqlite3.connect(analysis_model.analysisDbPath) as con:
        con.executemany(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (?, ?, '{}')",
            [(8, 200.0), (9, 300.0)],
        )
        con.commit()


def _seed_h5ad(analysis_model, *, with_correlation=True):
    """s1's `.h5ad`: the three features, a TIC layer, and (optionally) a
    stored correlation matrix shaped as the pipeline stage writes it."""
    _add_features(analysis_model)
    out_dir = Path(analysis_model.analysisDbPath).parent
    var = pd.DataFrame({"mz": list(MZS)}, index=[f"mz_{m:.4f}" for m in MZS])
    rng = np.random.default_rng(0)
    X = rng.random((30, 3)).astype(np.float32)
    adata = ad.AnnData(X=csr_matrix(X), var=var)
    adata.layers["TIC"] = csr_matrix(X)
    adata.obsm["spatial"] = np.column_stack([np.arange(30) % 6, np.arange(30) // 6]).astype(float)
    if with_correlation:
        adata.varp["featureCorrelation"] = np.array(MATRIX, dtype=np.float32)
        adata.uns["featureCorrelation"] = {"method": "pearson", "layer": "TIC"}
    adata.write_h5ad(out_dir / "s1.h5ad")


def _open_tab(view, root, find_visual_child, qtbot, index=5):
    nav_button = find_visual_child(root, f"navButton_{index}")
    center = nav_button.mapToScene(nav_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)
    if index != 5:
        return None
    section = find_visual_child(root, "featureCorrelationSection")
    qtbot.waitUntil(lambda: not section.property("rowsLoading"), timeout=3000)
    return section


def _prop(obj, name):
    """A QML `property var` as plain Python (JS-computed ones arrive as QJSValue)."""
    value = obj.property(name)
    return value.toVariant() if hasattr(value, "toVariant") else value


def _select(section, qtbot, mz):
    QMetaObject.invokeMethod(section, "selectFeature", Q_ARG("QVariant", mz))
    qtbot.waitUntil(lambda: not section.property("resultLoading"), timeout=3000)


def test_empty_state_without_any_correlation(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    _open_tab(view, root, find_visual_child, qtbot)

    assert find_visual_child(root, "featureCorrelationEmptyStateLabel").property("visible") is True
    assert find_visual_child(root, "featureCorrelationSplit").property("visible") is False


def test_picking_a_feature_fills_both_tables(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    _seed_h5ad(annotated_analysis_model)
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    section = _open_tab(view, root, find_visual_child, qtbot)

    assert _prop(section, "sampleNames") == ["s1"]
    assert len(_prop(section, "features")) == 3
    _select(section, qtbot, 123.4567)

    top = _prop(section, "topRows")
    bottom = _prop(section, "bottomRows")
    assert [r["mz"] for r in top] == [300.0, 200.0]
    assert [r["mz"] for r in bottom] == [200.0, 300.0]
    assert top[0]["mean_r"] == np.float32(0.9)
    assert find_visual_child(root, "featureCorrelationSelectedLabel").property("text") == "123.4567: Caffeine"

    table = find_visual_child(root, "mostCorrelatedTable")
    qtbot.waitUntil(lambda: find_visual_child(table, "mostCorrelatedMeanR_0") is not None, timeout=2000)
    assert find_visual_child(table, "mostCorrelatedMeanR_0").property("text") == "0.900"


def test_top_n_limits_each_table(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    _seed_h5ad(annotated_analysis_model)
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    section = _open_tab(view, root, find_visual_child, qtbot)
    _select(section, qtbot, 123.4567)

    section.setProperty("topN", 1)
    assert [r["mz"] for r in _prop(section, "topRows")] == [300.0]
    assert [r["mz"] for r in _prop(section, "bottomRows")] == [200.0]


def test_name_search_filters_the_feature_list(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    _seed_h5ad(annotated_analysis_model)
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    section = _open_tab(view, root, find_visual_child, qtbot)

    find_visual_child(root, "featureCorrelationNameSearchField").setProperty("text", "caff")
    assert [f["mz"] for f in _prop(section, "filteredFeatures")] == [123.4567]
    find_visual_child(root, "featureCorrelationNameSearchField").setProperty("text", "zzz")
    assert find_visual_child(root, "featureCorrelationSearchEmptyLabel").property("visible") is True


def test_show_spatial_distribution_jumps_to_visual_inspection(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    _seed_h5ad(annotated_analysis_model)
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    section = _open_tab(view, root, find_visual_child, qtbot)
    _select(section, qtbot, 123.4567)

    table = find_visual_child(root, "mostCorrelatedTable")
    table.showSpatialDistributionRequested.emit(300.0)

    stack = root.findChild(object, "sectionStack")
    qtbot.waitUntil(lambda: stack.property("currentIndex") == 3, timeout=3000)
    controls = find_visual_child(root, "controlsFlickable")
    qtbot.waitUntil(lambda: not controls.property("featuresLoading"), timeout=2000)
    assert controls.property("selectedFeature")["mz"] == 300.0


def test_selection_survives_switching_tabs(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    _seed_h5ad(annotated_analysis_model)
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    section = _open_tab(view, root, find_visual_child, qtbot)
    _select(section, qtbot, 123.4567)
    section.setProperty("topN", 7)

    _open_tab(view, root, find_visual_child, qtbot, index=0)
    section_2 = _open_tab(view, root, find_visual_child, qtbot)
    assert section_2 is not section
    qtbot.waitUntil(lambda: not section_2.property("resultLoading"), timeout=3000)
    assert section_2.property("selectedMz") == 123.4567
    assert section_2.property("topN") == 7
    assert [r["mz"] for r in _prop(section_2, "topRows")] == [300.0, 200.0]


def test_compute_asks_before_overwriting(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    _seed_h5ad(annotated_analysis_model)
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    section = _open_tab(view, root, find_visual_child, qtbot)

    find_visual_child(root, "featureCorrelationComputeButton").clicked.emit()
    qtbot.wait(50)
    assert section.findChild(QObject, "overwriteFeatureCorrelationDialog").property("visible") is True
    assert section.property("computing") is False


def test_compute_fills_an_empty_analysis(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    _seed_h5ad(annotated_analysis_model, with_correlation=False)
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    section = _open_tab(view, root, find_visual_child, qtbot)
    assert _prop(section, "sampleNames") == []

    find_visual_child(root, "featureCorrelationComputeButton").clicked.emit()
    qtbot.waitUntil(lambda: not section.property("computing"), timeout=30000)
    assert "1 sample" in section.property("computeMessage")
    qtbot.waitUntil(lambda: _prop(section, "sampleNames") == ["s1"], timeout=3000)
    assert find_visual_child(root, "featureCorrelationSplit").property("visible") is True
