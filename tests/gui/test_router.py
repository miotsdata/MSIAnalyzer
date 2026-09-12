from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickItem
from PySide6.QtCore import QUrl, QObject, Qt
from pathlib import Path

from msianalyzer.gui.models.project import ProjectModel
from msianalyzer.core.project.project import Project, create_project_folder


def test_copy_to_clipboard_sets_system_clipboard_text(application):
    application.router.copyToClipboard("/tmp/proj/output_0")
    assert QGuiApplication.clipboard().text() == "/tmp/proj/output_0"


def test_to_local_path(application, tmp_path):
    qurl = QUrl.fromLocalFile(str(tmp_path))
    assert qurl.scheme() == "file"

    normalized = application.router.toLocalPath(qurl)

    assert normalized == str(tmp_path)
    assert Path(normalized).exists()
    assert qurl.toString() != tmp_path
    assert "file" in qurl.toString()


def test_show_project_home_requested_changes_home_page(
    application, project, engine, qtbot
):
    window = engine.rootObjects()[0]
    stack_view = window.findChild(QQuickItem, "stackView")
    assert stack_view is not None
    current_item = stack_view.property("currentItem")
    assert current_item.objectName() == "startPage"

    project_model = ProjectModel(project, "/tmp/proj", application)

    application.router.showProjectHomeRequested.emit(project_model)
    qtbot.wait(100)

    current_item = stack_view.property("currentItem")
    assert current_item.objectName() == "projectHomePage"

    label = current_item.findChild(QQuickItem, "projectNameLabel")
    assert label is not None
    assert label.property("text") == project.name


def test_create_new_project_requested_changes_page(application, engine, qtbot):

    window = engine.rootObjects()[0]
    stack_view = window.findChild(QQuickItem, "stackView")
    assert stack_view is not None
    current_item = stack_view.property("currentItem")
    assert current_item.objectName() == "startPage"

    application.router.createProjectPageRequested.emit()
    qtbot.wait(100)

    current_item = stack_view.property("currentItem")
    assert current_item.objectName() == "createProjectPage"


def test_invalidCreateProjectName_shows_message(application, engine, qtbot):
    window = engine.rootObjects()[0]
    stack_view = window.findChild(QQuickItem, "stackView")
    assert stack_view is not None

    message_dialog = window.findChild(QObject, "errorDialog")
    assert message_dialog is not None
    assert message_dialog.property("visible") is False
    assert message_dialog.property("modality") == Qt.ApplicationModal

    error_message = "Invalid name"
    application.core_bridge.invalidCreateProjectName.emit(error_message)

    qtbot.waitUntil(lambda: message_dialog.property("visible") is True, timeout=2000)
    assert message_dialog.property("text") == error_message
    message_dialog.setProperty("visible", False)


def test_project_folder_chosen_loads_real_project_and_shows_home_page(
    application, engine, qtbot, tmp_path
):
    proj_path = tmp_path / "myproj"
    create_project_folder(path=proj_path, name="myproj")

    window = engine.rootObjects()[0]
    stack_view = window.findChild(QQuickItem, "stackView")
    assert stack_view.property("currentItem").objectName() == "startPage"

    application.router.projectFolderChosen.emit(str(proj_path))
    qtbot.wait(100)

    current_item = stack_view.property("currentItem")
    assert current_item.objectName() == "projectHomePage"
    label = current_item.findChild(QQuickItem, "projectNameLabel")
    assert label.property("text") == "myproj"


def test_run_started_then_completed_shows_running_page_then_analysis_page(
    application, engine, qtbot, tmp_path
):
    proj_path = tmp_path / "myproj"
    create_project_folder(path=proj_path, name="myproj")
    application.project_folder = str(proj_path)
    application.core_bridge.load_project(str(proj_path))
    qtbot.wait(50)

    window = engine.rootObjects()[0]
    stack_view = window.findChild(QQuickItem, "stackView")
    current_item = stack_view.property("currentItem")
    assert current_item.objectName() == "projectHomePage"

    application.core_bridge.runStarted.emit("run-1")
    qtbot.wait(100)

    current_item = stack_view.property("currentItem")
    assert current_item.objectName() == "runningAnalysisPage"

    # The real run pipeline would have persisted "run-1" into the project
    # file by the time runCompleted fires; write it in directly so the
    # post-run reload (real disk I/O, same as production) finds it.
    project = Project.load_from_yaml(proj_path / ".msianalyzer.yml")
    project.runs["run-1"] = {
        "id": "run-1",
        "start_date": "2026-01-01 12:00:00",
        "config": {
            "io": {"out_dir": str(proj_path / "output")},
            "analysis": {"db_name": None},
        },
    }
    project.export(proj_path / ".msianalyzer.yml")

    application.core_bridge.runCompleted.emit("run-1")
    qtbot.wait(100)

    current_item = stack_view.property("currentItem")
    assert current_item.objectName() == "analysisPage"


def test_analysis_selected_shows_analysis_page(application, engine, qtbot, tmp_path):
    proj_path = tmp_path / "myproj"
    create_project_folder(path=proj_path, name="myproj")
    application.project_folder = str(proj_path)
    application.core_bridge.load_project(str(proj_path))
    qtbot.wait(50)

    application.project.runs["run-1"] = {
        "id": "run-1",
        "start_date": "2026-01-01 12:00:00",
        "config": {
            "io": {"out_dir": str(proj_path / "output")},
            "analysis": {"db_name": None},
        },
    }
    # the ProjectModel snapshot predates this run; rebuild it, mirroring the
    # reload Application does after a run actually completes
    application.project_model = ProjectModel(application.project, application.project_folder)

    window = engine.rootObjects()[0]
    stack_view = window.findChild(QQuickItem, "stackView")

    application.router.analysisSelected.emit("run-1")
    qtbot.wait(100)

    current_item = stack_view.property("currentItem")
    assert current_item.objectName() == "analysisPage"


def test_invalidCreateProjectPath_shows_message(application, engine, qtbot):
    window = engine.rootObjects()[0]
    stack_view = window.findChild(QQuickItem, "stackView")
    assert stack_view is not None

    message_dialog = window.findChild(QObject, "errorDialog")
    assert message_dialog is not None
    assert message_dialog.property("visible") is False

    error_message = "Invalid path"
    application.core_bridge.invalidCreateProjectPath.emit(error_message)

    qtbot.waitUntil(lambda: message_dialog.property("visible") is True, timeout=2000)
    assert message_dialog.property("text") == error_message
    message_dialog.setProperty("visible", False)
