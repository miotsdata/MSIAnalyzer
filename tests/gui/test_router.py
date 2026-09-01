from PySide6.QtQuick import QQuickItem
from PySide6.QtCore import QUrl, QObject, Qt
from pathlib import Path

from msianalyzer.gui.models.project import ProjectModel


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
