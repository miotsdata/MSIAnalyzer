"""Visual Inspection's "Get statistics" window (ADR 68)."""

from pathlib import Path

import pytest
from PySide6.QtCore import Q_ARG, QMetaObject, QObject, Qt
from PySide6.QtQuick import QQuickItem

from msianalyzer.core.plotting.roi import save_roi_to_sample
from tests.gui.views.test_visual_inspection_section import (
    _as_list,
    _open_visual_tab,
    _write_sample_h5ad,
)

_open_windows = []


@pytest.fixture(autouse=True)
def _close_windows_after_test():
    yield
    for window in _open_windows:
        window.close()
    _open_windows.clear()


def _open_statistics(analysis_view, model, find_visual_child, qtbot):
    out_dir = Path(model.outDir)
    for name in ("s1", "s2"):
        path = out_dir / f"{name}.h5ad"
        _write_sample_h5ad(path, obs_columns={"tic": [1.0, 2.0, 3.0, 4.0]})
        save_roi_to_sample(path, "Top", "#ff0000", [[0, 0], [2, 0], [2, 1], [0, 1]])
        save_roi_to_sample(path, "Left", "#00ff00", [[0, 0], [1, 0], [1, 2], [0, 2]])
    view = analysis_view(model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)
    button = find_visual_child(root, "openStatisticsButton")
    qtbot.mouseClick(view, Qt.LeftButton, pos=button.mapToScene(
        button.boundingRect().center()).toPoint())
    window = root.findChild(QObject, "statisticsWindow")
    assert window is not None
    _open_windows.append(window)
    qtbot.waitUntil(lambda: window.property("visible") is True, timeout=2000)
    return root, window


def _result(window):
    return _as_list(window.property("result")) or {}


def _wait_plot(window, qtbot, check=lambda r: True):
    qtbot.waitUntil(
        lambda: not window.property("loading") and "image" in _result(window)
        and check(_result(window)),
        timeout=8000,
    )
    return _result(window)


def test_get_statistics_draws_the_selected_feature_per_sample(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    _root, window = _open_statistics(analysis_view, visual_analysis_model, find_visual_child, qtbot)
    result = _wait_plot(window, qtbot)
    assert result["kind"] == "violin"
    assert result["labels"] == ["s1", "s2"]
    image = window.findChild(QQuickItem, "statsPlotImage")
    qtbot.waitUntil(lambda: image.property("sourceSize").width() > 0, timeout=3000)
    # A feature starts with "Detected pixels only" on.
    assert window.property("detectedOnly") is True


def test_use_roi_and_grouped_by_roi(analysis_view, visual_analysis_model, find_visual_child, qtbot):
    _root, window = _open_statistics(analysis_view, visual_analysis_model, find_visual_child, qtbot)
    _wait_plot(window, qtbot)
    window.setProperty("useRoi", True)
    result = _wait_plot(window, qtbot, lambda r: len(r["labels"]) == 4)
    assert set(result["labels"]) == {"s1 · Top", "s1 · Left", "s2 · Top", "s2 · Left"}
    window.setProperty("outsideRoi", True)
    _wait_plot(window, qtbot, lambda r: "s1 · (no ROI)" in r["labels"])
    window.setProperty("groupByRoi", True)
    assert window.property("cols") == 1  # ROI panels stack one below the other
    result = _wait_plot(window, qtbot, lambda r: len(r["rois"]) == 3)
    assert result["samples"] == ["s1", "s2"]


def test_reorder_dialog_moves_change_the_plot_order(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    _root, window = _open_statistics(analysis_view, visual_analysis_model, find_visual_child, qtbot)
    _wait_plot(window, qtbot)
    dialog = window.findChild(QObject, "statsReorderDialog")
    QMetaObject.invokeMethod(dialog, "openFor", Q_ARG("QVariant", _result(window)))
    model = dialog.findChild(QObject, "statsReorderFirstModel")
    QMetaObject.invokeMethod(model, "move", Q_ARG(int, 1), Q_ARG(int, 0), Q_ARG(int, 1))
    QMetaObject.invokeMethod(dialog, "accept")
    _wait_plot(window, qtbot, lambda r: r["labels"] == ["s2", "s1"])
    assert _as_list(window.property("order")) == ["s2", "s1"]


def test_get_statistics_disabled_on_local_moran_layer(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    out_dir = Path(visual_analysis_model.outDir)
    for name in ("s1", "s2"):
        _write_sample_h5ad(out_dir / f"{name}.h5ad")
    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)
    controls = find_visual_child(root, "controlsFlickable")
    button = find_visual_child(root, "openStatisticsButton")
    assert button.property("enabled") is True
    controls.setProperty("dataLayer", "localMoran")
    assert button.property("enabled") is False


def test_figure_always_fits_the_panel_and_only_zoom_overflows(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    _root, window = _open_statistics(analysis_view, visual_analysis_model, find_visual_child, qtbot)
    flick = window.findChild(QQuickItem, "statsPlotFlickable")

    def fits(r):
        return r["width"] <= flick.width() and r["height"] <= flick.height()

    _wait_plot(window, qtbot, fits)
    window.setProperty("widthIn", 40)  # a huge figsize still fits
    result = _wait_plot(window, qtbot, lambda r: fits(r) and r["width"] > r["height"] * 3)
    window.setProperty("zoom", 200)
    zoomed = _wait_plot(window, qtbot, lambda r: r["width"] > result["width"] * 1.9)
    assert zoomed["width"] > flick.width()  # only zoom scrolls
