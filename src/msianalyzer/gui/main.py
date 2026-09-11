# src/msianalyzer/gui/main.py
import sys

import PySide6
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtCore import QUrl
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWebEngineQuick import QtWebEngineQuick
from pathlib import Path
import logging

from msianalyzer.gui import resources_rc
from msianalyzer.gui.utils.application import Application  # noqa: F401  (registers qrc resources on import)
from msianalyzer.gui.utils.config_schema import ConfigSchemaProvider

from msianalyzer.core.utils import configure_logging


def build_engine(
    app: QGuiApplication, application: Application
) -> QQmlApplicationEngine:
    """Construct and populate the QML engine. Reusable by tests."""
    engine = QQmlApplicationEngine()

    qml_import_path = Path(PySide6.__file__).parent / "Qt" / "qml"
    engine.addImportPath(str(qml_import_path))

    context = engine.rootContext()
    context.setContextProperty("Router", application.router)
    context.setContextProperty("CoreBridge", application.core_bridge)
    context.setContextProperty("AnalysisBridge", application.analysis_bridge)
    engine.addImageProvider("heatmap", application.analysis_bridge.heatmap_provider)
    # Static schema metadata, not app state — see config_schema.py.
    config_schema_provider = ConfigSchemaProvider(engine)
    context.setContextProperty("ConfigSchema", config_schema_provider)

    errors = []
    engine.warnings.connect(lambda warnings: errors.extend(warnings))

    engine.load(QUrl("qrc:/Main/Main.qml"))
    if not engine.rootObjects():
        details = "\n".join(str(w) for w in errors) or "(no warnings captured)"
        raise RuntimeError(f"Failed to load Main.qml:\n{details}")

    return engine


def main() -> int:
    configure_logging(level=logging.DEBUG)
    # Must run before any QtQuick.Controls-importing QML loads — the
    # platform default style pulled in a dark theme on at least one real
    # session (GNOME/GTK integration), with some text unreadable against
    # it. Material is a light theme by default regardless of the host
    # desktop's own theme (see Main.qml's explicit Material.theme too).
    QQuickStyle.setStyle("Material")
    # Must run before the QGuiApplication is constructed — used by
    # WebEngineView (Annotations mirror plots, MS1 spectra).
    QtWebEngineQuick.initialize()
    app = QGuiApplication(sys.argv)

    # Add bindings
    application = Application()
    engine = build_engine(app, application)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
