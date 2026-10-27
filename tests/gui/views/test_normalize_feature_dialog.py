"""Analyses > Normalize to feature… (ADR 61): search a feature, add its
normalized layer on the background worker, then offer to replace it."""

from __future__ import annotations

from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
import pytest
from PySide6.QtCore import Q_ARG, QMetaObject, QUrl
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest
from scipy.sparse import csr_matrix

_HOST = """
import QtQuick
import "qrc:/Views"

Item {
    width: 900; height: 800
    property var dialog: dlg
    NormalizeFeatureDialog { id: dlg }
}
"""


def _as_list(value):
    return value.toVariant() if hasattr(value, "toVariant") else value


@pytest.fixture
def dialog(application, tmp_path, visual_analysis_model, qtbot):
    out = Path(visual_analysis_model.analysisDbPath).parent
    for i, name in enumerate(("s1", "s2")):
        obs = pd.DataFrame({"tic": [1.0, 1.0]}, index=[f"{name}_0", f"{name}_1"])
        var = pd.DataFrame({"mz": [150.0]}, index=["mz_150.0000"])
        X = csr_matrix(np.array([[2.0 + i], [4.0]], dtype=np.float32))
        ad.AnnData(X=X, obs=obs, var=var).write_h5ad(out / f"{name}.h5ad")
    host = tmp_path / "host.qml"
    host.write_text(_HOST)
    view = QQuickView()
    ctx = view.engine().rootContext()
    ctx.setContextProperty("AnalysisBridge", application.analysis_bridge)
    view.setSource(QUrl.fromLocalFile(str(host)))
    view.show()
    QTest.qWaitForWindowExposed(view)
    dlg = view.rootObject().property("dialog")
    with qtbot.waitSignal(application.analysis_bridge.featureListReady, timeout=10000):
        QMetaObject.invokeMethod(dlg, "openFor", Q_ARG("QVariant", visual_analysis_model))
    yield dlg
    view.close()


def test_search_normalize_then_offer_replace(dialog, application, qtbot):
    bridge = application.analysis_bridge
    assert len(_as_list(dialog.property("features"))) == 1

    dialog.findChild(object, "normalizeMzSearchField").setProperty("text", "999")
    QMetaObject.invokeMethod(dialog, "applySearch")
    assert _as_list(dialog.property("order")) == []
    dialog.findChild(object, "normalizeMzSearchField").setProperty("text", "150")
    QMetaObject.invokeMethod(dialog, "applySearch")
    assert _as_list(dialog.property("order")) == [0]

    dialog.setProperty("selectedIndex", 0)
    assert not dialog.property("selectedHasLayer")
    with qtbot.waitSignal(bridge.featureNormalizationFinished, timeout=60000):
        QMetaObject.invokeMethod(dialog, "normalize")

    assert not dialog.property("running")
    assert "feature_1" in dialog.property("statusText")
    assert dialog.property("selectedHasLayer")
    assert dialog.findChild(object, "normalizeRunButton").property("text") == "Replace layer"


def test_remove_layer_after_confirmation(dialog, application, qtbot):
    bridge = application.analysis_bridge
    dialog.setProperty("selectedIndex", 0)
    with qtbot.waitSignal(bridge.featureNormalizationFinished, timeout=60000):
        QMetaObject.invokeMethod(dialog, "normalize")
    assert len(_as_list(dialog.property("layerRows"))) == 1

    # The ✕ chip is a Repeater delegate, out of findChild's reach: it only
    # sets pendingRemoval, which the confirm row then acts on.
    dialog.setProperty("pendingRemoval", 1)
    with qtbot.waitSignal(bridge.featureLayersChanged, timeout=5000):
        dialog.findChild(object, "normalizeConfirmRemoveButton").clicked.emit()

    assert _as_list(dialog.property("layerRows")) == []
    assert dialog.property("pendingRemoval") == -1
    assert "removed" in dialog.property("statusText")
    assert not dialog.property("selectedHasLayer")
