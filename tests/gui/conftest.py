# tests/gui/conftest.py
import subprocess
import pytest

# Must run before any QApplication/QGuiApplication is constructed — pytest-qt's
# `qapp` fixture builds one lazily, but not before this module is imported, so
# module-import time is early enough. Needed by WebEngineView-based views.
from PySide6.QtWebEngineQuick import QtWebEngineQuick

QtWebEngineQuick.initialize()

# Same style as production (see gui/main.py) — before any QtQuick.Controls
# import loads, so before qapp too. Keeps control geometry (and therefore
# layout-dependent tests, e.g. the new-analysis tab Flow's row wrapping) in
# sync with what a real session actually renders.
from PySide6.QtQuickControls2 import QQuickStyle

QQuickStyle.setStyle("Fusion")

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
def _apply_test_font_scale(qapp):
    # Same default-font scale as production (see gui/main.py's
    # apply_font_scale) — for the same reason `QQuickStyle.setStyle`
    # above is mirrored: keeps text-dependent geometry (wrapping,
    # implicit sizes) in sync with what a real session renders.
    #
    # Must depend on `qapp` (not run at module-import time, unlike the
    # style/WebEngine setup above): `QGuiApplication.font()` returns a
    # bogus font with neither a valid point nor pixel size before any
    # QGuiApplication instance exists — scaling *that* and setting it as
    # the buffered default poisoned the real one once `qapp` later
    # constructed the actual application, leaving every test's QML
    # rendered at an almost-invisible 1px default font (confirmed by
    # reproducing it: `QGuiApplication.font().pixelSize()` read back as
    # `1` after construction). `QQuickStyle.setStyle` has no such
    # lazy-resolution problem — it's a plain string preference, not
    # something the platform integration computes only once a real
    # application exists.
    from msianalyzer.gui.main import apply_font_scale

    apply_font_scale()


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

    Prefer `root.findChild(QQuickItem, name)` for anything NOT inside a
    Repeater (it's the same standard Qt lookup used elsewhere in these
    tests) — this function has a real, confirmed fragility: searching from
    a large subtree (e.g. the whole page `root`) immediately after several
    Repeaters populate at once (many delegates created in one property
    change) has reproducibly segfaulted inside PySide6's own
    `getWrapperForQObject` (confirmed via gdb — a wrapper-lifecycle bug in
    PySide6 itself, not a logic error in the QML being searched). Passing
    the smallest sensible starting item (e.g. the specific panel containing
    what you're looking for, not `root`) avoids it by cutting down how much
    of the tree gets walked. See `test_ms1_spectrum_point_click_updates_detail_panel`.
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
def annotated_analysis_model(analysis_model):
    """`analysis_model`, seeded with one annotated feature (id=7, mz
    123.4567, scan 42, sample 's1', library 'my_library', compound
    'Caffeine')."""
    import sqlite3

    import numpy as np

    from msianalyzer.core.parser.mzml_parser import array_to_blob

    with sqlite3.connect(analysis_model.analysisDbPath) as con:
        con.execute(
            "INSERT INTO samples (sample_id, name, raw_db_path, polarity) "
            "VALUES (1, 's1', 'a.db', 'positive')"
        )
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) "
            "VALUES (7, 123.4567, '{\"s1\": 0}')"
        )
        con.execute(
            "INSERT INTO annotation_libraries (id, path, name) "
            "VALUES (1, 'lib.db', 'my_library')"
        )
        con.execute(
            "INSERT INTO ms2_associations "
            "(sample_id, scan_id, match_key, precursor_mz, "
            "rt, n_peaks, polarity) "
            "VALUES (1, 42, 'k1', 150.1234, 12.3, 5, 'positive')"
        )
        blob = array_to_blob(np.array([100.0, 200.0], dtype=np.float32))
        con.execute(
            "INSERT INTO ms2_annotations "
            "(id, feature_id, sample_id, scan_id, library_id, "
            "library_spectrum_id, compound_name, compound_formula, inchikey, "
            "score, dot_product_score, lib_coverage, emp_coverage, "
            "coverage_score, n_matched_peaks, n_lib_peaks, n_emp_peaks_raw, "
            "n_emp_peaks_filtered, emp_raw_mz, emp_raw_intensity, "
            "lib_raw_mz, lib_raw_intensity, rank_ms2, rank_feature) "
            "VALUES (1, 7, 1, 42, 1, 9, 'Caffeine', 'C8H10N4O2', "
            "'RYYVLZVUVIJVGH-UHFFFAOYSA-N', 0.87, 0.9, 0.8, 0.75, 0.77, "
            "2, 2, 10, 3, ?, ?, ?, ?, 1, 1)",
            (blob, blob, blob, blob),
        )
        con.commit()
    return analysis_model


@pytest.fixture
def visual_analysis_model(analysis_model):
    """`analysis_model`, seeded with 2 samples ('s1', 's2') and 1 feature
    (mz=150.0, unannotated) — for the Visual Inspection section's grid."""
    import sqlite3

    with sqlite3.connect(analysis_model.analysisDbPath) as con:
        con.execute(
            "INSERT INTO samples (sample_id, name, raw_db_path, polarity) "
            "VALUES (1, 's1', 'a.db', 'positive')"
        )
        con.execute(
            "INSERT INTO samples (sample_id, name, raw_db_path, polarity) "
            "VALUES (2, 's2', 'b.db', 'positive')"
        )
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) "
            "VALUES (1, 150.0, '{\"s1\": 0, \"s2\": 0}')"
        )
        con.commit()
    return analysis_model


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
        view.engine().addImageProvider(
            "heatmap", application.analysis_bridge.heatmap_provider
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
