import sqlite3

from PySide6.QtCore import Qt


def _open_visual_tab(view, root, find_visual_child, qtbot):
    nav_button = find_visual_child(root, "navButton_3")
    center = nav_button.mapToScene(nav_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)


def _scroll_controls_panel_to(root, find_visual_child, item):
    """Scrolls `controlsFlickable` so `item` (a descendant of
    `controlsPanel`) is on-screen — the panel can hold more controls than
    fit a typical window, same as any other scrollable list in this app;
    a real click needs the target actually visible, not just present in
    the (scrolled-off) content."""
    flickable = find_visual_child(root, "controlsFlickable")
    panel = find_visual_child(root, "controlsPanel")
    y_in_panel = item.mapToItem(panel, 0, 0).y()
    flickable.setProperty("contentY", max(0, y_in_panel - 20))


def test_visual_empty_state_without_features(
    analysis_view, analysis_model, find_visual_child, qtbot
):
    view = analysis_view(analysis_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)

    empty_label = find_visual_child(root, "visualEmptyStateLabel")
    assert empty_label is not None
    assert empty_label.property("visible") is True


def test_visual_shows_controls_and_feature_selector(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)

    empty_label = find_visual_child(root, "visualEmptyStateLabel")
    assert empty_label.property("visible") is False

    feature_combo = find_visual_child(root, "featureCombo")
    assert feature_combo.property("count") == 1

    colormap_combo = find_visual_child(root, "colormapCombo")
    assert colormap_combo is not None

    tile_s1 = find_visual_child(root, "heatmapTile_s1")
    tile_s2 = find_visual_child(root, "heatmapTile_s2")
    assert tile_s1 is not None
    assert tile_s2 is not None


def test_visual_layer_button_click_updates_data_layer(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)

    section = find_visual_child(root, "visualSection")
    assert section.property("dataLayer") == "TIC"

    raw_button = find_visual_child(root, "rawLayerButton")
    center = raw_button.mapToScene(raw_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)

    assert section.property("dataLayer") == "raw"


def test_visual_sample_visibility_toggle_removes_tile(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)

    assert find_visual_child(root, "heatmapTile_s2") is not None

    checkbox = find_visual_child(root, "sampleVisibility_s2")
    _scroll_controls_panel_to(root, find_visual_child, checkbox)
    center = checkbox.mapToScene(checkbox.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)

    assert find_visual_child(root, "heatmapTile_s2") is None
    assert find_visual_child(root, "heatmapTile_s1") is not None


def test_visual_grid_cols_spinbox_updates_section_property(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)

    section = find_visual_child(root, "visualSection")
    cols_spin = find_visual_child(root, "colsSpinBox")
    assert cols_spin.property("value") == section.property("gridCols")
