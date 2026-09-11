from PySide6.QtQuick import QQuickItem
from PySide6.QtCore import QUrl, QObject, Qt
from pathlib import Path

from msianalyzer.gui.models.project import ProjectModel
from msianalyzer.core.project.project import create_project_folder


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

    project_model = ProjectModel(project, application)

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


def test_run_started_then_completed_shows_running_page_then_back_to_project_home(
    application, engine, qtbot, tmp_path
):
    proj_path = tmp_path / "myproj"
    create_project_folder(path=proj_path, name="myproj")
    application.project_folder = str(proj_path)
    application.core_bridge.load_project(str(proj_path / ".msianalyzer.yml"))
    qtbot.wait(50)

    window = engine.rootObjects()[0]
    stack_view = window.findChild(QQuickItem, "stackView")
    current_item = stack_view.property("currentItem")
    assert current_item.objectName() == "projectHomePage"

    application.core_bridge.runStarted.emit("run-1")
    qtbot.wait(100)

    current_item = stack_view.property("currentItem")
    assert current_item.objectName() == "runningAnalysisPage"

    application.core_bridge.runCompleted.emit("run-1")
    qtbot.wait(100)

    current_item = stack_view.property("currentItem")
    assert current_item.objectName() == "projectHomePage"


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
