"""Analyses > Match target list… (ADR 60): match a target list against a
finished analysis, review, apply, undo."""

from __future__ import annotations

import sqlite3

import pytest
from pyteomics import mass as pt_mass
from PySide6.QtCore import Q_ARG, QMetaObject, QUrl
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest

from msianalyzer.core.analysis_db import load_feature_list
from msianalyzer.core.annotation.target_list import adduct_by_label, adduct_mz
from msianalyzer.gui.utils.config_schema import ConfigSchemaProvider

_HOST = """
import QtQuick
import "qrc:/Views"

Item {
    width: 1200; height: 800
    property var dialog: dlg
    TargetListMatchDialog { id: dlg }
}
"""

GLUCOSE_MZ = adduct_mz(pt_mass.calculate_mass(formula="C6H12O6"), adduct_by_label("[M+H]+"))


def _as_list(value):
    return value.toVariant() if hasattr(value, "toVariant") else value


@pytest.fixture
def dialog(application, tmp_path, visual_analysis_model):
    with sqlite3.connect(visual_analysis_model.analysisDbPath) as con:
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (2, ?, '{}')",
            (GLUCOSE_MZ,),
        )
        con.commit()
    host = tmp_path / "host.qml"
    host.write_text(_HOST)
    schema = ConfigSchemaProvider()
    view = QQuickView()
    ctx = view.engine().rootContext()
    ctx.setContextProperty("Router", application.router)
    ctx.setContextProperty("AnalysisBridge", application.analysis_bridge)
    ctx.setContextProperty("ConfigSchema", schema)
    view.setSource(QUrl.fromLocalFile(str(host)))
    view.show()
    QTest.qWaitForWindowExposed(view)
    dlg = view.rootObject().property("dialog")
    QMetaObject.invokeMethod(dlg, "openFor", Q_ARG("QVariant", visual_analysis_model))
    targets = tmp_path / "targets.csv"
    targets.write_text(
        "name,formula,inchikey\nGlucose,C6H12O6,\nFructose,C6H12O6,\nCaffeine,C8H10N4O2,\n"
    )
    dlg.setProperty("files", [str(targets)])
    yield dlg
    view.close()
    del schema


def test_find_review_apply_and_undo(dialog, visual_analysis_model, application):
    db = visual_analysis_model.analysisDbPath
    # Pre-filled from this analysis' config (none here): positive, every adduct.
    assert dialog.property("polarity") == "positive"
    assert "[M+H]+" in _as_list(dialog.property("adducts"))

    QMetaObject.invokeMethod(dialog, "findMatches")
    rows = _as_list(dialog.property("rows"))
    names = {(r["name"], r["detected"]) for r in rows}
    assert ("Caffeine", False) in names
    glucose_rows = [i for i, r in enumerate(rows) if r["feature_id"] == 2]
    assert {rows[i]["name"] for i in glucose_rows} == {"Glucose", "Fructose"}
    # One suggested name per feature.
    flags = _as_list(dialog.property("useFlags"))
    assert sum(flags[i] for i in glucose_rows) == 1

    # Ticking the other one unticks the first: one target name per feature.
    other = next(i for i in glucose_rows if not flags[i])
    QMetaObject.invokeMethod(dialog, "setUse", Q_ARG("QVariant", other), Q_ARG("QVariant", True))
    flags = _as_list(dialog.property("useFlags"))
    assert [flags[i] for i in glucose_rows].count(True) == 1 and flags[other]
    chosen = rows[other]["name"]

    with_signal = []
    application.analysis_bridge.targetListMatchesChanged.connect(with_signal.append)
    QMetaObject.invokeMethod(dialog, "apply")
    assert with_signal == [db]
    names = dict(zip(load_feature_list(db)["feature_id"], load_feature_list(db)["compound_name"]))
    assert names[2] == chosen
    assert dialog.property("postRunCount") == 1
    # The table refreshed: the feature now shows its new name.
    rows = _as_list(dialog.property("rows"))
    assert {r["current_name"] for r in rows if r["feature_id"] == 2} == {chosen}

    assert application.analysis_bridge.removePostRunTargetList(db) == 1
    assert application.analysis_bridge.countPostRunTargetList(db) == 0


def test_bad_file_shows_error(dialog, tmp_path):
    dialog.setProperty("files", [str(tmp_path / "missing.csv")])
    QMetaObject.invokeMethod(dialog, "findMatches")
    assert dialog.property("statusError") is True
    assert _as_list(dialog.property("rows")) == []


def test_switching_polarity_resets_adducts(dialog):
    QMetaObject.invokeMethod(dialog, "setPolarity", Q_ARG("QVariant", "negative"))
    adducts = _as_list(dialog.property("adducts"))
    assert adducts and all(a.endswith("-") for a in adducts)
