"""QML side of imported analyzed `.h5ad` datasets (ADR 49): the workspace's
dataset switcher, Visual Inspection following the active dataset (samples,
layer picker, notebook obs columns, tile URLs), Spatial Correlation's
"Compute Moran's I" bar, and the import dialog's inspect -> map -> import
flow."""

from __future__ import annotations

import anndata as ad
import numpy as np
import pandas as pd
import pytest
from PySide6.QtCore import QUrl
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest
from scipy.sparse import csr_matrix

from msianalyzer.core.imported_dataset import import_analyzed_h5ad



def _as_list(value):
    """See test_visual_inspection_section.py's helper of the same name."""
    return value.toVariant() if hasattr(value, "toVariant") else value


def _open_visual_tab(view, root, find_visual_child, qtbot):
    """Condensed test_visual_inspection_section.py helper: click the Visual
    Inspection tab, then wait out its async prefetch + feature/obs fetches."""
    from PySide6.QtCore import Qt

    nav_button = find_visual_child(root, "navButton_3")
    center = nav_button.mapToScene(nav_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.waitUntil(
        lambda: find_visual_child(root, "controlsFlickable") is not None, timeout=2000
    )
    controls = find_visual_child(root, "controlsFlickable")
    qtbot.waitUntil(lambda: not controls.property("featuresLoading"), timeout=2000)
    qtbot.waitUntil(lambda: not controls.property("obsColumnsLoading"), timeout=2000)


def _pipeline_sample(name: str) -> ad.AnnData:
    coords = [(x, y) for y in range(3) for x in range(3)]
    obs = pd.DataFrame(
        {"x": [c[0] for c in coords], "y": [c[1] for c in coords], "tic": np.ones(9)},
        index=[f"{name}_px{i}" for i in range(9)],
    )
    var = pd.DataFrame({"mz": [150.0]}, index=["mz_150.0000"])
    adata = ad.AnnData(
        X=csr_matrix(np.arange(9, dtype=np.float32).reshape(9, 1)), obs=obs, var=var
    )
    adata.obsm["spatial"] = np.array(coords, dtype=float)
    adata.layers["TIC"] = adata.X.copy()
    adata.uns["project_id"] = "test-run"
    return adata


@pytest.fixture
def notebook_source(visual_analysis_model, tmp_path):
    """Pipeline `.h5ad`s for `visual_analysis_model`'s samples s1/s2, plus
    a notebook-style merged file with a `leiden` column. Returns its path."""
    out = tmp_path / "out"
    samples = {name: _pipeline_sample(name) for name in ("s1", "s2")}
    for name, adata in samples.items():
        adata.write_h5ad(out / f"{name}.h5ad")
    merged = ad.concat(
        samples, label="sample", index_unique="-", merge="same", uns_merge="unique"
    )
    merged.obs["leiden"] = pd.Categorical([str(i % 2) for i in range(merged.n_obs)])
    src = tmp_path / "notebook.h5ad"
    merged.write_h5ad(src)
    return src


@pytest.fixture
def dataset_id(visual_analysis_model, notebook_source):
    return import_analyzed_h5ad(
        visual_analysis_model.analysisDbPath, notebook_source, name="Clusters",
        sample_column="sample", sample_map={"s1": "s1", "s2": "s2"}, n_workers=1,
    ).dataset_id


def _bridge_idle(bridge) -> bool:
    """True once none of the bridge's background QThreads is running."""
    for name, value in vars(bridge).items():
        if name.endswith("_worker") and value is not None:
            try:
                if value.isRunning():
                    return False
            except RuntimeError:  # already deleted on the C++ side
                continue
    return True


@pytest.fixture(autouse=True)
def _drain_bridge_workers(application, qtbot):
    """Switching datasets starts background fetches (feature list,
    spatial-correlation models). Let them finish before the per-test
    Application (and its bridge, their parent) is torn down, or Qt aborts
    on "QThread: Destroyed while thread is still running"."""
    yield
    qtbot.waitUntil(lambda: _bridge_idle(application.analysis_bridge), timeout=10000)
    qtbot.wait(20)  # let queued finished-signals land before teardown


def test_dataset_switcher_hidden_without_imports(
    analysis_view, visual_analysis_model, find_visual_child
):
    view = analysis_view(visual_analysis_model)
    switcher = find_visual_child(view.rootObject(), "datasetSwitcher")
    assert switcher.property("visible") is False


def test_dataset_switcher_lists_imports_and_follows_bridge(
    analysis_view, visual_analysis_model, dataset_id, find_visual_child, application, qtbot
):
    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    switcher = find_visual_child(root, "datasetSwitcher")
    combo = find_visual_child(root, "datasetCombo")
    assert switcher.property("visible") is True
    assert combo.property("count") == 2
    assert combo.property("currentIndex") == 0  # workspace opens on the pipeline output

    application.analysis_bridge.setActiveDataset(visual_analysis_model.analysisDbPath, dataset_id)
    qtbot.waitUntil(lambda: combo.property("currentIndex") == 1, timeout=1000)
    assert find_visual_child(root, "removeDatasetButton").property("visible") is True


def test_visual_inspection_follows_active_dataset(
    analysis_view, visual_analysis_model, dataset_id, find_visual_child, application, qtbot
):
    application.analysis_bridge.setActiveDataset(visual_analysis_model.analysisDbPath, dataset_id)
    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    # AnalysisPage resets to the pipeline output on open — re-select.
    application.analysis_bridge.setActiveDataset(visual_analysis_model.analysisDbPath, dataset_id)
    _open_visual_tab(view, root, find_visual_child, qtbot)

    controls = find_visual_child(root, "controlsFlickable")
    assert controls.property("datasetActive") is True
    assert find_visual_child(root, "datasetLayerCombo").property("visible") is True
    assert find_visual_child(root, "rawLayerButton").property("visible") is False
    assert _as_list(controls.property("datasetLayerOptions")) == ["X", "TIC"]
    assert controls.property("dataLayer") == "TIC"
    names = [c["name"] for c in _as_list(controls.property("obsColumns"))]
    assert "leiden" in names

    # The tile really renders from the imported file: its URL carries the
    # dataset token, and the image loads (3x3 grid).
    tile = find_visual_child(root, "heatmapImage_s1")
    image = find_visual_child(tile, "zoomableImageContent")
    qtbot.waitUntil(lambda: image.property("sourceSize").width() > 0, timeout=3000)
    assert image.property("source").toString().endswith(f"%7Cds{dataset_id}")
    assert image.property("sourceSize").width() == 3


def test_switching_dataset_while_on_visual_inspection_reloads_tiles(
    analysis_view, visual_analysis_model, dataset_id, find_visual_child, application, qtbot
):
    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)
    controls = find_visual_child(root, "controlsFlickable")
    assert controls.property("datasetActive") is False

    application.analysis_bridge.setActiveDataset(visual_analysis_model.analysisDbPath, dataset_id)

    qtbot.waitUntil(lambda: controls.property("datasetActive") is True, timeout=2000)
    qtbot.waitUntil(lambda: not controls.property("featuresLoading"), timeout=3000)
    qtbot.waitUntil(lambda: root.property("currentSectionReady") is True, timeout=3000)
    tile = find_visual_child(root, "heatmapImage_s1")
    image = find_visual_child(tile, "zoomableImageContent")
    qtbot.waitUntil(
        lambda: image.property("source").toString().endswith(f"%7Cds{dataset_id}"),
        timeout=3000,
    )
    qtbot.waitUntil(lambda: image.property("sourceSize").width() == 3, timeout=3000)


def test_spatial_correlation_compute_bar_follows_the_selected_dataset(
    analysis_view, visual_analysis_model, dataset_id, find_visual_child, application, qtbot
):
    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    nav = find_visual_child(root, "navButton_4")
    from PySide6.QtCore import Qt

    center = nav.mapToScene(nav.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.waitUntil(lambda: find_visual_child(root, "datasetMoranBar") is not None, timeout=2000)
    bar = find_visual_child(root, "datasetMoranBar")
    title = find_visual_child(root, "datasetMoranBarTitle")
    layer_combo = find_visual_child(root, "datasetMoranLayerCombo")
    # Pipeline output: computable after the run too, on its normalized layer.
    assert bar.property("visible") is True
    assert title.property("text").startswith("Pipeline output")
    assert layer_combo.property("currentText") == "TIC"

    application.analysis_bridge.setActiveDataset(visual_analysis_model.analysisDbPath, dataset_id)
    qtbot.waitUntil(lambda: title.property("text").startswith("Dataset"), timeout=1000)
    assert layer_combo.property("currentText") == "TIC"


# ---------------------------------------------------------------------------
# Import dialog
# ---------------------------------------------------------------------------

_DIALOG_HOST = """
import QtQuick
import QtQuick.Controls
import "qrc:/Views"

Item {
    width: 800; height: 700
    property var dialog: dlg
    ImportH5adDialog { id: dlg; objectName: "importH5adDialog" }
}
"""


@pytest.fixture
def dialog_view(application, tmp_path):
    host = tmp_path / "host.qml"
    host.write_text(_DIALOG_HOST)
    view = QQuickView()
    ctx = view.engine().rootContext()
    ctx.setContextProperty("Router", application.router)
    ctx.setContextProperty("AnalysisBridge", application.analysis_bridge)
    view.setSource(QUrl.fromLocalFile(str(host)))
    view.show()
    QTest.qWaitForWindowExposed(view)
    yield view
    view.close()


def test_import_dialog_inspects_maps_and_imports(
    dialog_view, visual_analysis_model, notebook_source, application, qtbot
):
    dlg = dialog_view.rootObject().property("dialog")
    from PySide6.QtCore import QMetaObject, Q_ARG

    QMetaObject.invokeMethod(dlg, "openFor", Q_ARG("QVariant", visual_analysis_model))
    # What the FileDialog's onAccepted does.
    dlg.setProperty("sourcePath", str(notebook_source))
    dlg.setProperty("inspecting", True)
    application.analysis_bridge.inspectH5ad(str(notebook_source))
    qtbot.waitUntil(lambda: dlg.property("inspection") is not None and not dlg.property("inspecting"),
                    timeout=3000)

    assert dlg.property("sampleColumn") == "sample"
    assert _as_list(dlg.property("sourceLabels")) == ["s1", "s2"]
    assert _as_list(dlg.property("mapping")) == ["s1", "s2"]  # auto-matched by name
    assert dlg.property("analysisIdMismatch") is False
    assert dlg.property("canImport") is True

    with qtbot.waitSignal(application.analysis_bridge.datasetImportFinished, timeout=20000):
        QMetaObject.invokeMethod(dlg, "startImport")
    qtbot.waitUntil(lambda: not dlg.property("importing"), timeout=2000)
    rows = application.analysis_bridge.getImportedDatasets(visual_analysis_model.analysisDbPath)
    assert [r["name"] for r in rows] == ["notebook"]  # default name = file stem


def test_import_dialog_flags_a_file_from_another_analysis(
    dialog_view, visual_analysis_model, notebook_source, application, qtbot
):
    other = notebook_source.with_name("other.h5ad")
    adata = ad.read_h5ad(notebook_source)
    adata.uns["project_id"] = "some-other-run"
    adata.write_h5ad(other)

    dlg = dialog_view.rootObject().property("dialog")
    from PySide6.QtCore import QMetaObject, Q_ARG

    QMetaObject.invokeMethod(dlg, "openFor", Q_ARG("QVariant", visual_analysis_model))
    dlg.setProperty("sourcePath", str(other))
    dlg.setProperty("inspecting", True)
    application.analysis_bridge.inspectH5ad(str(other))
    qtbot.waitUntil(lambda: dlg.property("inspection") is not None, timeout=3000)

    assert dlg.property("analysisIdMismatch") is True
    assert dlg.property("canImport") is True  # a warning, not a blocker
