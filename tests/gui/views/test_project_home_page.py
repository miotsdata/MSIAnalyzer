from PySide6.QtCore import Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QSignalSpy

from msianalyzer.gui.models.project import ProjectModel


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
    model = ProjectModel(project_with_runs, "/tmp/proj")
    view = project_home_view(model)
    root = view.rootObject()

    run_id, run = next(iter(project_with_runs.runs.items()))
    row = find_visual_child(root, "runRow_" + run_id)
    assert row is not None

    date_text = row.findChild(QQuickItem, "runRowDate")
    out_dir_text = row.findChild(QQuickItem, "runRowOutDir")
    config_text = row.findChild(QQuickItem, "runRowConfigPath")

    assert date_text.property("text") == run["start_date"]
    assert run["config"]["io"]["out_dir"] in out_dir_text.property("text")
    assert run["config_path"] in config_text.property("text")


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
