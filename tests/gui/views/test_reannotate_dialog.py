"""Analyses > Re-annotate… (ADR 63): edit the MS2 settings, re-run the MS2
stages on the background worker, keep and restore every annotation."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Q_ARG, QMetaObject, QUrl
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest

from msianalyzer.gui.utils.config_schema import ConfigSchemaProvider

_HOST = """
import QtQuick
import "qrc:/Views"

Item {
    width: 1000; height: 900
    property var dialog: dlg
    ReannotateDialog { id: dlg }
}
"""


def _plain(value):
    return value.toVariant() if hasattr(value, "toVariant") else value


@pytest.fixture
def dialog(application, tmp_path, analysis_model):
    # The run must be in the project for its saved config to be updated.
    project_model = analysis_model.project
    project_model._project.runs[analysis_model.runId] = analysis_model._run_dict
    schema = ConfigSchemaProvider()
    host = tmp_path / "host.qml"
    host.write_text(_HOST)
    view = QQuickView()
    ctx = view.engine().rootContext()
    ctx.setContextProperty("AnalysisBridge", application.analysis_bridge)
    ctx.setContextProperty("ConfigSchema", schema)
    ctx.setContextProperty("Router", application.router)
    view.setSource(QUrl.fromLocalFile(str(host)))
    view.show()
    QTest.qWaitForWindowExposed(view)
    dlg = view.rootObject().property("dialog")
    QMetaObject.invokeMethod(dlg, "openFor", Q_ARG("QVariant", analysis_model))
    yield dlg
    view.close()


def test_reannotate_with_edited_settings_then_restore(
    dialog, application, analysis_model, find_visual_child, qtbot
):
    bridge = application.analysis_bridge

    def field(name):
        return find_visual_child(dialog.property("contentItem"), name)

    ppm = field("field_annotate_candidate_ppm")
    assert ppm is not None and float(ppm.property("text")) == 10.0
    assert _plain(dialog.property("history")) == []

    ppm.setProperty("text", "7")
    with qtbot.waitSignal(bridge.annotationTaskFinished, timeout=30000):
        QMetaObject.invokeMethod(dialog, "reannotate")

    history = _plain(dialog.property("history"))
    assert [e["kind"] for e in history] == ["pipeline", "reannotate"]
    assert _plain(dialog.property("settings"))["annotate"]["candidate_ppm"] == 7.0
    assert not dialog.property("statusError"), dialog.property("statusText")
    # The MS2 cleaning tab lists the new annotation's shared fragments.
    assert _plain(dialog.property("ms2Background"))["ran"] is True
    saved = analysis_model.project._project.runs[analysis_model.runId]["config"]
    assert saved["annotate"]["candidate_ppm"] == 7.0

    with qtbot.waitSignal(bridge.annotationTaskFinished, timeout=30000):
        QMetaObject.invokeMethod(dialog, "restore", Q_ARG("QVariant", history[0]["id"]))

    history = _plain(dialog.property("history"))
    assert [e["kind"] for e in history] == ["pipeline", "reannotate", "restore"]
    # The restored state's settings are back in the form.
    assert float(field("field_annotate_candidate_ppm").property("text")) == 10.0
