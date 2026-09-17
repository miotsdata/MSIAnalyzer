import sqlite3
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtQuick import QQuickItem

from msianalyzer.core.registration import attach_he_image

# Same reasoning as test_roi_design_window.py's identical fixture: close
# every window this file opens, even on a failing test.
_open_coreg_windows = []


@pytest.fixture(autouse=True)
def _close_coreg_windows_after_test():
    yield
    for window in _open_coreg_windows:
        window.close()
    _open_coreg_windows.clear()


def _as_list(value):
    """A QML `property var` list read back via `.property()` — see the
    identical helper in test_visual_inspection_section.py/
    test_roi_design_window.py: a JS array built inside QML (e.g.
    `landmarks`, `.concat()`-built) comes back wrapped in a QJSValue and
    needs `.toVariant()`."""
    return value.toVariant() if hasattr(value, "toVariant") else value


def _make_png(path: Path, size=(40, 30)) -> None:
    width, height = size
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    Image.fromarray(arr).save(path, format="PNG")


@pytest.fixture
def coreg_analysis_model(analysis_model, tmp_path):
    """`analysis_model`, seeded with 2 samples backed by REAL raw
    per-sample database paths under `tmp_path` — unlike
    `visual_analysis_model`'s placeholder `'a.db'`/`'b.db'` strings, these
    need to be real paths `attach_he_image` can actually write under."""
    raw1 = tmp_path / "s1.db"
    raw2 = tmp_path / "s2.db"
    with sqlite3.connect(analysis_model.analysisDbPath) as con:
        con.execute(
            "INSERT INTO samples (sample_id, name, raw_db_path, polarity) "
            "VALUES (1, 's1', ?, 'positive')", (str(raw1),),
        )
        con.execute(
            "INSERT INTO samples (sample_id, name, raw_db_path, polarity) "
            "VALUES (2, 's2', ?, 'positive')", (str(raw2),),
        )
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) "
            "VALUES (1, 150.0, '{\"s1\": 0, \"s2\": 0}')"
        )
        con.commit()
    return analysis_model


def _open_visual_tab(view, root, find_visual_child, qtbot):
    nav_button = find_visual_child(root, "navButton_3")
    center = nav_button.mapToScene(nav_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)
    controls = find_visual_child(root, "controlsFlickable")
    qtbot.waitUntil(lambda: not controls.property("featuresLoading"), timeout=2000)


def _open_coregistration_window(view, root, find_visual_child, qtbot):
    _open_visual_tab(view, root, find_visual_child, qtbot)
    button = find_visual_child(root, "openCoregistrationButton")
    center = button.mapToScene(button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(100)
    window = root.findChild(QObject, "coregistrationWindow")
    assert window is not None
    qtbot.waitUntil(lambda: window.property("visible") is True, timeout=2000)
    _open_coreg_windows.append(window)
    return window


def _click(window, item, qtbot):
    qtbot.mouseClick(window, Qt.LeftButton, pos=item.mapToScene(
        item.boundingRect().center()).toPoint())
    qtbot.wait(30)


def _tap_pane_point(window, zoom_image, x, y, qtbot):
    """Click the point in `zoom_image` (a ZoomableImage) at native
    pixel-coordinate (x, y) — the landmark analogue of
    test_roi_design_window.py's `_tap_grid_point`, but with no `+0.5`
    cell-center offset (a landmark is one clicked point, not a polygon
    cell-membership test — see LandmarkOverlay.qml)."""
    overlay_holder = zoom_image.findChild(QQuickItem, "zoomableImageOverlayHolder")
    scale = zoom_image.property("effectiveScale")
    point = overlay_holder.mapToScene(QPointF(x * scale, y * scale)).toPoint()
    qtbot.mouseMove(window, pos=point)
    qtbot.mouseClick(window, Qt.LeftButton, pos=point)
    qtbot.wait(30)


def _attach_image_to_s1(coreg_analysis_model, size=(40, 30)):
    raw_db_path = Path(sqlite3.connect(coreg_analysis_model.analysisDbPath).execute(
        "SELECT raw_db_path FROM samples WHERE name = 's1'"
    ).fetchone()[0])
    image_path = raw_db_path.parent / "slide.png"
    _make_png(image_path, size=size)
    attach_he_image(raw_db_path, image_path)
    return raw_db_path


def test_coregistration_window_opens_with_no_image_state(
    analysis_view, coreg_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(coreg_analysis_model)
    root = view.rootObject()
    window = _open_coregistration_window(view, root, find_visual_child, qtbot)

    no_image_label = window.findChild(QQuickItem, "noImageLabel")
    assert no_image_label.property("visible") is True
    status_label = window.findChild(QQuickItem, "attachedImageStatusLabel")
    assert status_label.property("text") == "No image attached"
    fit_button = window.findChild(QQuickItem, "fitAndSaveButton")
    assert fit_button.property("enabled") is False


def test_coregistration_window_shows_attached_image_status(
    analysis_view, coreg_analysis_model, find_visual_child, qtbot
):
    _attach_image_to_s1(coreg_analysis_model, size=(40, 30))

    view = analysis_view(coreg_analysis_model)
    root = view.rootObject()
    window = _open_coregistration_window(view, root, find_visual_child, qtbot)

    no_image_label = window.findChild(QQuickItem, "noImageLabel")
    assert no_image_label.property("visible") is False
    status_label = window.findChild(QQuickItem, "attachedImageStatusLabel")
    assert status_label.property("text") == "PNG 40x30"


def test_tapping_both_panes_creates_a_landmark_pair(
    analysis_view, coreg_analysis_model, find_visual_child, qtbot
):
    _attach_image_to_s1(coreg_analysis_model)

    view = analysis_view(coreg_analysis_model)
    root = view.rootObject()
    window = _open_coregistration_window(view, root, find_visual_child, qtbot)

    he_zoom = window.findChild(QQuickItem, "heZoomableImage")
    grid_zoom = window.findChild(QQuickItem, "gridZoomableImage")

    _tap_pane_point(window, he_zoom, 5, 5, qtbot)
    assert window.property("pendingSource") == "he"

    _tap_pane_point(window, grid_zoom, 1, 1, qtbot)
    assert window.property("pendingSource") == ""
    landmarks = _as_list(window.property("landmarks"))
    assert len(landmarks) == 1
    # Loose tolerance: mapToScene(...).toPoint() rounds to the nearest
    # screen pixel, and the He pane's effectiveScale is a real (non-1.0)
    # value once a real image is loaded, so a screen-pixel rounding error
    # translates back to a small but nonzero error in source coordinates.
    assert landmarks[0]["heX"] == pytest.approx(5.0, abs=0.5)
    assert landmarks[0]["gridY"] == pytest.approx(1.0, abs=0.5)


def test_tapping_same_pane_twice_moves_the_pending_point(
    analysis_view, coreg_analysis_model, find_visual_child, qtbot
):
    _attach_image_to_s1(coreg_analysis_model)

    view = analysis_view(coreg_analysis_model)
    root = view.rootObject()
    window = _open_coregistration_window(view, root, find_visual_child, qtbot)

    he_zoom = window.findChild(QQuickItem, "heZoomableImage")
    _tap_pane_point(window, he_zoom, 5, 5, qtbot)
    _tap_pane_point(window, he_zoom, 8, 8, qtbot)

    assert window.property("pendingSource") == "he"
    pending = _as_list(window.property("pendingPoint"))
    assert pending["x"] == pytest.approx(8.0, abs=0.5)
    assert _as_list(window.property("landmarks")) == []


def test_undo_removes_last_landmark(
    analysis_view, coreg_analysis_model, find_visual_child, qtbot
):
    _attach_image_to_s1(coreg_analysis_model)

    view = analysis_view(coreg_analysis_model)
    root = view.rootObject()
    window = _open_coregistration_window(view, root, find_visual_child, qtbot)

    he_zoom = window.findChild(QQuickItem, "heZoomableImage")
    grid_zoom = window.findChild(QQuickItem, "gridZoomableImage")
    _tap_pane_point(window, he_zoom, 5, 5, qtbot)
    _tap_pane_point(window, grid_zoom, 1, 1, qtbot)
    assert len(_as_list(window.property("landmarks"))) == 1

    undo_button = window.findChild(QQuickItem, "landmarkUndoButton")
    _click(window, undo_button, qtbot)

    assert _as_list(window.property("landmarks")) == []


def test_fit_and_save_button_enables_after_3_landmarks_and_saves(
    analysis_view, coreg_analysis_model, find_visual_child, qtbot
):
    _attach_image_to_s1(coreg_analysis_model)

    view = analysis_view(coreg_analysis_model)
    root = view.rootObject()
    window = _open_coregistration_window(view, root, find_visual_child, qtbot)

    he_zoom = window.findChild(QQuickItem, "heZoomableImage")
    grid_zoom = window.findChild(QQuickItem, "gridZoomableImage")
    fit_button = window.findChild(QQuickItem, "fitAndSaveButton")

    pairs = [((5, 5), (1, 1)), ((15, 5), (3, 1)), ((5, 15), (1, 3))]
    for (he_x, he_y), (grid_x, grid_y) in pairs[:-1]:
        _tap_pane_point(window, he_zoom, he_x, he_y, qtbot)
        _tap_pane_point(window, grid_zoom, grid_x, grid_y, qtbot)
        assert fit_button.property("enabled") is False

    (he_x, he_y), (grid_x, grid_y) = pairs[-1]
    _tap_pane_point(window, he_zoom, he_x, he_y, qtbot)
    _tap_pane_point(window, grid_zoom, grid_x, grid_y, qtbot)
    assert fit_button.property("enabled") is True

    _click(window, fit_button, qtbot)

    status_label = window.findChild(QQuickItem, "saveStatusLabel")
    assert status_label.property("visible") is True
    assert "RMSE" in status_label.property("text")
    error_label = window.findChild(QQuickItem, "saveErrorLabel")
    assert error_label.property("visible") is False
