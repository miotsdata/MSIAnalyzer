from PySide6.QtCore import Qt, QObject
from PySide6.QtQuick import QQuickItem


def test_run_button_present(engine):
    root = engine.rootObjects()[0]
    button = root.findChild(object, "loadProjectButton")  # objectName in QML
    assert button is not None
    assert button.property("visible") is True
    button = root.findChild(object, "createProjectButton")  # objectName in QML
    assert button is not None
    assert button.property("visible") is True


def test_load_project_button_opens_dialog(engine, qtbot):
    window = engine.rootObjects()[0]
    button = window.findChild(QQuickItem, "loadProjectButton")
    dialog = window.findChild(QObject, "projectFolderDialog")

    assert dialog.property("visible") is False

    button_center = button.mapToScene(button.boundingRect().center()).toPoint()
    qtbot.mouseClick(window, Qt.LeftButton, pos=button_center)

    qtbot.waitUntil(lambda: dialog.property("visible") is True, timeout=2000)
