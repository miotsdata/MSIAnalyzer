from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QColor
from PySide6.QtQuick import QQuickItem, QQuickView
from PySide6.QtTest import QTest

_HOST = """
import QtQuick
import "qrc:/Style"

Item {
    width: 200; height: 60
    AppCheckBox { objectName: "box"; text: "Show" }
}
"""


def test_app_checkbox_box_is_white_with_tick_only_when_checked(application, tmp_path, qtbot):
    host = tmp_path / "host.qml"
    host.write_text(_HOST)
    view = QQuickView()
    view.setSource(QUrl.fromLocalFile(str(host)))
    view.show()
    QTest.qWaitForWindowExposed(view)

    box = view.rootObject().findChild(QObject, "box")
    indicator = view.rootObject().findChild(QQuickItem, "appCheckBoxIndicator")
    # White even in dark mode (Theme.isDark defaults to true).
    assert QColor(indicator.property("color")) == QColor("#ffffff")
    tick = indicator.childItems()[0]
    assert tick.property("visible") is False

    box.setProperty("checked", True)
    assert tick.property("visible") is True
    view.close()
