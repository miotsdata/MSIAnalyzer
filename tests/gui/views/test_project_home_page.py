from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QSignalSpy

from msianalyzer.gui.models.project import ProjectModel, _format_minute_precision


def _project_with_run_on_disk(tmp_path, run_id="run-0"):
    """A `ProjectModel` whose one run's output dir + config file are real
    files on disk, for exercising the "Delete analysis" flow end to end."""
    from msianalyzer.core.project.project import Project

    out_dir = tmp_path / "output" / run_id
    out_dir.mkdir(parents=True)
    config_path = tmp_path / "configs" / f"run_{run_id}.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("io: {}")

    p = Project(name="test_proj")
    p.runs[run_id] = {
        "id": run_id,
        "start_date": "2026-01-01 12:00:00",
        "config": {"io": {"out_dir": str(out_dir)}},
        "config_path": str(config_path),
    }
    p.export(tmp_path / ".msianalyzer.yml")
    return ProjectModel(p, str(tmp_path)), out_dir, config_path


def test_empty_state_shows_only_new_analysis_button(project_home_view, project):
    model = ProjectModel(project, "/tmp/proj")
    view = project_home_view(model)
    root = view.rootObject()

    button = root.findChild(QQuickItem, "newAnalysisButton")
    empty_label = root.findChild(QQuickItem, "emptyStateLabel")
    runs_list = root.findChild(QQuickItem, "runsListView")

    assert button is not None
    assert button.property("visible") is True
    assert empty_label.property("visible") is True
    assert runs_list.property("visible") is False


def test_populated_state_shows_run_rows(project_home_view, project_with_runs):
    model = ProjectModel(project_with_runs, "/tmp/proj")
    view = project_home_view(model)
    root = view.rootObject()

    empty_label = root.findChild(QQuickItem, "emptyStateLabel")
    runs_list = root.findChild(QQuickItem, "runsListView")
    runs_repeater = root.findChild(QQuickItem, "runsRepeater")
    button = root.findChild(QQuickItem, "newAnalysisButton")

    assert empty_label.property("visible") is False
    assert runs_list.property("visible") is True
    assert runs_repeater.property("count") == len(project_with_runs.runs)
    assert button.property("visible") is True


def test_project_name_label_is_bold_not_hardcoded_blue(project_home_view, project):
    # Regression check for the "blue" leftover from early scaffolding
    # (reported hard to read against the new dark palette, 2026-09-14) —
    # the title should now be a bold, theme-following heading instead of
    # an explicit color.
    from PySide6.QtGui import QColor

    model = ProjectModel(project, "/tmp/proj")
    view = project_home_view(model)
    root = view.rootObject()

    label = root.findChild(QQuickItem, "projectNameLabel")
    assert label.property("font").bold() is True
    assert label.property("color") != QColor("blue")


def test_empty_state_label_uses_theme_muted_text_color(project_home_view, project):
    # Style/Theme.qml singleton (2026-09-14) — secondary/hint text should
    # read from Theme.mutedTextColor, not a hardcoded "gray" literal, so a
    # future palette change only needs one edit.
    from PySide6.QtGui import QColor

    model = ProjectModel(project, "/tmp/proj")
    view = project_home_view(model)
    root = view.rootObject()

    empty_label = root.findChild(QQuickItem, "emptyStateLabel")
    assert empty_label.property("color") == QColor("#808080")


def test_run_row_shows_date_out_dir_and_config_path(
    project_home_view, project_with_runs, find_visual_child
):
    # Reported: paths were absolute (cluttered, most runs didn't even show
    # a config path) and timestamps carried seconds nobody needs. Now:
    # relative to the project folder, and minute-precision dates.
    model = ProjectModel(project_with_runs, "/tmp/proj")
    view = project_home_view(model)
    root = view.rootObject()

    run_id, run = next(iter(project_with_runs.runs.items()))
    row = find_visual_child(root, "runRow_" + run_id)
    assert row is not None

    date_text = row.findChild(QQuickItem, "runRowDate")
    out_dir_text = row.findChild(QQuickItem, "runRowOutDir")
    config_text = row.findChild(QQuickItem, "runRowConfigPath")

    assert date_text.property("text") == _format_minute_precision(run["start_date"])
    expected_out_dir_rel = run["config"]["io"]["out_dir"].removeprefix("/tmp/proj/")
    expected_config_rel = run["config_path"].removeprefix("/tmp/proj/")
    assert out_dir_text.property("text") == "Output: " + expected_out_dir_rel
    assert config_text.property("text") == "Config: " + expected_config_rel


def test_run_row_context_menu_copies_absolute_paths(
    project_home_view, project_with_runs, find_visual_child
):
    # "if user wants the full path, it can right click and use copy path"
    model = ProjectModel(project_with_runs, "/tmp/proj")
    view = project_home_view(model)
    root = view.rootObject()

    run_id, run = next(iter(project_with_runs.runs.items()))
    row = find_visual_child(root, "runRow_" + run_id)

    copy_out_dir_item = row.findChild(object, "copyOutDirMenuItem_" + run_id)
    copy_config_item = row.findChild(object, "copyConfigPathMenuItem_" + run_id)
    assert copy_out_dir_item is not None
    assert copy_config_item is not None

    copy_out_dir_item.click()
    assert QGuiApplication.clipboard().text() == run["config"]["io"]["out_dir"]

    copy_config_item.click()
    assert QGuiApplication.clipboard().text() == run["config_path"]


def test_run_row_shows_relative_paths_not_absolute(
    project_home_view, project_with_runs, find_visual_child
):
    model = ProjectModel(project_with_runs, "/tmp/proj")
    view = project_home_view(model)
    root = view.rootObject()

    run_id, run = next(iter(project_with_runs.runs.items()))
    row = find_visual_child(root, "runRow_" + run_id)
    out_dir_text = row.findChild(QQuickItem, "runRowOutDir")

    assert run["config"]["io"]["out_dir"] not in out_dir_text.property("text")


def test_project_home_shows_mzml_and_xml_file_panels(
    project_home_view, project, find_visual_child, tmp_path
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "sample_b.mzML").write_bytes(b"")
    (data_dir / "sample_a.mzml").write_bytes(b"")
    (data_dir / "sample_a.xml").write_bytes(b"")
    (data_dir / "not_relevant.txt").write_bytes(b"")

    model = ProjectModel(project, str(tmp_path))
    view = project_home_view(model)
    root = view.rootObject()

    mzml_repeater = find_visual_child(root, "fileListPanelRepeater_mzml")
    xml_repeater = find_visual_child(root, "fileListPanelRepeater_xml")

    assert mzml_repeater.property("model") == ["sample_a.mzml", "sample_b.mzML"]
    assert xml_repeater.property("model") == ["sample_a.xml"]


def test_file_list_panels_show_empty_state_without_data_dir(
    project_home_view, project, find_visual_child
):
    model = ProjectModel(project, "/tmp/proj_without_data_dir")
    view = project_home_view(model)
    root = view.rootObject()

    mzml_empty = find_visual_child(root, "fileListPanelEmptyLabel_mzml")
    xml_empty = find_visual_child(root, "fileListPanelEmptyLabel_xml")

    assert mzml_empty.property("visible") is True
    assert xml_empty.property("visible") is True


def test_split_view_panels_have_a_visible_gap(
    project_home_view, project, find_visual_child
):
    # Reported: the two panels were flush against each other ("attached").
    model = ProjectModel(project, "/tmp/proj")
    view = project_home_view(model)
    root = view.rootObject()

    split_view = find_visual_child(root, "mainSplitView")
    analyses_column = find_visual_child(root, "analysesColumn")
    mzml_panel = find_visual_child(root, "mzmlFilesPanel")

    left_panel_right_edge = analyses_column.mapToItem(split_view, analyses_column.width(), 0).x()
    right_panel_left_edge = mzml_panel.mapToItem(split_view, 0, 0).x()

    assert right_panel_left_edge - left_panel_right_edge >= 6


def test_new_analysis_button_emits_router_signal(
    project_home_view, project, application, qtbot
):
    model = ProjectModel(project, "/tmp/proj")
    view = project_home_view(model)
    root = view.rootObject()
    button = root.findChild(QQuickItem, "newAnalysisButton")

    spy = QSignalSpy(application.router.newAnalysisPageRequested)
    button_center = button.mapToScene(button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=button_center)

    qtbot.waitUntil(lambda: spy.count() == 1, timeout=2000)
    assert spy.at(0)[0].name == model.name


def test_clicking_run_row_emits_analysis_selected(
    project_home_view, project_with_runs, application, qtbot, find_visual_child
):
    model = ProjectModel(project_with_runs, "/tmp/proj")
    view = project_home_view(model)
    root = view.rootObject()

    run_id = next(iter(project_with_runs.runs.keys()))
    row = find_visual_child(root, "runRow_" + run_id)
    assert row is not None

    spy = QSignalSpy(application.router.analysisSelected)
    row_center = row.mapToScene(row.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=row_center)

    qtbot.waitUntil(lambda: spy.count() == 1, timeout=2000)
    assert spy.at(0)[0] == run_id


def test_delete_run_menu_item_opens_confirmation_dialog(
    project_home_view, tmp_path, find_visual_child
):
    model, out_dir, config_path = _project_with_run_on_disk(tmp_path)
    view = project_home_view(model)
    root = view.rootObject()

    row = find_visual_child(root, "runRow_run-0")
    delete_item = row.findChild(object, "deleteRunMenuItem_run-0")
    assert delete_item is not None

    delete_item.click()

    dialog = row.findChild(object, "deleteRunDialog_run-0")
    assert dialog is not None
    assert dialog.property("visible") is True
    # nothing deleted yet — only confirming triggers the actual deletion
    assert out_dir.exists()
    assert config_path.exists()


def test_confirming_delete_run_dialog_removes_files_and_row(
    project_home_view, tmp_path, find_visual_child
):
    model, out_dir, config_path = _project_with_run_on_disk(tmp_path)
    view = project_home_view(model)
    root = view.rootObject()

    row = find_visual_child(root, "runRow_run-0")
    row.findChild(object, "deleteRunMenuItem_run-0").click()
    dialog = row.findChild(object, "deleteRunDialog_run-0")

    dialog.accepted.emit()

    assert not out_dir.exists()
    assert not config_path.exists()
    assert "run-0" not in model.runs
    runs_repeater = root.findChild(QQuickItem, "runsRepeater")
    assert runs_repeater.property("count") == 0


def test_run_row_shows_pointing_hand_cursor_on_hover(
    project_home_view, project_with_runs, find_visual_child
):
    # "the analyses cards in project home... should make the user
    # understand that they can be clickable"
    model = ProjectModel(project_with_runs, "/tmp/proj")
    view = project_home_view(model)
    root = view.rootObject()

    run_id = next(iter(project_with_runs.runs.keys()))
    mouse_area = find_visual_child(root, "runRowMouseArea_" + run_id)

    assert mouse_area is not None
    assert mouse_area.property("cursorShape") == Qt.PointingHandCursor
