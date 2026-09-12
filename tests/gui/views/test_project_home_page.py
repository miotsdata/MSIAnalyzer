from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QSignalSpy

from msianalyzer.gui.models.project import ProjectModel, _format_minute_precision


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
