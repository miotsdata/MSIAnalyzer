# src/msianalyzer/gui/main.py
import importlib.metadata
import multiprocessing
import os
import sys

from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtCore import QLibraryInfo, QUrl
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWebEngineQuick import QtWebEngineQuick
from pathlib import Path
import logging

from msianalyzer.gui import resources_rc  # pylint: disable=unused-import  (registers qrc resources on import)
from msianalyzer.gui.utils.application import Application  # noqa: F401  (registers qrc resources on import)
from msianalyzer.gui.utils.config_schema import ConfigSchemaProvider
from msianalyzer.gui.utils import single_instance

from msianalyzer.core.utils import configure_logging
from msianalyzer.core.utils.logging_utils import default_debug_log_dir
from msianalyzer.gui.utils.freeze_watchdog import FreezeWatchdog


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

    # Qt's own answer, not a hardcoded PySide6/Qt/qml: the Windows wheel
    # keeps it at PySide6/qml, and a PyInstaller bundle somewhere else again.
    engine.addImportPath(QLibraryInfo.path(QLibraryInfo.LibraryPath.QmlImportsPath))

    context = engine.rootContext()
    context.setContextProperty("Router", application.router)
    context.setContextProperty("CoreBridge", application.core_bridge)
    context.setContextProperty("AnalysisBridge", application.analysis_bridge)
    engine.addImageProvider("heatmap", application.analysis_bridge.heatmap_provider)
    engine.addImageProvider("he_image", application.analysis_bridge.he_image_provider)
    engine.addImageProvider("he_overlay", application.analysis_bridge.he_overlay_provider)
    # Static schema metadata, not app state — see config_schema.py.
    config_schema_provider = ConfigSchemaProvider(engine)
    context.setContextProperty("ConfigSchema", config_schema_provider)
    # Help > About. Reads the installed distribution's own metadata
    # (pyproject.toml's `version`) rather than a separately hand-maintained
    # constant, so it can't drift out of sync with an actual release.
    try:
        app_version = importlib.metadata.version("MSIAnalyzer")
    except importlib.metadata.PackageNotFoundError:
        app_version = "dev"
    context.setContextProperty("AppVersion", app_version)

    errors = []
    engine.warnings.connect(lambda warnings: errors.extend(warnings))

    engine.load(QUrl("qrc:/Main/Main.qml"))
    if not engine.rootObjects():
        details = "\n".join(str(w) for w in errors) or "(no warnings captured)"
        raise RuntimeError(f"Failed to load Main.qml:\n{details}")

    return engine


def main() -> int:
    # A windowed (no-console) Windows .exe starts with sys.stdout/stderr
    # set to None, and any library that prints or draws a progress bar
    # then crashes. Everything worth keeping goes to the debug log file
    # anyway. Before freeze_support() so worker processes get it too.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    # Linux defaults to fork, and forking this process (Qt + logging
    # threads already running) can deadlock the workers. Spawn is what
    # Windows always uses.
    multiprocessing.set_start_method("spawn", force=True)
    # Frozen build: every ProcessPoolExecutor worker is a fresh launch of
    # this same executable. This turns that launch into a worker and
    # never returns, instead of opening another GUI. No-op otherwise.
    multiprocessing.freeze_support()
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

    # A second launch pings the first instance (see single_instance.py)
    # and exits here immediately, before building any UI — `server` must
    # stay alive for the whole `app.exec()` below, so it's a local, not
    # discarded.
    server = single_instance.acquire()
    if server is None:
        return 0

    apply_font_scale()

    # Add bindings
    application = Application()
    engine = build_engine(app, application)

    def _activate_existing_window() -> None:
        roots = engine.rootObjects()
        if not roots:
            return
        window = roots[0]
        window.show()
        window.raise_()
        window.requestActivate()

    single_instance.connect_activation(server, _activate_existing_window)

    # Any GUI-thread stall of 3 s or more (a "not responding" freeze) dumps
    # every thread's stack next to the debug log (ADR 50).
    watchdog = FreezeWatchdog(default_debug_log_dir())
    watchdog.start()

    exit_code = app.exec()
    watchdog.stop()

    # `app.exec()` returning means every window closed (or the user
    # confirmed quitting mid-run, see Main.qml's quitDuringRunDialog) —
    # the app is done as far as anyone watching the window is concerned.
    # But a `RunWorker`/`MirrorPlotWorker`/etc. QThread left running past
    # that point (an abandoned analysis, a slow export) is still a live
    # non-daemon thread, and CPython's normal interpreter shutdown blocks
    # on those before the process actually exits — which is exactly the
    # "closed the window, it kept running" symptom this is fixing.
    # `os._exit` skips that wait (and Python's own atexit/cleanup) and
    # kills the process immediately; any `ProcessPoolExecutor` workers a
    # stuck `Run` spawned die right behind it too, since they watch for
    # their parent going away. There is deliberately nothing left to flush
    # here (temp plot HTML files are disposable, not real output) that
    # would make a clean shutdown worth waiting for.
    os._exit(exit_code)


if __name__ == "__main__":
    sys.exit(main())
