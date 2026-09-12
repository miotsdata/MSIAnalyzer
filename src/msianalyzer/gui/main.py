# src/msianalyzer/gui/main.py
import os
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


def apply_font_scale(scale: float = 0.9) -> None:
    """Scales the application's default font size ("slightly reduce the
    size of all the text (not in plot, but of the gui)").

    Scales whatever the platform's own default font size already is,
    rather than hardcoding a pixel/point size that would look wrong on a
    system with a different baseline. `QGuiApplication.setFont` is a
    static call — usable (and buffered by Qt) before a `QGuiApplication`
    instance exists, but must still run before any QML `Item` is actually
    constructed, so every Text/Control picks it up as their default.

    The default font isn't always point-sized — `pointSizeF()` returns
    `-1` (a sentinel, not a real size) when the platform's default is
    pixel-sized instead, and blindly scaling that gives `setPointSizeF` a
    negative value (a no-op, with a Qt warning) rather than the intended
    smaller font. Scale whichever of the two is actually set.
    """
    font = QGuiApplication.font()
    if font.pointSizeF() > 0:
        font.setPointSizeF(font.pointSizeF() * scale)
    else:
        font.setPixelSize(max(1, round(font.pixelSize() * scale)))
    QGuiApplication.setFont(font)


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
    # it. Tried Material next: readable, but its native-FileDialog
    # combination froze the app on the same session (root cause not
    # pinned down). Fusion is the one that's actually confirmed working
    # end-to-end on a real session — readable AND no dialog freeze.
    # Overridable (MSIANALYZER_QT_STYLE=Basic, Material, ...) for the same
    # kind of style bisection that found this, without a separate build.
    QQuickStyle.setStyle(os.environ.get("MSIANALYZER_QT_STYLE", "Fusion"))
    # Must run before the QGuiApplication is constructed — used by
    # WebEngineView (Annotations mirror plots, MS1 spectra).
    QtWebEngineQuick.initialize()
    app = QGuiApplication(sys.argv)
    apply_font_scale()

    # Add bindings
    application = Application()
    engine = build_engine(app, application)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
