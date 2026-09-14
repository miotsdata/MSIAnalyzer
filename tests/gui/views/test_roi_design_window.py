from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
import pytest
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtQuick import QQuickItem
from scipy.sparse import csr_matrix

# Each opened RoiDesignWindow is a genuine extra top-level QWindow, on top
# of whatever the `analysis_view` fixture already accumulates across the
# whole test session (that fixture's own teardown only `.close()`s its
# views, never destroys them). Closing this file's own windows explicitly
# (even on a failing test, via this fixture) is cheap, correct hygiene —
# though note it does NOT fully eliminate a separate, pre-existing
# full-suite-only flakiness this codebase already documents elsewhere (see
# find_visual_child's docstring): occasionally, a *later, unrelated* test
# hits a stale-QML-binding AttributeError tied to how many top-level
# windows/QQuickViews have piled up in the same process, not to anything
# specific to this file. Confirmed via isolated reruns of the affected
# test, which always pass on their own.
_open_roi_windows = []


@pytest.fixture(autouse=True)
def _close_roi_windows_after_test():
    yield
    for window in _open_roi_windows:
        window.close()
    _open_roi_windows.clear()


def _as_list(value):
    """A QML `property var` list read back via `.property()` — see the
    identical helper in test_visual_inspection_section.py: a JS array
    built inside QML (e.g. `draftVertices`, `.concat()`-built) comes back
    wrapped in a QJSValue and needs `.toVariant()`."""
    return value.toVariant() if hasattr(value, "toVariant") else value


def _write_grid_h5ad(path, n=4):
    """An n x n spatial grid (integer coordinates 0..n-1), one feature —
    same shape as tests/core/unit/test_roi.py's fixture, so grid-index
    space vertices map onto obs rows the same simple way (pixel (x, y) is
    exactly grid-index (col, row) since coordinates are already 0..n-1)."""
    coords = [(x, y) for y in range(n) for x in range(n)]
    obs = pd.DataFrame(index=[f"px{i}" for i in range(len(coords))])
    var = pd.DataFrame({"mz": [150.0]}, index=["mz_150.0000"])
    X = csr_matrix(np.arange(len(coords), dtype=np.float32).reshape(-1, 1))
    adata = ad.AnnData(X=X, obs=obs, var=var)
    adata.obsm["spatial"] = np.array(coords, dtype=float)
    adata.write_h5ad(path)


def _click(window, item, qtbot):
    qtbot.mouseClick(window, Qt.LeftButton, pos=item.mapToScene(
        item.boundingRect().center()).toPoint())
    qtbot.wait(30)


def _open_visual_tab(view, root, find_visual_child, qtbot):
    nav_button = find_visual_child(root, "navButton_3")
    center = nav_button.mapToScene(nav_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)


def _open_roi_design_window(view, root, find_visual_child, qtbot):
    _open_visual_tab(view, root, find_visual_child, qtbot)
    button = find_visual_child(root, "openRoiDesignButton")
    center = button.mapToScene(button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(100)
    window = root.findChild(QObject, "roiDesignWindow")
    assert window is not None
    qtbot.waitUntil(lambda: window.property("visible") is True, timeout=2000)
    _open_roi_windows.append(window)
    return window


def _start_drawing(window, name, qtbot):
    """"Add ROI" -> set a name -> "Start Drawing" — the naming step that
    now precedes any vertex placement (name/color are chosen up front, not
    after the shape is closed)."""
    add_button = window.findChild(QQuickItem, "roiAddButton")
    _click(window, add_button, qtbot)
    assert window.property("draftState") == "naming"

    # Set draftName directly rather than driving the TextField's own text
    # input — this codebase's tests don't simulate real keystrokes into
    # text controls elsewhere either (see e.g. ComboBox popups, never
    # clicked through in these tests); onTextEdited only fires on genuine
    # user edits, not a plain setProperty("text", ...) on the field.
    window.setProperty("draftName", name)

    start_button = window.findChild(QQuickItem, "roiStartDrawingButton")
    assert start_button.property("enabled") is True
    _click(window, start_button, qtbot)
    assert window.property("draftState") == "drawing"


def _tap_grid_point(window, canvas, col, row, qtbot):
    """Click the point in `canvas` (the RoiDrawingCanvas) corresponding to
    grid-index (col, row)'s pixel *center* — the same convention
    RoiOverlay.toItemX/toItemY and core.plotting.roi.polygon_pixel_mask
    use. Moves the mouse there first so HoverHandler's own close-affordance
    tracking (driven by real pointer movement, not clicks) is up to date
    before the tap — otherwise a tap meant to land on the highlighted
    first-vertex marker could be evaluated against a stale hover position
    from the previous click instead."""
    overlay_holder = canvas.findChild(QQuickItem, "zoomableImageOverlayHolder")
    zoom_image = canvas.findChild(QQuickItem, "roiZoomableImage")
    scale = zoom_image.property("effectiveScale")
    point = overlay_holder.mapToScene(
        QPointF((col + 0.5) * scale, (row + 0.5) * scale)
    ).toPoint()
    qtbot.mouseMove(window, pos=point)
    qtbot.mouseClick(window, Qt.LeftButton, pos=point)
    qtbot.wait(30)


def test_draw_saves_immediately_on_close_writes_h5ad(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    out_dir = Path(visual_analysis_model.outDir)
    _write_grid_h5ad(out_dir / "s1.h5ad")
    _write_grid_h5ad(out_dir / "s2.h5ad")

    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    window = _open_roi_design_window(view, root, find_visual_child, qtbot)
    canvas = window.findChild(QQuickItem, "roiDrawingCanvas")
    assert canvas is not None

    # A tap before drawing has started does nothing.
    _tap_grid_point(window, canvas, 1, 1, qtbot)
    assert _as_list(window.property("draftVertices")) == []

    _start_drawing(window, "liver", qtbot)

    # A square spanning grid-index 1..3 -> pixel centers (1.5,1.5),
    # (2.5,1.5), (1.5,2.5), (2.5,2.5), i.e. the 4 pixels at
    # (x, y) in {1, 2} x {1, 2} (see tests/core/unit/test_roi.py).
    _tap_grid_point(window, canvas, 1, 1, qtbot)
    _tap_grid_point(window, canvas, 3, 1, qtbot)
    assert window.property("canClose") is False
    _tap_grid_point(window, canvas, 3, 3, qtbot)
    assert window.property("canClose") is True
    _tap_grid_point(window, canvas, 1, 3, qtbot)
    assert len(_as_list(window.property("draftVertices"))) == 4

    # Tapping back on the (now-highlighted) first vertex closes the shape
    # and saves it immediately — no separate Save step.
    _tap_grid_point(window, canvas, 1, 1, qtbot)
    qtbot.wait(100)

    assert window.property("saveError") == ""
    assert window.property("draftState") == "idle"

    reread = ad.read_h5ad(out_dir / "s1.h5ad")
    assert "roi_liver" in reread.obs.columns
    assert reread.obs["roi_liver"].sum() == 4
    assert reread.uns["rois"]["liver"]["color"]

    # The draft resets and the saved ROI now shows in the sample's list.
    assert _as_list(window.property("draftVertices")) == []
    saved_names = [r["name"] for r in _as_list(window.property("savedRois"))]
    assert "liver" in saved_names


def test_undo_removes_last_vertex(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    out_dir = Path(visual_analysis_model.outDir)
    _write_grid_h5ad(out_dir / "s1.h5ad")
    _write_grid_h5ad(out_dir / "s2.h5ad")

    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    window = _open_roi_design_window(view, root, find_visual_child, qtbot)
    canvas = window.findChild(QQuickItem, "roiDrawingCanvas")
    _start_drawing(window, "liver", qtbot)

    _tap_grid_point(window, canvas, 1, 1, qtbot)
    _tap_grid_point(window, canvas, 3, 1, qtbot)
    assert len(_as_list(window.property("draftVertices"))) == 2

    undo_button = window.findChild(QQuickItem, "roiUndoButton")
    _click(window, undo_button, qtbot)

    assert len(_as_list(window.property("draftVertices"))) == 1


def test_cancel_clears_the_draft(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    out_dir = Path(visual_analysis_model.outDir)
    _write_grid_h5ad(out_dir / "s1.h5ad")
    _write_grid_h5ad(out_dir / "s2.h5ad")

    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    window = _open_roi_design_window(view, root, find_visual_child, qtbot)
    canvas = window.findChild(QQuickItem, "roiDrawingCanvas")
    _start_drawing(window, "liver", qtbot)

    _tap_grid_point(window, canvas, 1, 1, qtbot)
    _tap_grid_point(window, canvas, 3, 1, qtbot)
    _tap_grid_point(window, canvas, 3, 3, qtbot)
    assert len(_as_list(window.property("draftVertices"))) == 3

    cancel_button = window.findChild(QQuickItem, "roiCancelButton")
    _click(window, cancel_button, qtbot)

    assert _as_list(window.property("draftVertices")) == []
    assert window.property("draftState") == "idle"


def test_cancel_naming_before_drawing_starts(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    out_dir = Path(visual_analysis_model.outDir)
    _write_grid_h5ad(out_dir / "s1.h5ad")
    _write_grid_h5ad(out_dir / "s2.h5ad")

    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    window = _open_roi_design_window(view, root, find_visual_child, qtbot)

    add_button = window.findChild(QQuickItem, "roiAddButton")
    _click(window, add_button, qtbot)
    window.setProperty("draftName", "liver")

    cancel_button = window.findChild(QQuickItem, "roiCancelNamingButton")
    _click(window, cancel_button, qtbot)

    assert window.property("draftState") == "idle"
    assert window.property("draftName") == ""


def test_zoom_buttons_change_canvas_zoom(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    out_dir = Path(visual_analysis_model.outDir)
    _write_grid_h5ad(out_dir / "s1.h5ad")
    _write_grid_h5ad(out_dir / "s2.h5ad")

    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    window = _open_roi_design_window(view, root, find_visual_child, qtbot)
    canvas = window.findChild(QQuickItem, "roiDrawingCanvas")

    assert canvas.property("zoom") == 1.0

    zoom_in = window.findChild(QQuickItem, "roiZoomInButton")
    _click(window, zoom_in, qtbot)
    zoomed_once = canvas.property("zoom")
    assert zoomed_once > 1.0
    _click(window, zoom_in, qtbot)
    assert canvas.property("zoom") > zoomed_once

    # minZoom (ZoomableImage.qml) floors at 1.0 ("fit to view") — zooming
    # out from a zoomed-in state should move back *toward* 1.0, not below it.
    zoom_out = window.findChild(QQuickItem, "roiZoomOutButton")
    _click(window, zoom_out, qtbot)
    assert 1.0 <= canvas.property("zoom") <= zoomed_once

    reset = window.findChild(QQuickItem, "roiZoomResetButton")
    _click(window, reset, qtbot)
    assert canvas.property("zoom") == 1.0


def test_delete_roi_removes_it_from_sample_and_catalog(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    from msianalyzer.core.plotting import roi as roi_module

    out_dir = Path(visual_analysis_model.outDir)
    _write_grid_h5ad(out_dir / "s1.h5ad")
    _write_grid_h5ad(out_dir / "s2.h5ad")
    roi_module.save_roi_to_sample(
        out_dir / "s1.h5ad", "liver", "#ff0000", [[1, 1], [3, 1], [3, 3], [1, 3]]
    )

    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    window = _open_roi_design_window(view, root, find_visual_child, qtbot)

    qtbot.waitUntil(
        lambda: len(_as_list(window.property("savedRois"))) == 1, timeout=2000
    )
    # Repeater-created delegates aren't reachable via QObject.findChild
    # (see find_visual_child's own docstring) — walk the visual tree from
    # the window's root content item instead.
    qtbot.waitUntil(
        lambda: find_visual_child(window.contentItem(), "roiDeleteButton_liver") is not None,
        timeout=2000,
    )
    delete_button = find_visual_child(window.contentItem(), "roiDeleteButton_liver")
    _click(window, delete_button, qtbot)
    qtbot.wait(100)

    assert _as_list(window.property("savedRois")) == []
    reread = ad.read_h5ad(out_dir / "s1.h5ad")
    assert "roi_liver" not in reread.obs.columns


def test_saving_against_analysis_db_predating_rois_table_creates_it(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    """An analysis run before this feature existed has no `rois` table —
    saving must create it on the fly rather than crashing."""
    import sqlite3

    out_dir = Path(visual_analysis_model.outDir)
    _write_grid_h5ad(out_dir / "s1.h5ad")
    _write_grid_h5ad(out_dir / "s2.h5ad")

    with sqlite3.connect(visual_analysis_model.analysisDbPath) as con:
        con.execute("DROP TABLE IF EXISTS rois")
        con.commit()

    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    window = _open_roi_design_window(view, root, find_visual_child, qtbot)
    canvas = window.findChild(QQuickItem, "roiDrawingCanvas")
    _start_drawing(window, "liver", qtbot)

    _tap_grid_point(window, canvas, 1, 1, qtbot)
    _tap_grid_point(window, canvas, 3, 1, qtbot)
    _tap_grid_point(window, canvas, 3, 3, qtbot)
    _tap_grid_point(window, canvas, 1, 3, qtbot)
    _tap_grid_point(window, canvas, 1, 1, qtbot)
    qtbot.wait(100)

    assert window.property("saveError") == ""
    with sqlite3.connect(visual_analysis_model.analysisDbPath) as con:
        rows = con.execute("SELECT name FROM rois").fetchall()
    assert rows == [("liver",)]


def test_reopening_the_window_shows_previously_saved_rois(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    """Reported bug: draw + save an ROI, close the window, reopen it (via
    Visual Inspection's "Draw ROI" button again) — the ROI must still show
    up, both in the saved-ROI list and as an overlay on the image."""
    out_dir = Path(visual_analysis_model.outDir)
    _write_grid_h5ad(out_dir / "s1.h5ad")
    _write_grid_h5ad(out_dir / "s2.h5ad")

    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    window = _open_roi_design_window(view, root, find_visual_child, qtbot)
    canvas = window.findChild(QQuickItem, "roiDrawingCanvas")
    _start_drawing(window, "liver", qtbot)
    _tap_grid_point(window, canvas, 1, 1, qtbot)
    _tap_grid_point(window, canvas, 3, 1, qtbot)
    _tap_grid_point(window, canvas, 3, 3, qtbot)
    _tap_grid_point(window, canvas, 1, 3, qtbot)
    _tap_grid_point(window, canvas, 1, 1, qtbot)
    qtbot.wait(100)
    assert window.property("saveError") == ""
    assert len(_as_list(window.property("savedRois"))) == 1

    # Close the window (as if the user clicked the OS close button).
    window.setProperty("visible", False)
    qtbot.wait(50)

    # Reopen it via Visual Inspection's "Draw ROI" button again.
    button = find_visual_child(root, "openRoiDesignButton")
    qtbot.mouseClick(view, Qt.LeftButton, pos=button.mapToScene(
        button.boundingRect().center()).toPoint())
    qtbot.wait(100)
    qtbot.waitUntil(lambda: window.property("visible") is True, timeout=2000)

    saved = _as_list(window.property("savedRois"))
    assert [r["name"] for r in saved] == ["liver"]

    canvas = window.findChild(QQuickItem, "roiDrawingCanvas")
    overlay = canvas.findChild(QQuickItem, "roiOverlay")
    assert len(_as_list(overlay.property("savedRois"))) == 1


def test_window_always_opens_on_the_first_sample(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    """The window always opens on the first sample, full stop — no
    guessing which sample to land on based on where ROIs happen to exist.
    "Other ROIs in analyses" (see below) is what surfaces cross-sample ROI
    status instead."""
    out_dir = Path(visual_analysis_model.outDir)
    _write_grid_h5ad(out_dir / "s1.h5ad")
    _write_grid_h5ad(out_dir / "s2.h5ad")

    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    window = _open_roi_design_window(view, root, find_visual_child, qtbot)

    window.setProperty("selectedSampleIndex", 1)
    qtbot.wait(30)
    assert window.property("selectedSample")["name"] == "s2"

    canvas = window.findChild(QQuickItem, "roiDrawingCanvas")
    _start_drawing(window, "kidney", qtbot)
    _tap_grid_point(window, canvas, 1, 1, qtbot)
    _tap_grid_point(window, canvas, 3, 1, qtbot)
    _tap_grid_point(window, canvas, 3, 3, qtbot)
    _tap_grid_point(window, canvas, 1, 3, qtbot)
    _tap_grid_point(window, canvas, 1, 1, qtbot)
    qtbot.wait(100)
    assert window.property("saveError") == ""

    window.setProperty("visible", False)
    qtbot.wait(50)

    button = find_visual_child(root, "openRoiDesignButton")
    qtbot.mouseClick(view, Qt.LeftButton, pos=button.mapToScene(
        button.boundingRect().center()).toPoint())
    qtbot.wait(100)
    qtbot.waitUntil(lambda: window.property("visible") is True, timeout=2000)

    # Back to s1, regardless of "kidney" living on s2.
    assert window.property("selectedSample")["name"] == "s1"
    assert _as_list(window.property("savedRois")) == []


def test_other_rois_lists_catalog_entries_missing_from_this_sample(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    from msianalyzer.core import analysis_db
    from msianalyzer.core.plotting import roi as roi_module

    out_dir = Path(visual_analysis_model.outDir)
    _write_grid_h5ad(out_dir / "s1.h5ad")
    _write_grid_h5ad(out_dir / "s2.h5ad")
    roi_module.save_roi_to_sample(
        out_dir / "s1.h5ad", "liver", "#ff0000", [[1, 1], [3, 1], [3, 3], [1, 3]]
    )
    # "Other ROIs in analyses" is sourced from the analysis-wide catalog
    # table (what AnalysisBridge.saveRoi would also register), not from
    # scanning every sample's h5ad directly.
    analysis_db.register_roi(visual_analysis_model.analysisDbPath, "liver", "#ff0000")

    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    window = _open_roi_design_window(view, root, find_visual_child, qtbot)

    # On s1 (has "liver"): it's in "ROIs on this sample", not "Other ROIs".
    assert [r["name"] for r in _as_list(window.property("savedRois"))] == ["liver"]
    assert _as_list(window.property("otherRois")) == []

    # Switch to s2 (doesn't have it yet): the reverse.
    window.setProperty("selectedSampleIndex", 1)
    qtbot.wait(30)
    assert _as_list(window.property("savedRois")) == []
    other = _as_list(window.property("otherRois"))
    assert [r["name"] for r in other] == ["liver"]
    assert other[0]["color"] == "#ff0000"

    draw_button = find_visual_child(window.contentItem(), "drawForSampleButton_liver")
    assert draw_button is not None


def test_draw_for_this_sample_starts_drawing_with_existing_name_and_color(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    from msianalyzer.core import analysis_db
    from msianalyzer.core.plotting import roi as roi_module

    out_dir = Path(visual_analysis_model.outDir)
    _write_grid_h5ad(out_dir / "s1.h5ad")
    _write_grid_h5ad(out_dir / "s2.h5ad")
    roi_module.save_roi_to_sample(
        out_dir / "s1.h5ad", "liver", "#ff0000", [[1, 1], [3, 1], [3, 3], [1, 3]]
    )
    analysis_db.register_roi(visual_analysis_model.analysisDbPath, "liver", "#ff0000")

    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    window = _open_roi_design_window(view, root, find_visual_child, qtbot)
    window.setProperty("selectedSampleIndex", 1)  # s2, doesn't have "liver" yet
    qtbot.wait(30)

    draw_button = find_visual_child(window.contentItem(), "drawForSampleButton_liver")
    _click(window, draw_button, qtbot)

    # Straight into drawing, no naming step — same name/color as the
    # catalog entry, no vertices yet.
    assert window.property("draftState") == "drawing"
    assert window.property("draftName") == "liver"
    assert window.property("draftColor").name() == "#ff0000"
    assert _as_list(window.property("draftVertices")) == []

    canvas = window.findChild(QQuickItem, "roiDrawingCanvas")
    _tap_grid_point(window, canvas, 1, 1, qtbot)
    _tap_grid_point(window, canvas, 3, 1, qtbot)
    _tap_grid_point(window, canvas, 3, 3, qtbot)
    _tap_grid_point(window, canvas, 1, 3, qtbot)
    _tap_grid_point(window, canvas, 1, 1, qtbot)
    qtbot.wait(100)

    assert window.property("saveError") == ""
    reread = ad.read_h5ad(out_dir / "s2.h5ad")
    assert "roi_liver" in reread.obs.columns
    assert reread.uns["rois"]["liver"]["color"] == "#ff0000"

    # Now on this sample's own list, no longer in "Other ROIs".
    assert [r["name"] for r in _as_list(window.property("savedRois"))] == ["liver"]
    assert _as_list(window.property("otherRois")) == []
