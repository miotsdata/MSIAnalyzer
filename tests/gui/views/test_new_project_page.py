from PySide6.QtQuick import QQuickView
from PySide6.QtCore import QUrl, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QSignalSpy
import pytest


@pytest.fixture
def project_home_view(application):
    view = QQuickView()
    view.engine().rootContext().setContextProperty("Router", application.router)
    view.engine().rootContext().setContextProperty(
        "CoreBridge", application.core_bridge
    )

    view.setSource(QUrl("qrc:/Views/CreateProjectPage.qml"))
    view.show()

    root = view.rootObject()

    yield view

    view.close()


def test_create_btn_disabled_by_default(project_home_view):
    btn = project_home_view.rootObject().findChild(QQuickItem, "createProjectButton")
    assert btn is not None
    assert btn.property("enabled") is False


def test_create_button_enabled_only_when_name_and_path_present(project_home_view):
    root = project_home_view.rootObject()
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
    project_home_view, application, qtbot
):
    root = project_home_view.rootObject()

    button = root.findChild(QQuickItem, "createProjectButton")
    name_input = root.findChild(QQuickItem, "createProjectNameInput")
    path_input = root.findChild(QQuickItem, "createProjectPathInput")

    spy = QSignalSpy(application.router.createProjectRequested)

    name_input.setProperty("text", "My Project")
    path_input.setProperty("text", "/home/user/projects/my_project")
    button_center = button.mapToScene(button.boundingRect().center()).toPoint()

    qtbot.mouseClick(project_home_view, Qt.LeftButton, pos=button_center)

    qtbot.waitUntil(lambda: spy.count() == 1, timeout=2000)

    emitted_name, emitted_path = spy.at(0)
    assert emitted_name == "My Project"
    assert emitted_path == "/home/user/projects/my_project"
