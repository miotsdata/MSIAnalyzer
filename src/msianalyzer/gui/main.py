# src/msianalyzer/gui/main.py
import sys

import PySide6
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtCore import QUrl
from pathlib import Path

from msianalyzer.gui import resources_rc  # noqa: F401  (registers qrc resources on import)


def build_engine(app: QGuiApplication) -> QQmlApplicationEngine:
    """Construct and populate the QML engine. Reusable by tests."""
    engine = QQmlApplicationEngine()

    qml_import_path = Path(PySide6.__file__).parent / "Qt" / "qml"
    engine.addImportPath(str(qml_import_path))

    errors = []
    engine.warnings.connect(lambda warnings: errors.extend(warnings))

    engine.load(QUrl("qrc:/Main/Main.qml"))
    if not engine.rootObjects():
        details = "\n".join(str(w) for w in errors) or "(no warnings captured)"
        raise RuntimeError(f"Failed to load Main.qml:\n{details}")

    return engine


def main() -> int:
    app = QGuiApplication(sys.argv)
    engine = build_engine(app)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
