"""Overlay tab (ADR 67): adding/removing channels, the tile id the heatmap
provider renders, contrast fields, blend mode, state across tab
switches, and "Add to overlay" from Feature Correlation."""

import sqlite3
from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
from PySide6.QtCore import QMetaObject, Qt, Q_ARG, Q_RETURN_ARG
from PySide6.QtGui import QImage
from scipy.sparse import csr_matrix

OVERLAY_TAB = 6


def _seed(analysis_model):
    """s1's `.h5ad` (3x2 pixels) with Caffeine (123.4567) at pixel 0 and
    m/z 200 at pixel 5, plus a feature correlation for the handoff test."""
    with sqlite3.connect(analysis_model.analysisDbPath) as con:
        con.execute("INSERT INTO features (feature_id, mz, members_json) VALUES (8, 200.0, '{}')")
        con.commit()
    out_dir = Path(analysis_model.analysisDbPath).parent
    X = np.zeros((6, 2), dtype=np.float32)
    X[0, 0] = 10.0
    X[5, 1] = 10.0
    var = pd.DataFrame({"mz": [123.4567, 200.0]}, index=["mz_123.4567", "mz_200.0000"])
    adata = ad.AnnData(X=csr_matrix(X), var=var)
    adata.layers["raw"] = csr_matrix(X)
    adata.layers["TIC"] = csr_matrix(X)
    xs, ys = np.meshgrid(np.arange(3), np.arange(2))
    adata.obsm["spatial"] = np.column_stack([xs.ravel(), ys.ravel()]).astype(float)
    adata.varp["featureCorrelation"] = np.array([[1, -0.3], [-0.3, 1]], dtype=np.float32)
    adata.uns["featureCorrelation"] = {"method": "pearson", "layer": "TIC"}
    adata.write_h5ad(out_dir / "s1.h5ad")


def _prop(obj, name):
    value = obj.property(name)
    return value.toVariant() if hasattr(value, "toVariant") else value


def _open_tab(view, root, find_visual_child, qtbot, index=OVERLAY_TAB, section_name="overlaySection"):
    nav_button = find_visual_child(root, f"navButton_{index}")
    center = nav_button.mapToScene(nav_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)
    if not section_name:
        return None
    section = find_visual_child(root, section_name)
    qtbot.waitUntil(lambda: not section.property("rowsLoading"), timeout=3000)
    return section


def _tile_source(section, sample):
    return QMetaObject.invokeMethod(section, "tileSource", Q_RETURN_ARG("QVariant"), Q_ARG("QVariant", sample))


def _add(section, mz):
    QMetaObject.invokeMethod(section, "addChannel", Q_ARG("QVariant", mz))


def test_empty_until_a_channel_is_added(analysis_view, annotated_analysis_model, find_visual_child, qtbot):
    _seed(annotated_analysis_model)
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    section = _open_tab(view, root, find_visual_child, qtbot)

    assert find_visual_child(root, "overlayEmptyLabel").property("visible") is True
    assert section.property("channelsToken") == ""
    assert len(_prop(section, "features")) == 2


def test_adding_channels_builds_the_tile_id(analysis_view, annotated_analysis_model, find_visual_child, qtbot, application):
    _seed(annotated_analysis_model)
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    section = _open_tab(view, root, find_visual_child, qtbot)

    _add(section, 123.4567)
    _add(section, 200.0)
    _add(section, 200.0)  # already there: ignored
    channels = _prop(section, "channels")
    assert [c["mz"] for c in channels] == [123.4567, 200.0]
    assert [c["color"] for c in channels] == ["#ff00ff", "#00ff00"]
    assert channels[0]["label"] == "123.4567: Caffeine"

    token = section.property("channelsToken")
    assert token == "123.4567:ff00ff:auto:auto:1,200:00ff00:auto:auto:1"
    source = _tile_source(section, "s1")
    assert source == "image://heatmap/s1|overlay|TIC|add|" + token + "|ds-1"

    # The provider renders that id: magenta where Caffeine is, green at 200.
    image = application.analysis_bridge.heatmap_provider.requestImage(
        source[len("image://heatmap/"):], None, None
    ).convertToFormat(QImage.Format.Format_RGBA8888)
    assert image.width() == 3 and image.height() == 2
    assert image.pixelColor(0, 0).name() == "#ff00ff"
    assert image.pixelColor(2, 1).name() == "#00ff00"
    assert image.pixelColor(1, 0).name() == "#000000"


def test_channel_controls_update_the_token(analysis_view, annotated_analysis_model, find_visual_child, qtbot):
    _seed(annotated_analysis_model)
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    section = _open_tab(view, root, find_visual_child, qtbot)
    _add(section, 123.4567)
    _add(section, 200.0)

    min_field = find_visual_child(root, "overlayChannelMin_0")
    min_field.setProperty("text", "2.5")
    min_field.editingFinished.emit()
    gamma = find_visual_child(root, "overlayChannelGamma_0")
    gamma.setProperty("text", "0.5")
    gamma.editingFinished.emit()
    assert section.property("channelsToken").split(",")[0] == "123.4567:ff00ff:2.5:auto:0.5"

    visible = find_visual_child(root, "overlayChannelVisible_1")
    visible.setProperty("checked", False)  # what a real click does first
    visible.clicked.emit()
    assert section.property("channelsToken") == "123.4567:ff00ff:2.5:auto:0.5"

    section.setProperty("mode", "max")
    source = _tile_source(section, "s1")
    assert "|overlay|TIC|max|" in source

    find_visual_child(root, "overlayChannelRemove_0").clicked.emit()
    assert [c["mz"] for c in _prop(section, "channels")] == [200.0]
    assert section.property("channelsToken") == ""  # the one left is hidden


def test_at_most_six_channels(analysis_view, annotated_analysis_model, find_visual_child, qtbot):
    _seed(annotated_analysis_model)
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    section = _open_tab(view, root, find_visual_child, qtbot)
    for mz in (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0):
        _add(section, mz)
    channels = _prop(section, "channels")
    assert len(channels) == 6
    assert len({c["color"] for c in channels}) == 6


def test_channels_survive_switching_tabs(analysis_view, annotated_analysis_model, find_visual_child, qtbot):
    _seed(annotated_analysis_model)
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    section = _open_tab(view, root, find_visual_child, qtbot)
    _add(section, 200.0)
    section.setProperty("mode", "max")

    _open_tab(view, root, find_visual_child, qtbot, index=0, section_name=None)
    section_2 = _open_tab(view, root, find_visual_child, qtbot)
    assert section_2 is not section
    assert [c["mz"] for c in _prop(section_2, "channels")] == [200.0]
    assert section_2.property("mode") == "max"


def test_add_to_overlay_from_feature_correlation(analysis_view, annotated_analysis_model, find_visual_child, qtbot):
    _seed(annotated_analysis_model)
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    corr = _open_tab(view, root, find_visual_child, qtbot, index=5, section_name="featureCorrelationSection")

    QMetaObject.invokeMethod(corr, "selectFeature", Q_ARG("QVariant", 123.4567))
    qtbot.waitUntil(lambda: not corr.property("resultLoading"), timeout=3000)
    table = find_visual_child(root, "mostCorrelatedTable")
    table.addToOverlayRequested.emit(200.0)
    stack = root.findChild(object, "sectionStack")
    qtbot.waitUntil(lambda: stack.property("currentIndex") == OVERLAY_TAB, timeout=3000)

    section = find_visual_child(root, "overlaySection")
    qtbot.waitUntil(lambda: len(_prop(section, "channels")) == 2, timeout=3000)
    assert [c["mz"] for c in _prop(section, "channels")] == [123.4567, 200.0]
