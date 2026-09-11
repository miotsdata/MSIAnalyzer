# tests/gui/conftest.py
import subprocess
import pytest
from msianalyzer.core.project.project import Project
from msianalyzer.gui.main import build_engine
from msianalyzer.gui.utils import Router
from msianalyzer.gui.utils.application import Application
from msianalyzer.gui.utils.config_schema import ConfigSchemaProvider

from PySide6.QtQuick import QQuickView
from PySide6.QtCore import QUrl
from PySide6.QtTest import QTest

import os

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
QRC_PATH = PROJECT_ROOT / "src/msianalyzer/gui/qml/resources.qrc"
RC_PATH = PROJECT_ROOT / "src/msianalyzer/gui/resources_rc.py"


@pytest.fixture(scope="session", autouse=True)
def compile_qml_resources():
    rc_path = "src/msianalyzer/gui/resources_rc.py"
    if os.path.exists(rc_path):
        os.remove(rc_path)
    result = subprocess.run(
        [
            "pyside6-rcc",
            str(QRC_PATH),
            "-o",
            str(RC_PATH),
        ],
        capture_output=True,
        text=True,
    )


@pytest.fixture
def router():
    return Router()


@pytest.fixture
def engine(qapp, application):
    eng = build_engine(qapp, application)
    yield eng
    eng.deleteLater()


@pytest.fixture
def application():
    return Application()


@pytest.fixture
def project():
    p = Project(name="test_proj")
    return p


@pytest.fixture
def project_with_runs():
    """A project with two `Run.to_dict()`-shaped entries in `.runs`."""
    p = Project(name="test_proj_with_runs")
    for i in range(2):
        run_id = f"run-{i}"
        p.runs[run_id] = {
            "id": run_id,
            "start_date": f"2026-01-0{i + 1} 12:00:00",
            "end_date": f"2026-01-0{i + 1} 12:30:00",
            "status": "COMPLETED",
            "config": {"io": {"out_dir": f"/tmp/proj/output_{i}"}},
            "config_path": f"/tmp/proj/configs/run_{i}.yaml",
        }
    return p


def _find_visual_child(item, object_name):
    """Depth-first search over `QQuickItem.childItems()`.

    Repeater/ListView delegates are visual children of their view but are
    *not* reachable via `QObject.findChild` (their QObject parent is the
    QQmlDelegateModel machinery, not the visual parent) — use this instead
    for anything created by a `Repeater`.
    """
    for child in item.childItems():
        if child.objectName() == object_name:
            return child
        found = _find_visual_child(child, object_name)
        if found is not None:
            return found
    return None


@pytest.fixture
def find_visual_child():
    return _find_visual_child


@pytest.fixture
def project_home_view(application):
    """Factory: build a standalone ProjectHomePage view for a given project model."""
    views = []

    def _make(project_model):
        view = QQuickView()
        view.engine().rootContext().setContextProperty("Router", application.router)
        view.engine().rootContext().setContextProperty(
            "CoreBridge", application.core_bridge
        )
        view.setInitialProperties({"project": project_model})
        view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        view.setSource(QUrl("qrc:/Views/ProjectHomePage.qml"))
        view.resize(800, 600)
        view.show()
        QTest.qWaitForWindowExposed(view)
        QTest.qWait(50)  # let Repeater-created delegates finish incubating
        views.append(view)
        return view

    yield _make

    for view in views:
        view.close()


@pytest.fixture
def new_analysis_view(application):
    """Factory: build a standalone NewAnalysisPage view for a given project model."""
    views = []
    schema_provider = ConfigSchemaProvider()

    def _make(project_model):
        view = QQuickView()
        view.engine().rootContext().setContextProperty("Router", application.router)
        view.engine().rootContext().setContextProperty(
            "CoreBridge", application.core_bridge
        )
        view.engine().rootContext().setContextProperty("ConfigSchema", schema_provider)
        view.setInitialProperties({"project": project_model})
        view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        view.setSource(QUrl("qrc:/Views/NewAnalysisPage.qml"))
        view.resize(900, 700)
        view.show()
        QTest.qWaitForWindowExposed(view)
        QTest.qWait(50)
        views.append(view)
        return view

    yield _make

    for view in views:
        view.close()


@pytest.fixture
def running_analysis_view(application):
    """Factory: build a standalone RunningAnalysisPage view."""
    views = []

    def _make(project_model, run_id="run-1"):
        view = QQuickView()
        view.engine().rootContext().setContextProperty("Router", application.router)
        view.engine().rootContext().setContextProperty(
            "CoreBridge", application.core_bridge
        )
        view.setInitialProperties({"project": project_model, "runId": run_id})
        view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        view.setSource(QUrl("qrc:/Views/RunningAnalysisPage.qml"))
        view.resize(800, 600)
        view.show()
        QTest.qWaitForWindowExposed(view)
        QTest.qWait(50)
        views.append(view)
        return view

    yield _make

    for view in views:
        view.close()


@pytest.fixture
def analysis_model(tmp_path, project):
    """An `AnalysisModel` pointing at a real, empty analysis DB."""
    from msianalyzer.core.analysis_db import init_analysis_db
    from msianalyzer.gui.models.analysis import AnalysisModel
    from msianalyzer.gui.models.project import ProjectModel

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    db_path = out_dir / "analysis_test-run.db"
    init_analysis_db(db_path).close()

    run_dict = {
        "id": "test-run",
        "start_date": "2026-01-01 12:00:00",
        "config": {
            "io": {"out_dir": str(out_dir)},
            "analysis": {"db_name": db_path.name},
        },
    }
    project_model = ProjectModel(project, str(tmp_path))
    return AnalysisModel(project_model, "test-run", run_dict)


@pytest.fixture
def analysis_view(application):
    """Factory: build a standalone AnalysisPage view for a given AnalysisModel."""
    views = []

    def _make(analysis_model):
        view = QQuickView()
        view.engine().rootContext().setContextProperty("Router", application.router)
        view.engine().rootContext().setContextProperty(
            "CoreBridge", application.core_bridge
        )
        view.engine().rootContext().setContextProperty(
            "AnalysisBridge", application.analysis_bridge
        )
        view.setInitialProperties({"analysis": analysis_model})
        view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        view.setSource(QUrl("qrc:/Views/AnalysisPage.qml"))
        view.resize(900, 700)
        view.show()
        QTest.qWaitForWindowExposed(view)
        QTest.qWait(50)
        views.append(view)
        return view

    yield _make

    for view in views:
        view.close()


@pytest.fixture
def create_project_view(application):
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
