from PySide6.QtCore import QUrl, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QSignalSpy
import pytest


def test_create_btn_disabled_by_default(create_project_view):
    btn = create_project_view.rootObject().findChild(QQuickItem, "createProjectButton")
    assert btn is not None
    assert btn.property("enabled") is False


def test_create_button_enabled_only_when_name_and_path_present(create_project_view):
    root = create_project_view.rootObject()
    name_input = root.findChild(QQuickItem, "createProjectNameInput")
    path_input = root.findChild(QQuickItem, "createProjectPathInput")
    button = root.findChild(QQuickItem, "createProjectButton")

    assert button.property("enabled") is False

    name_input.setProperty("text", "My Project")
    assert button.property("enabled") is False  # path still missing

    path_input.setProperty("text", "/home/user/projects/my_project")
    assert button.property("enabled") is True

    name_input.setProperty("text", "")
    assert button.property("enabled") is False  # name cleared again


def test_create_project_button_emits_router_signal(
    create_project_view, application, qtbot
):
    root = create_project_view.rootObject()

    button = root.findChild(QQuickItem, "createProjectButton")
    name_input = root.findChild(QQuickItem, "createProjectNameInput")
    path_input = root.findChild(QQuickItem, "createProjectPathInput")

    spy = QSignalSpy(application.router.createProjectRequested)

    name_input.setProperty("text", "My Project")
    path_input.setProperty("text", "/home/user/projects/my_project")
    button_center = button.mapToScene(button.boundingRect().center()).toPoint()

    qtbot.mouseClick(create_project_view, Qt.LeftButton, pos=button_center)

    qtbot.waitUntil(lambda: spy.count() == 1, timeout=2000)

    emitted_name, emitted_path = spy.at(0)
    assert emitted_name == "My Project"
    assert emitted_path == "/home/user/projects/my_project"
