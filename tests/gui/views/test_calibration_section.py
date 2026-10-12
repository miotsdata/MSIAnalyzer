import sqlite3
from pathlib import Path
from types import SimpleNamespace

import anndata as ad
import numpy as np
import pandas as pd
import pytest
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtQuick import QQuickItem
from scipy.sparse import csr_matrix

from msianalyzer.core.analysis_db import (
    append_injected_features,
    init_analysis_db,
    log_command,
    register_sample,
)
from msianalyzer.core.parser.mzml_parser import array_to_blob
from msianalyzer.gui.models.analysis import AnalysisModel
from msianalyzer.gui.models.project import ProjectModel
from msianalyzer.gui.utils.analysis_bridge import AnalysisBridge

# Every opened RoiDesignWindow is a genuine extra top-level QWindow — see
# test_roi_design_window.py's identical fixture/comment for why these are
# explicitly closed after each test.
_open_roi_windows = []


@pytest.fixture(autouse=True)
def _close_roi_windows_after_test():
    yield
    for window in _open_roi_windows:
        window.close()
    _open_roi_windows.clear()


def _as_list(value):
    return value.toVariant() if hasattr(value, "toVariant") else value


def _click(window, item, qtbot):
    qtbot.mouseClick(
        window, Qt.LeftButton, pos=item.mapToScene(item.boundingRect().center()).toPoint()
    )
    qtbot.wait(30)


def _tap_grid_point(window, canvas, col, row, qtbot):
    """Same convention as test_roi_design_window.py's identical helper —
    grid-index (col, row)'s pixel center, in the RoiDrawingCanvas' own
    scaled/zoomed coordinate space."""
    overlay_holder = canvas.findChild(QQuickItem, "zoomableImageOverlayHolder")
    zoom_image = canvas.findChild(QQuickItem, "roiZoomableImage")
    scale = zoom_image.property("effectiveScale")
    point = overlay_holder.mapToScene(
        QPointF((col + 0.5) * scale, (row + 0.5) * scale)
    ).toPoint()
    qtbot.mouseMove(window, pos=point)
    qtbot.mouseClick(window, Qt.LeftButton, pos=point)
    qtbot.wait(30)


def _start_drawing(window, name, qtbot):
    add_button = window.findChild(QQuickItem, "roiAddButton")
    _click(window, add_button, qtbot)
    assert window.property("draftState") == "naming"
    window.setProperty("draftName", name)
    start_button = window.findChild(QQuickItem, "roiStartDrawingButton")
    _click(window, start_button, qtbot)
    assert window.property("draftState") == "drawing"


def _build_calibration_raw_db(raw_db_path, grid_n=4, ref_mz=100.0, analyte_mz=200.0):
    """A grid_n x grid_n raw db (pixel_id 1..n, x/y = grid indices 0..n-1,
    matching test_roi_design_window.py's `_write_grid_h5ad` convention),
    each pixel carrying both a reference and an analyte m/z peak."""
    coords = [(x, y) for y in range(grid_n) for x in range(grid_n)]
    with sqlite3.connect(raw_db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE ms1_scans (
                scan_id INTEGER PRIMARY KEY, rt REAL NOT NULL, n_peaks INTEGER,
                mz_min REAL, mz_max REAL, tic REAL, polarity TEXT,
                mz_array BLOB NOT NULL, intensity_array BLOB NOT NULL
            );
            CREATE TABLE spatial_pixels (
                pixel_id INTEGER PRIMARY KEY AUTOINCREMENT,
                x INTEGER NOT NULL, y INTEGER NOT NULL,
                t_start REAL NOT NULL, t_end REAL NOT NULL
            );
            CREATE TABLE pixel_ms1_scans (
                pixel_id INTEGER NOT NULL, scan_id INTEGER NOT NULL,
                PRIMARY KEY (pixel_id, scan_id)
            );
            """
        )
        for i, (x, y) in enumerate(coords, start=1):
            mz_arr = np.array([ref_mz, analyte_mz])
            int_arr = np.array([100.0, 10.0 + i])
            conn.execute(
                "INSERT INTO ms1_scans VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    i, 1.0, 2, ref_mz, analyte_mz, float(np.sum(int_arr)), "positive",
                    array_to_blob(mz_arr), array_to_blob(int_arr),
                ),
            )
            conn.execute(
                "INSERT INTO spatial_pixels (pixel_id, x, y, t_start, t_end) "
                "VALUES (?, ?, ?, ?, ?)",
                (i, x, y, 0.0, 1.0),
            )
            conn.execute("INSERT INTO pixel_ms1_scans VALUES (?, ?)", (i, i))
        conn.commit()


def _write_targeted_h5ad(path, grid_n=4, ref_mz=100.0, analyte_mz=200.0):
    """Both the reference m/z *and* the analyte already quantified — the
    shape a real targeted run's assemble_adata step produces now that every
    configured analyte is baked in at run time (no more on-demand,
    per-click extraction). Analyte values mirror
    `_build_calibration_raw_db`'s own `10.0 + i` per pixel."""
    coords = [(x, y) for y in range(grid_n) for x in range(grid_n)]
    n = len(coords)
    obs = pd.DataFrame(index=[str(i) for i in range(1, n + 1)])
    var = pd.DataFrame(
        {"mz": [ref_mz, analyte_mz]},
        index=[f"mz_{ref_mz:.4f}", f"mz_{analyte_mz:.4f}"],
    )
    ref_col = np.full(n, 100.0, dtype=np.float32)
    analyte_col = np.array([10.0 + i for i in range(1, n + 1)], dtype=np.float32)
    X = csr_matrix(np.column_stack([ref_col, analyte_col]))
    adata = ad.AnnData(X=X, obs=obs, var=var)
    adata.obsm["spatial"] = np.array(coords, dtype=float)
    adata.write_h5ad(path)


@pytest.fixture
def calibration_analysis_model(tmp_path, project):
    """A targeted `AnalysisModel` with one sample "s1": a real raw db (4x4
    grid, reference m/z 100.0 + analyte m/z 200.0), an initial `.h5ad`
    already carrying both columns, and the analyte registered as a
    browsable ("injected") feature — the shape a real targeted run
    produces end to end, so the Calibration workspace's analyte picker has
    something to show with no further extraction."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    db_path = out_dir / "analysis_test-run.db"
    init_analysis_db(db_path).close()
    log_command(
        db_path, command_name="normalize_tic",
        arguments={"mode": "tic", "reference_mz": 100.0}, run_id="test-run",
    )

    raw_db_path = tmp_path / "s1.db"
    _build_calibration_raw_db(raw_db_path)
    register_sample(db_path, name="s1", raw_db_path=str(raw_db_path))
    _write_targeted_h5ad(out_dir / "s1.h5ad")
    append_injected_features(db_path, [SimpleNamespace(mz=200.0, members_json="{}")])

    run_dict = {
        "id": "test-run",
        "start_date": "2026-01-01 12:00:00",
        "config": {
            "io": {"out_dir": str(out_dir)},
            "analysis": {"db_name": db_path.name},
            "analysis_type": "targeted",
        },
    }
    project_model = ProjectModel(project, str(tmp_path))
    return AnalysisModel(project_model, "test-run", run_dict)


@pytest.fixture
def calibration_analysis_model_with_raster_pixel_size(tmp_path, project):
    """Same shape as `calibration_analysis_model`, but "s1"'s raw db also
    has a real raster pixel size on record (as a real targeted run would
    leave behind when the acquisition's raster XML had a <rasterConfig>
    section — see Run._process_one_sample/write_raw_metadata) — for
    testing that the Calibration workspace defaults its pixel-size field
    from it."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    db_path = out_dir / "analysis_test-run.db"
    init_analysis_db(db_path).close()
    log_command(
        db_path, command_name="normalize_tic",
        arguments={"mode": "tic", "reference_mz": 100.0}, run_id="test-run",
    )

    raw_db_path = tmp_path / "s1.db"
    _build_calibration_raw_db(raw_db_path)
    from msianalyzer.core.parser.mzml_parser import write_raw_metadata

    with sqlite3.connect(raw_db_path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
    write_raw_metadata(raw_db_path, {"pixel_size_x": 0.05, "pixel_size_unit": "mm"})

    register_sample(db_path, name="s1", raw_db_path=str(raw_db_path))
    _write_targeted_h5ad(out_dir / "s1.h5ad")
    append_injected_features(db_path, [SimpleNamespace(mz=200.0, members_json="{}")])

    run_dict = {
        "id": "test-run",
        "start_date": "2026-01-01 12:00:00",
        "config": {
            "io": {"out_dir": str(out_dir)},
            "analysis": {"db_name": db_path.name},
            "analysis_type": "targeted",
        },
    }
    project_model = ProjectModel(project, str(tmp_path))
    return AnalysisModel(project_model, "test-run", run_dict)


@pytest.fixture
def two_analyte_calibration_model(tmp_path, project):
    """Same shape as `calibration_analysis_model`, but with *two* analytes
    (200.0 and 300.0) baked into the `.h5ad` and registered as features —
    reproduces the user's report that config only seemed to allow a single
    analyte m/z: it never did (NormalizationConfig.analyte_mzs is a list
    all along — see test_new_analysis_page.py's own comma-separated-field
    tests), but nothing end to end had actually exercised the Calibration
    workspace's picker with more than one analyte to choose from until now."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    db_path = out_dir / "analysis_test-run.db"
    init_analysis_db(db_path).close()
    log_command(
        db_path, command_name="normalize_tic",
        arguments={"mode": "tic", "reference_mz": 100.0}, run_id="test-run",
    )

    raw_db_path = tmp_path / "s1.db"
    _build_calibration_raw_db(raw_db_path)
    register_sample(db_path, name="s1", raw_db_path=str(raw_db_path))

    coords = [(x, y) for y in range(4) for x in range(4)]
    n = len(coords)
    obs = pd.DataFrame(index=[str(i) for i in range(1, n + 1)])
    var = pd.DataFrame(
        {"mz": [100.0, 200.0, 300.0]},
        index=["mz_100.0000", "mz_200.0000", "mz_300.0000"],
    )
    ref_col = np.full(n, 100.0, dtype=np.float32)
    analyte_a_col = np.array([10.0 + i for i in range(1, n + 1)], dtype=np.float32)
    analyte_b_col = np.array([50.0 + i for i in range(1, n + 1)], dtype=np.float32)
    X = csr_matrix(np.column_stack([ref_col, analyte_a_col, analyte_b_col]))
    adata = ad.AnnData(X=X, obs=obs, var=var)
    adata.obsm["spatial"] = np.array(coords, dtype=float)
    adata.write_h5ad(out_dir / "s1.h5ad")

    append_injected_features(
        db_path,
        [
            SimpleNamespace(mz=200.0, members_json="{}"),
            SimpleNamespace(mz=300.0, members_json="{}"),
        ],
    )

    run_dict = {
        "id": "test-run",
        "start_date": "2026-01-01 12:00:00",
        "config": {
            "io": {"out_dir": str(out_dir)},
            "analysis": {"db_name": db_path.name},
            "analysis_type": "targeted",
        },
    }
    project_model = ProjectModel(project, str(tmp_path))
    return AnalysisModel(project_model, "test-run", run_dict)


def _scroll_to(root, find_visual_child, item):
    """Scrolls `calibrationFlickable` so `item` is on-screen — the page can
    be taller than the fixed 900x700 test window (same reasoning as
    test_visual_inspection_section.py's identical `_scroll_controls_panel_to`)."""
    flickable = find_visual_child(root, "calibrationFlickable")
    y_in_flickable = item.mapToItem(flickable, 0, 0).y()
    flickable.setProperty("contentY", max(0, y_in_flickable - 20))


def _wait_for_calibration_section(root, find_visual_child, qtbot):
    """A targeted analysis' Calibration workspace is now the sole page —
    no tab to click to reach it (see AnalysisPage.qml's `isTargeted`) — so
    every test just waits for it to finish constructing.

    Also settles the background feature list fetch (requestFeatureList)
    that `controls` (HeatmapControlsPanel) kicks off as soon as `analysis`
    is set, plus the auto-range fetch its own onSelectedFeatureChanged
    fires once the analyte auto-selects — the analyte picker having no
    explicit "Load" click anymore (unlike the old text-field-plus-button
    flow) means every test now reaches this point before those background
    QThreads necessarily finish; closing the view (test teardown) while
    one is still running is a real, reproduced "QThread: Destroyed while
    thread is still running" -> segfault, not just theoretical.
    """
    qtbot.waitUntil(
        lambda: find_visual_child(root, "calibrationSection") is not None, timeout=2000
    )
    section = find_visual_child(root, "calibrationSection")
    qtbot.waitUntil(lambda: section.property("hasAnalyte") is True, timeout=2000)
    qtbot.wait(150)
    return section


def test_calibration_section_shown_directly_for_targeted_analysis(
    analysis_view, calibration_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(calibration_analysis_model)
    root = view.rootObject()

    _wait_for_calibration_section(root, find_visual_child, qtbot)
    nav_rail = find_visual_child(root, "navRail")
    assert nav_rail.property("visible") is False


def test_nav_rail_shown_and_no_calibration_section_for_untargeted_analysis(
    analysis_view, visual_analysis_model, find_visual_child
):
    view = analysis_view(visual_analysis_model)
    root = view.rootObject()

    nav_rail = find_visual_child(root, "navRail")
    assert nav_rail.property("visible") is True
    assert find_visual_child(root, "calibrationSection") is None


def test_pixel_size_defaults_from_raster_xml_metadata_when_present(
    analysis_view, calibration_analysis_model_with_raster_pixel_size, find_visual_child, qtbot
):
    # "the size of the pixels are in the xml file, we could use it" —
    # when the acquisition's raster XML recorded a pixel size (see
    # Run._process_one_sample/write_raw_metadata), the field pre-fills
    # from it instead of starting empty; Add Standard is enabled with no
    # typing needed at all once an analyte is also selected.
    view = analysis_view(calibration_analysis_model_with_raster_pixel_size)
    root = view.rootObject()
    section = _wait_for_calibration_section(root, find_visual_child, qtbot)
    qtbot.waitUntil(lambda: section.property("hasAnalyte") is True, timeout=2000)
    qtbot.waitUntil(lambda: section.property("pixelSize") > 0, timeout=2000)

    pixel_size_field = find_visual_child(section, "pixelSizeField")
    assert pixel_size_field.property("text") == "0.05"
    pixel_size_unit_combo = find_visual_child(section, "pixelSizeUnitCombo")
    assert pixel_size_unit_combo.property("currentText") == "mm"

    add_standard_button = find_visual_child(section, "addStandardButton")
    assert add_standard_button.property("enabled") is True

    # Still freely overridable — not locked to the XML's own value.
    pixel_size_field.setProperty("text", "0.2")
    qtbot.wait(30)
    assert section.property("pixelSize") == pytest.approx(0.2)


def test_pixel_size_stays_empty_when_no_raster_xml_metadata(
    analysis_view, calibration_analysis_model, find_visual_child, qtbot
):
    # The plain fixture's raw db has no pixel-size metadata at all (no
    # <rasterConfig> in its raster XML) — must behave exactly as before
    # this feature existed: empty field, user defines it themselves.
    view = analysis_view(calibration_analysis_model)
    root = view.rootObject()
    section = _wait_for_calibration_section(root, find_visual_child, qtbot)
    qtbot.waitUntil(lambda: section.property("hasAnalyte") is True, timeout=2000)

    pixel_size_field = find_visual_child(section, "pixelSizeField")
    assert pixel_size_field.property("text") == ""
    assert section.property("pixelSize") == 0.0


def test_add_standard_button_disabled_until_analyte_and_pixel_size_set(
    analysis_view, calibration_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(calibration_analysis_model)
    root = view.rootObject()
    section = _wait_for_calibration_section(root, find_visual_child, qtbot)

    add_standard_button = find_visual_child(section, "addStandardButton")
    assert add_standard_button.property("enabled") is False

    # The analyte is already baked into the run's .h5ad and registered as a
    # browsable feature (see calibration_analysis_model) — the picker
    # selects it automatically (there's only one), no click needed. This is
    # exactly the "analyte loading takes ages" complaint's fix: nothing to
    # load here at all.
    qtbot.waitUntil(lambda: section.property("hasAnalyte") is True, timeout=2000)
    assert add_standard_button.property("enabled") is False  # pixel size still unset

    pixel_size_field = find_visual_child(section, "pixelSizeField")
    pixel_size_field.setProperty("text", "0.1")
    qtbot.wait(30)
    assert add_standard_button.property("enabled") is True


def test_full_add_standard_flow_draws_roi_and_saves_amount(
    analysis_view, calibration_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(calibration_analysis_model)
    root = view.rootObject()
    section = _wait_for_calibration_section(root, find_visual_child, qtbot)

    qtbot.waitUntil(lambda: section.property("hasAnalyte") is True, timeout=2000)

    pixel_size_field = find_visual_child(section, "pixelSizeField")
    pixel_size_field.setProperty("text", "0.1")
    qtbot.wait(30)

    add_standard_button = find_visual_child(section, "addStandardButton")
    assert add_standard_button.property("enabled") is True
    _click(view, add_standard_button, qtbot)
    qtbot.wait(100)

    window = root.findChild(QObject, "standardRoiDesignWindow")
    assert window is not None
    qtbot.waitUntil(lambda: window.property("visible") is True, timeout=2000)
    _open_roi_windows.append(window)

    canvas = window.findChild(QQuickItem, "roiDrawingCanvas")
    _start_drawing(window, "std1", qtbot)
    _tap_grid_point(window, canvas, 0, 0, qtbot)
    _tap_grid_point(window, canvas, 2, 0, qtbot)
    _tap_grid_point(window, canvas, 2, 2, qtbot)
    _tap_grid_point(window, canvas, 0, 2, qtbot)
    _tap_grid_point(window, canvas, 0, 0, qtbot)  # closes + saves -> roiSaved fires
    qtbot.wait(150)

    dialog = root.findChild(QObject, "standardConcentrationDialog")
    assert dialog is not None
    qtbot.waitUntil(lambda: dialog.property("visible") is True, timeout=2000)

    amount_field = dialog.findChild(QQuickItem, "standardAmountField")
    amount_field.setProperty("text", "10.0")
    save_button = dialog.findChild(QQuickItem, "saveStandardButton")
    # standardConcentrationDialog lives in CalibrationSection.qml, part of
    # `view`'s own tree — not RoiDesignWindow's separate top-level window
    # (`window`), even though the RoiDesignWindow is what triggered it.
    _click(view, save_button, qtbot)
    qtbot.wait(100)

    assert dialog.property("visible") is False

    rows = _as_list(section.property("standardsRows"))
    assert len(rows) == 1
    assert rows[0]["roi_name"] == "std1"
    assert rows[0]["sample_name"] == "s1"
    assert rows[0]["amount"] == pytest.approx(10.0)
    assert rows[0]["n_pixels"] == 4  # a 2x2 block of the 4x4 grid

    row_item = find_visual_child(section, "standardRow_std1")
    assert row_item is not None


def test_concentration_area_unit_independent_from_pixel_size_input_unit(
    analysis_view, calibration_analysis_model, find_visual_child, qtbot
):
    # Reported directly: a realistic 100 um MSI raster pitch, entered
    # naturally in um, produced tiny pmol/um2 concentration numbers with no
    # way to see them in a more readable unit without re-entering pixel
    # size in mm (unnatural for describing a raster pitch). Fixed by making
    # "which area unit to report in" a separate choice from "which unit I
    # typed the pixel size number in" (concentrationAreaUnitCombo) — this
    # drives the conversion (_pixelSizeInAreaUnit) end to end through a
    # real Add Standard flow.
    view = analysis_view(calibration_analysis_model)
    root = view.rootObject()
    section = _wait_for_calibration_section(root, find_visual_child, qtbot)

    qtbot.waitUntil(lambda: section.property("hasAnalyte") is True, timeout=2000)

    pixel_size_field = find_visual_child(section, "pixelSizeField")
    pixel_size_field.setProperty("text", "100")
    pixel_size_unit_combo = find_visual_child(section, "pixelSizeUnitCombo")
    um_index = list(pixel_size_unit_combo.property("model")).index("um")
    pixel_size_unit_combo.setProperty("currentIndex", um_index)
    pixel_size_unit_combo.activated.emit(um_index)
    qtbot.wait(30)
    assert section.property("pixelSizeUnit") == "um"
    # concentrationAreaUnit defaults to "mm" — left alone here on purpose,
    # deliberately mismatched from the "um" just entered above.
    assert section.property("concentrationAreaUnit") == "mm"

    header = find_visual_child(section, "standardsConcentrationHeader")
    assert "pmol/mm" in header.property("text")

    add_standard_button = find_visual_child(section, "addStandardButton")
    assert add_standard_button.property("enabled") is True
    _click(view, add_standard_button, qtbot)
    qtbot.wait(100)

    window = root.findChild(QObject, "standardRoiDesignWindow")
    qtbot.waitUntil(lambda: window.property("visible") is True, timeout=2000)
    _open_roi_windows.append(window)
    canvas = window.findChild(QQuickItem, "roiDrawingCanvas")
    _start_drawing(window, "std1", qtbot)
    _tap_grid_point(window, canvas, 0, 0, qtbot)
    _tap_grid_point(window, canvas, 2, 0, qtbot)
    _tap_grid_point(window, canvas, 2, 2, qtbot)
    _tap_grid_point(window, canvas, 0, 2, qtbot)
    _tap_grid_point(window, canvas, 0, 0, qtbot)
    qtbot.wait(150)

    dialog = root.findChild(QObject, "standardConcentrationDialog")
    qtbot.waitUntil(lambda: dialog.property("visible") is True, timeout=2000)
    amount_field = dialog.findChild(QQuickItem, "standardAmountField")
    amount_field.setProperty("text", "10.0")
    save_button = dialog.findChild(QQuickItem, "saveStandardButton")
    _click(view, save_button, qtbot)
    qtbot.wait(100)

    rows = _as_list(section.property("standardsRows"))
    assert len(rows) == 1
    row = rows[0]
    # 4 pixels * (100 um -> 0.1 mm)^2 = 0.04 mm^2, NOT 4 * 100^2 = 40000 um^2.
    assert row["pixel_size_unit"] == "mm"
    assert row["area"] == pytest.approx(0.04)
    assert row["concentration"] == pytest.approx(10.0 / 0.04)

    area_label = find_visual_child(section, "standardArea_std1")
    assert "mm" in area_label.property("text")
    assert "0.04" in area_label.property("text")


def test_toggling_area_unit_after_standard_exists_updates_its_displayed_values(
    analysis_view, calibration_analysis_model, find_visual_child, qtbot
):
    # Reported directly: "I tried to change the report area unit and
    # nothing changed" — true before this fix, since the combo only
    # affected *new* standards going forward; an already-added row's
    # table cells never re-rendered in the newly selected unit. Now the
    # table always redisplays every row (regardless of which unit it was
    # originally computed in) in whatever concentrationAreaUnitCombo
    # currently has selected.
    view = analysis_view(calibration_analysis_model)
    root = view.rootObject()
    section = _wait_for_calibration_section(root, find_visual_child, qtbot)
    qtbot.waitUntil(lambda: section.property("hasAnalyte") is True, timeout=2000)

    pixel_size_field = find_visual_child(section, "pixelSizeField")
    pixel_size_field.setProperty("text", "0.1")  # mm, matches default concentrationAreaUnit
    qtbot.wait(30)

    add_standard_button = find_visual_child(section, "addStandardButton")
    _click(view, add_standard_button, qtbot)
    qtbot.wait(100)
    window = root.findChild(QObject, "standardRoiDesignWindow")
    qtbot.waitUntil(lambda: window.property("visible") is True, timeout=2000)
    _open_roi_windows.append(window)
    canvas = window.findChild(QQuickItem, "roiDrawingCanvas")
    _start_drawing(window, "std1", qtbot)
    _tap_grid_point(window, canvas, 0, 0, qtbot)
    _tap_grid_point(window, canvas, 2, 0, qtbot)
    _tap_grid_point(window, canvas, 2, 2, qtbot)
    _tap_grid_point(window, canvas, 0, 2, qtbot)
    _tap_grid_point(window, canvas, 0, 0, qtbot)
    qtbot.wait(150)
    dialog = root.findChild(QObject, "standardConcentrationDialog")
    qtbot.waitUntil(lambda: dialog.property("visible") is True, timeout=2000)
    dialog.findChild(QQuickItem, "standardAmountField").setProperty("text", "10.0")
    _click(view, dialog.findChild(QQuickItem, "saveStandardButton"), qtbot)
    qtbot.wait(100)

    area_label = find_visual_child(section, "standardArea_std1")
    concentration_label = find_visual_child(section, "standardConcentration_std1")
    # 4 pixels * 0.1mm^2 = 0.04 mm^2, concentration = 10/0.04 = 250 pmol/mm2.
    assert "mm" in area_label.property("text")
    assert float(concentration_label.property("text")) == pytest.approx(250.0)

    # Now toggle the combo, with NO new standard added — the existing
    # row's cells must update in place.
    area_unit_combo = find_visual_child(section, "concentrationAreaUnitCombo")
    um_index = list(area_unit_combo.property("model")).index("um")
    area_unit_combo.setProperty("currentIndex", um_index)
    area_unit_combo.activated.emit(um_index)
    qtbot.wait(30)

    assert section.property("concentrationAreaUnit") == "um"
    # 0.04 mm^2 -> 40000 um^2; concentration -> 250 / 1e6 = 2.5e-4 pmol/um2.
    assert "um" in area_label.property("text")
    assert "40000" in area_label.property("text") or "4.000e+4" in area_label.property("text")
    assert float(concentration_label.property("text")) == pytest.approx(2.5e-4)

    header = find_visual_child(section, "standardsConcentrationHeader")
    assert "pmol/um" in header.property("text")


@pytest.fixture
def wide_calibration_analysis_model(tmp_path, project):
    """Same shape as `calibration_analysis_model`, but on an 8x8 grid — big
    enough to fit three separate, non-overlapping standard ROIs without any
    of them touching the grid's own edge (the 4x4 fixture only has room for
    one full quadrant before a corner would fall outside the drawable
    canvas)."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    db_path = out_dir / "analysis_test-run.db"
    init_analysis_db(db_path).close()
    log_command(
        db_path, command_name="normalize_tic",
        arguments={"mode": "tic", "reference_mz": 100.0}, run_id="test-run",
    )

    raw_db_path = tmp_path / "s1.db"
    _build_calibration_raw_db(raw_db_path, grid_n=8)
    register_sample(db_path, name="s1", raw_db_path=str(raw_db_path))
    _write_targeted_h5ad(out_dir / "s1.h5ad", grid_n=8)
    append_injected_features(db_path, [SimpleNamespace(mz=200.0, members_json="{}")])

    run_dict = {
        "id": "test-run",
        "start_date": "2026-01-01 12:00:00",
        "config": {
            "io": {"out_dir": str(out_dir)},
            "analysis": {"db_name": db_path.name},
            "analysis_type": "targeted",
        },
    }
    project_model = ProjectModel(project, str(tmp_path))
    return AnalysisModel(project_model, "test-run", run_dict)


def test_drawing_multiple_standards_before_answering_queues_and_asks_for_each(
    analysis_view, wide_calibration_analysis_model, find_visual_child, qtbot
):
    # Real reported bug: drawing several standard ROIs back to back (in one
    # RoiDesignWindow session, a completely normal way to use it — the
    # window doesn't block further drawing while the amount popup is open
    # in the *other* top-level window) only ever asked for one amount; the
    # rest silently lost their ROI name/sample as each new `roiSaved`
    # overwrote the pending target before the previous one was answered.
    # Fixed with a FIFO queue (pendingStandardsQueue) — this proves every
    # drawn ROI eventually gets its own popup, not just the last one.
    view = analysis_view(wide_calibration_analysis_model)
    root = view.rootObject()
    section = _wait_for_calibration_section(root, find_visual_child, qtbot)

    pixel_size_field = find_visual_child(section, "pixelSizeField")
    pixel_size_field.setProperty("text", "0.1")
    qtbot.wait(30)

    add_standard_button = find_visual_child(section, "addStandardButton")
    _click(view, add_standard_button, qtbot)
    qtbot.wait(100)

    window = root.findChild(QObject, "standardRoiDesignWindow")
    qtbot.waitUntil(lambda: window.property("visible") is True, timeout=2000)
    _open_roi_windows.append(window)
    canvas = window.findChild(QQuickItem, "roiDrawingCanvas")

    # Three non-overlapping 2x2 blocks, well clear of the 8x8 grid's own
    # edge, drawn one after another with NO pause to answer any popup in
    # between.
    _start_drawing(window, "std1", qtbot)
    _tap_grid_point(window, canvas, 0, 0, qtbot)
    _tap_grid_point(window, canvas, 2, 0, qtbot)
    _tap_grid_point(window, canvas, 2, 2, qtbot)
    _tap_grid_point(window, canvas, 0, 2, qtbot)
    _tap_grid_point(window, canvas, 0, 0, qtbot)
    qtbot.wait(50)

    _start_drawing(window, "std2", qtbot)
    _tap_grid_point(window, canvas, 3, 0, qtbot)
    _tap_grid_point(window, canvas, 5, 0, qtbot)
    _tap_grid_point(window, canvas, 5, 2, qtbot)
    _tap_grid_point(window, canvas, 3, 2, qtbot)
    _tap_grid_point(window, canvas, 3, 0, qtbot)
    qtbot.wait(50)

    _start_drawing(window, "std3", qtbot)
    _tap_grid_point(window, canvas, 0, 3, qtbot)
    _tap_grid_point(window, canvas, 2, 3, qtbot)
    _tap_grid_point(window, canvas, 2, 5, qtbot)
    _tap_grid_point(window, canvas, 0, 5, qtbot)
    _tap_grid_point(window, canvas, 0, 3, qtbot)
    qtbot.wait(150)

    # Only the first popup should be open — the other two are queued, not
    # lost and not stacked as separate open dialogs.
    dialog = root.findChild(QObject, "standardConcentrationDialog")
    qtbot.waitUntil(lambda: dialog.property("visible") is True, timeout=2000)
    assert section.property("pendingStandardRoi") == "std1"
    assert len(_as_list(section.property("pendingStandardsQueue"))) == 2

    amount_field = dialog.findChild(QQuickItem, "standardAmountField")
    save_button = dialog.findChild(QQuickItem, "saveStandardButton")

    for expected_roi, amount in (("std1", "10.0"), ("std2", "20.0"), ("std3", "30.0")):
        qtbot.waitUntil(lambda: dialog.property("visible") is True, timeout=2000)
        assert section.property("pendingStandardRoi") == expected_roi
        amount_field.setProperty("text", amount)
        _click(view, save_button, qtbot)
        qtbot.wait(100)

    assert dialog.property("visible") is False
    rows = _as_list(section.property("standardsRows"))
    assert {r["roi_name"]: r["amount"] for r in rows} == {
        "std1": pytest.approx(10.0), "std2": pytest.approx(20.0), "std3": pytest.approx(30.0),
    }


def test_fit_curve_shows_stats_and_plot_after_two_standards(
    analysis_view, calibration_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(calibration_analysis_model)
    root = view.rootObject()
    section = _wait_for_calibration_section(root, find_visual_child, qtbot)

    # fitCurveButton requires hasAnalyte too — the analyte was already
    # baked into the run's .h5ad and registered as a feature (see
    # calibration_analysis_model), so the picker selects it with no click.
    qtbot.waitUntil(lambda: section.property("hasAnalyte") is True, timeout=2000)

    # Two standards added directly via the bridge (already covered
    # end-to-end, click-by-click, by test_full_add_standard_flow_... above)
    # — this test is about the fit/stats/plot wiring, not ROI drawing again.
    # addCalibrationPoint needs a real ROI mask on disk (its mean intensity
    # is read straight from the .h5ad), seeded directly here.
    db_path = calibration_analysis_model.analysisDbPath
    out_dir = Path(calibration_analysis_model.outDir)

    from msianalyzer.core.plotting.roi import save_roi_to_sample

    save_roi_to_sample(out_dir / "s1.h5ad", "std_a", "#ff0000", [[0, 0], [2, 0], [2, 2], [0, 2]])
    save_roi_to_sample(out_dir / "s1.h5ad", "std_b", "#00ff00", [[2, 2], [4, 2], [4, 4], [2, 4]])

    bridge_probe = AnalysisBridge()
    point_a = bridge_probe.addCalibrationPoint(
        db_path, "s1", "std_a", 200.0, 10.0, "pmol", 0.1, "mm"
    )
    point_b = bridge_probe.addCalibrationPoint(
        db_path, "s1", "std_b", 200.0, 4.0, "pmol", 0.1, "mm"
    )
    assert point_a["ok"] is True and point_b["ok"] is True
    section.refreshStandards()
    qtbot.wait(30)

    fit_button = find_visual_child(section, "fitCurveButton")
    assert fit_button.property("enabled") is True
    _click(view, fit_button, qtbot)
    qtbot.wait(100)

    stats_label = find_visual_child(section, "curveStatsLabel")
    assert stats_label.property("visible") is True
    assert "slope" in stats_label.property("text")

    plot = find_visual_child(section, "calibrationPlot")
    assert plot.property("visible") is True
    assert plot.property("hasData") is True
    title = find_visual_child(plot, "calibrationPlotTitle")
    assert title.property("text").startswith("y = ")
    assert "R²" in title.property("text")


def test_add_unknown_flow_draws_roi_and_records_result(
    analysis_view, calibration_analysis_model, find_visual_child, qtbot
):
    # Numerically, "an unknown drawn before any curve exists gets
    # interpolated_conc=None" and "fitting a curve re-interpolates every
    # existing unknown in place" are already fully covered, curve-fit-math
    # included, by test_analysis_bridge.py's own
    # test_fit_calibration_curve_reinterpolates_existing_unknowns (no QML
    # needed for that). This test is only about Step 3's "Add Sample to
    # Quantify" -> draw ROI -> table-updates UI wiring, mirroring
    # test_full_add_standard_flow_draws_roi_and_saves_amount's own scope
    # for Step 1 — kept deliberately simple (no pre-seeded curve) since a
    # heavier setup here (extra bridge/h5ad writes before drawing)
    # reproducibly crashed the offscreen test harness.
    view = analysis_view(calibration_analysis_model)
    root = view.rootObject()
    section = _wait_for_calibration_section(root, find_visual_child, qtbot)

    qtbot.waitUntil(lambda: section.property("hasAnalyte") is True, timeout=2000)

    pixel_size_field = find_visual_child(section, "pixelSizeField")
    pixel_size_field.setProperty("text", "0.1")
    qtbot.wait(30)

    add_unknown_button = find_visual_child(section, "addUnknownButton")
    assert add_unknown_button.property("enabled") is True
    _scroll_to(root, find_visual_child, add_unknown_button)
    _click(view, add_unknown_button, qtbot)
    qtbot.wait(100)

    window = root.findChild(QObject, "unknownRoiDesignWindow")
    assert window is not None
    qtbot.waitUntil(lambda: window.property("visible") is True, timeout=2000)
    _open_roi_windows.append(window)

    canvas = window.findChild(QQuickItem, "roiDrawingCanvas")
    _start_drawing(window, "unk1", qtbot)
    _tap_grid_point(window, canvas, 1, 1, qtbot)
    _tap_grid_point(window, canvas, 3, 1, qtbot)
    _tap_grid_point(window, canvas, 3, 3, qtbot)
    _tap_grid_point(window, canvas, 1, 3, qtbot)
    _tap_grid_point(window, canvas, 1, 1, qtbot)  # closes + saves -> roiSaved fires
    qtbot.wait(150)

    rows = _as_list(section.property("unknownsRows"))
    assert len(rows) == 1
    assert rows[0]["roi_name"] == "unk1"
    assert rows[0]["sample_name"] == "s1"
    assert rows[0]["n_pixels"] == 4
    assert rows[0]["interpolated_conc"] is None  # no curve fit yet

    row_label = find_visual_child(section, "unknownConcentration_unk1")
    assert row_label.property("text") == "no curve yet"


def test_analyte_picker_lists_and_switches_between_multiple_configured_analytes(
    analysis_view, two_analyte_calibration_model, find_visual_child, qtbot
):
    view = analysis_view(two_analyte_calibration_model)
    root = view.rootObject()
    section = _wait_for_calibration_section(root, find_visual_child, qtbot)

    combo = find_visual_child(section, "analyteCombo")
    labels = _as_list(combo.property("model"))
    assert len(labels) == 2
    assert "200.0000" in labels[0] and "300.0000" in labels[1]

    # Auto-selects the first one — not stuck on it, though: switching is a
    # real option, matching the config's own analyte_mzs list.
    assert section.property("analyteMz") == pytest.approx(200.0)

    combo.setProperty("currentIndex", 1)
    combo.activated.emit(1)
    qtbot.wait(30)

    assert section.property("analyteMz") == pytest.approx(300.0)
