from PySide6.QtQuick import QQuickItem
from PySide6.QtCore import QUrl
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
