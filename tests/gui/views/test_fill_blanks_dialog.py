"""Analyses > Fill blanks… (ADR 70): pick a layer, add its filled copy on
the background worker, offer to replace it, then remove it."""

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
    FillBlanksDialog { id: dlg }
}
"""


def _as_list(value):
    return value.toVariant() if hasattr(value, "toVariant") else value


@pytest.fixture
def dialog(application, tmp_path, visual_analysis_model, qtbot):
    out = Path(visual_analysis_model.analysisDbPath).parent
    # 3x3 grid, one feature, a hole in the middle.
    values = np.full(9, 2.0, dtype=np.float32)
    values[4] = 0.0
    y_index, x_index = np.divmod(np.arange(9), 3)
    for name in ("s1", "s2"):
        obs = pd.DataFrame({"tic": np.ones(9)}, index=[f"{name}_{i}" for i in range(9)])
        var = pd.DataFrame({"mz": [150.0]}, index=["mz_150.0000"])
        adata = ad.AnnData(X=csr_matrix(values.reshape(-1, 1)), obs=obs, var=var)
        adata.obsm["spatial"] = np.column_stack([x_index, y_index]).astype(float)
        adata.layers["TIC"] = adata.X.copy()
        adata.write_h5ad(out / f"{name}.h5ad")
    host = tmp_path / "host.qml"
    host.write_text(_HOST)
    view = QQuickView()
    ctx = view.engine().rootContext()
    ctx.setContextProperty("AnalysisBridge", application.analysis_bridge)
    view.setSource(QUrl.fromLocalFile(str(host)))
    view.show()
    QTest.qWaitForWindowExposed(view)
    dlg = view.rootObject().property("dialog")
    QMetaObject.invokeMethod(dlg, "openFor", Q_ARG("QVariant", visual_analysis_model))
    yield dlg
    view.close()


def test_fill_then_offer_replace_then_remove(dialog, application, qtbot):
    bridge = application.analysis_bridge
    assert _as_list(dialog.property("sourceLayers")) == ["TIC"]
    assert dialog.property("outputLayer") == "TIC_filled_r1"
    assert dialog.findChild(object, "fillNeighborsHint").property("text") == "at least 5 of 8"

    with qtbot.waitSignal(bridge.fillBlanksFinished, timeout=60000):
        QMetaObject.invokeMethod(dialog, "fill")

    assert not dialog.property("running")
    assert "2 empty value(s) filled" in dialog.property("statusText")
    assert dialog.property("outputExists")
    assert dialog.findChild(object, "fillRunButton").property("text") == "Replace layer"
    # A filled layer isn't offered as a source again.
    assert _as_list(dialog.property("sourceLayers")) == ["TIC"]

    dialog.setProperty("pendingRemoval", "TIC_filled_r1")
    with qtbot.waitSignal(bridge.featureLayersChanged, timeout=5000):
        dialog.findChild(object, "fillConfirmRemoveButton").clicked.emit()
    assert _as_list(dialog.property("filledRows")) == []
    assert "removed" in dialog.property("statusText")
