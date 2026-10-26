"""AnalysisBridge × imported analyzed `.h5ad` datasets (ADR 49): the
dataset switcher's routing (image providers, feature list, sample list,
spatial-correlation loaders), ROI save/delete with source write-back,
and the background import / delete / Moran's I slots."""

from __future__ import annotations

import anndata as ad
import numpy as np
import pandas as pd
import pytest
from scipy.sparse import csr_matrix

from msianalyzer.core import analysis_db
from msianalyzer.core.imported_dataset import import_analyzed_h5ad, sample_h5ad_path
from msianalyzer.gui.utils.analysis_bridge import (
    AnalysisBridge,
    _load_dataset_feature_list,
    _load_spatial_correlation_table,
)

SAMPLES = ("s1", "s2", "s3")
MZS = (100.0, 200.0, 300.0)
N_X, N_Y = 5, 4
# A square covering grid cells (1..2, 1..2) — 4 pixel centers.
ROI_VERTICES = [[1, 1], [3, 1], [3, 3], [1, 3]]


def _pipeline_sample(name: str, offset: float) -> ad.AnnData:
    coords = [(x, y) for y in range(N_Y) for x in range(N_X)]
    n = len(coords)
    obs = pd.DataFrame(
        {"x": [c[0] for c in coords], "y": [c[1] for c in coords], "tic": np.ones(n)},
        index=[f"{name}_px{i}" for i in range(n)],
    )
    var = pd.DataFrame({"mz": list(MZS)}, index=[f"mz_{m:.4f}" for m in MZS])
    X = np.arange(n * len(MZS), dtype=np.float32).reshape(n, len(MZS)) + offset
    adata = ad.AnnData(X=csr_matrix(X), obs=obs, var=var)
    adata.obsm["spatial"] = np.array(coords, dtype=float)
    adata.layers["TIC"] = adata.X.copy()
    return adata


@pytest.fixture
def analysis(tmp_path):
    """3-sample analysis + a notebook-style merged source (one feature
    filtered out, `leiden` added), imported as dataset "v1"."""
    out = tmp_path / "out"
    out.mkdir()
    db = out / "analysis_run1.db"
    analysis_db.init_analysis_db(db).close()
    samples = {}
    for i, name in enumerate(SAMPLES):
        analysis_db.register_sample(db, name=name, raw_db_path=tmp_path / f"{name}.db")
        samples[name] = _pipeline_sample(name, 100.0 * i)
        samples[name].write_h5ad(out / f"{name}.h5ad")
    with analysis_db.connect(db) as con:
        con.executemany(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (?, ?, '{}')",
            [(i + 1, mz) for i, mz in enumerate(MZS)],
        )
        con.commit()

    merged = ad.concat(samples, label="sample", index_unique="-", merge="same")
    merged = merged[:, merged.var["mz"] != 200.0].copy()
    merged.obs["leiden"] = pd.Categorical([str(i % 2) for i in range(merged.n_obs)])
    src = tmp_path / "notebook.h5ad"
    merged.write_h5ad(src)
    result = import_analyzed_h5ad(
        db, src, name="v1", sample_column="sample",
        sample_map={s: s for s in SAMPLES}, n_workers=1,
    )
    return db, src, result


def test_features_table_insert_matches_schema(analysis):
    # Guard for the fixture itself: the features table accepted our rows.
    db, _src, _result = analysis
    assert list(analysis_db.load_feature_list(db)["mz"]) == list(MZS)


def test_get_imported_datasets_lists_the_import(analysis):
    db, src, result = analysis
    rows = AnalysisBridge().getImportedDatasets(str(db))
    assert [r["name"] for r in rows] == ["v1"]
    assert rows[0]["id"] == result.dataset_id
    assert rows[0]["source_path"] == str(src.resolve())


def test_set_active_dataset_routes_providers_and_emits(analysis, qtbot):
    db, _src, result = analysis
    bridge = AnalysisBridge()
    assert bridge.activeDatasetId == -1

    with qtbot.waitSignal(bridge.activeDatasetChanged, timeout=1000):
        bridge.setActiveDataset(str(db), result.dataset_id)

    assert bridge.activeDatasetId == result.dataset_id
    assert bridge.heatmap_provider._h5ad_path("s1") == sample_h5ad_path(db, "s1", result.rel_dir)
    info = bridge.getActiveDatasetInfo()
    assert info["name"] == "v1" and "TIC" in info["layers"]
    assert sorted(info["samples"]) == list(SAMPLES)

    # Re-opening Visual Inspection re-points the provider — must keep the dataset.
    bridge.setHeatmapAnalysis(str(db))
    assert bridge.heatmap_provider._h5ad_path("s1") == sample_h5ad_path(db, "s1", result.rel_dir)

    with qtbot.waitSignal(bridge.activeDatasetChanged, timeout=1000):
        bridge.setActiveDataset(str(db), -1)
    assert bridge.activeDatasetId == -1
    assert bridge.getActiveDatasetInfo() == {}
    assert bridge.heatmap_provider._h5ad_path("s1") == sample_h5ad_path(db, "s1")


def test_another_analysis_path_always_resolves_to_pipeline(analysis, tmp_path):
    db, _src, result = analysis
    bridge = AnalysisBridge()
    bridge.setActiveDataset(str(db), result.dataset_id)
    other = tmp_path / "elsewhere" / "analysis_x.db"
    assert bridge._inspection_h5ad_path(str(other), "s1") == other.parent / "s1.h5ad"


def test_inspection_samples_follow_the_dataset(analysis, tmp_path):
    db, src, _result = analysis
    two = import_analyzed_h5ad(
        db, src, name="two", sample_column="sample",
        sample_map={"s1": "s1", "s3": "s3"}, n_workers=1,
    )
    bridge = AnalysisBridge()
    assert [s["name"] for s in bridge.getInspectionSamples(str(db))] == list(SAMPLES)
    bridge.setActiveDataset(str(db), two.dataset_id)
    assert [s["name"] for s in bridge.getInspectionSamples(str(db))] == ["s1", "s3"]
    # MS1's selector stays all-samples
    assert [s["name"] for s in bridge.getSamples(str(db))] == list(SAMPLES)


def test_dataset_feature_list_only_has_features_still_present(analysis):
    db, _src, result = analysis
    df = _load_dataset_feature_list(str(db), result.rel_dir)
    assert list(df["mz"]) == [100.0, 300.0]
    assert list(df["feature_id"]) == [1, 3]  # joined back to analysis features


def test_request_feature_list_is_keyed_per_dataset(analysis, qtbot):
    db, _src, result = analysis
    bridge = AnalysisBridge()
    with qtbot.waitSignal(bridge.featureListReady, timeout=3000) as spy:
        bridge.requestFeatureList(str(db))
    assert [f["mz"] for f in spy.args[0]] == list(MZS)

    bridge.setActiveDataset(str(db), result.dataset_id)
    with qtbot.waitSignal(bridge.featureListReady, timeout=3000) as spy:
        bridge.requestFeatureList(str(db))
    assert [f["mz"] for f in spy.args[0]] == [100.0, 300.0]


def test_spatial_correlation_loader_reads_dataset_files(analysis):
    db, _src, result = analysis
    for name in SAMPLES:
        path = sample_h5ad_path(db, name, result.rel_dir)
        adata = ad.read_h5ad(path)
        adata.uns["moranI"] = pd.DataFrame({"I": [0.5, 0.1]}, index=adata.var_names)
        adata.write_h5ad(path)

    assert _load_spatial_correlation_table(str(db)).empty  # pipeline has none
    df = _load_spatial_correlation_table(str(db), rel_dir=result.rel_dir)
    assert len(df) == 3 * 2
    assert set(df["sample"]) == set(SAMPLES)


# ---------------------------------------------------------------------------
# ROI with a dataset active
# ---------------------------------------------------------------------------


def test_save_roi_with_dataset_writes_imported_file_and_source(analysis):
    db, src, result = analysis
    bridge = AnalysisBridge()
    bridge.setActiveDataset(str(db), result.dataset_id)

    out = bridge.saveRoi(str(db), "s2", "liver", "#ff0000", ROI_VERTICES)

    assert out == {"ok": True, "pixel_count": 4}
    imported = ad.read_h5ad(sample_h5ad_path(db, "s2", result.rel_dir))
    assert imported.obs["roi_liver"].sum() == 4
    source = ad.read_h5ad(src)
    assert int(source.obs["roi_liver"].sum()) == 4
    assert set(source.obs.loc[source.obs["roi_liver"], "sample"]) == {"s2"}
    # pipeline files untouched
    assert "roi_liver" not in ad.read_h5ad(db.parent / "s2.h5ad").obs
    assert [r["name"] for r in bridge.getSampleRois(str(db), "s2")] == ["liver"]


def test_save_roi_with_missing_source_still_saves_and_warns(analysis, qtbot):
    db, src, result = analysis
    bridge = AnalysisBridge()
    bridge.setActiveDataset(str(db), result.dataset_id)
    src.unlink()

    with qtbot.waitSignal(bridge.roiSourceWriteFailed, timeout=1000):
        out = bridge.saveRoi(str(db), "s1", "liver", "#ff0000", ROI_VERTICES)

    assert out["ok"] is True and out["pixel_count"] == 4
    assert "could not be written back" in out["warning"]
    assert ad.read_h5ad(sample_h5ad_path(db, "s1", result.rel_dir)).obs["roi_liver"].sum() == 4


def test_delete_roi_with_dataset_removes_it_from_source(analysis):
    db, src, result = analysis
    bridge = AnalysisBridge()
    bridge.setActiveDataset(str(db), result.dataset_id)
    bridge.saveRoi(str(db), "s1", "liver", "#ff0000", ROI_VERTICES)

    assert bridge.deleteRoiFromSample(str(db), "s1", "liver") is True

    assert "roi_liver" not in ad.read_h5ad(src).obs
    assert "roi_liver" not in ad.read_h5ad(sample_h5ad_path(db, "s1", result.rel_dir)).obs


# ---------------------------------------------------------------------------
# Background slots
# ---------------------------------------------------------------------------


def test_inspect_h5ad_emits_inspection(analysis, qtbot):
    _db, src, _result = analysis
    bridge = AnalysisBridge()
    with qtbot.waitSignal(bridge.h5adInspected, timeout=3000) as spy:
        bridge.inspectH5ad(str(src))
    info = spy.args[0]
    assert info["kind"] == "merged"
    assert sorted(info["sample_values"]["sample"]) == list(SAMPLES)


def test_inspect_h5ad_bad_file_fails(tmp_path, qtbot):
    bad = tmp_path / "bad.h5ad"
    bad.write_text("not hdf5")
    bridge = AnalysisBridge()
    with qtbot.waitSignal(bridge.h5adInspectFailed, timeout=3000) as spy:
        bridge.inspectH5ad(str(bad))
    assert "bad.h5ad" in spy.args[0]


def test_import_h5ad_reports_progress_and_finishes(analysis, qtbot):
    db, src, _result = analysis
    bridge = AnalysisBridge()
    progress = []
    bridge.datasetTaskProgress.connect(lambda d, t, s: progress.append((d, t)))

    with qtbot.waitSignals(
        [bridge.importedDatasetsChanged, bridge.datasetImportFinished], timeout=20000
    ):
        bridge.importH5ad(str(db), str(src), "v2", "sample", {"s1": "s1", "s2": "s2"})

    qtbot.waitUntil(lambda: len(progress) == 2, timeout=2000)
    assert [r["name"] for r in bridge.getImportedDatasets(str(db))] == ["v1", "v2"]


def test_import_h5ad_duplicate_name_fails_with_message(analysis, qtbot):
    db, src, _result = analysis
    bridge = AnalysisBridge()
    with qtbot.waitSignal(bridge.datasetImportFailed, timeout=20000) as spy:
        bridge.importH5ad(str(db), str(src), "v1", "sample", {"s1": "s1"})
    assert "already exists" in spy.args[0]


def test_delete_active_dataset_falls_back_to_pipeline(analysis, qtbot):
    db, src, result = analysis
    bridge = AnalysisBridge()
    bridge.setActiveDataset(str(db), result.dataset_id)

    with qtbot.waitSignals(
        [bridge.activeDatasetChanged, bridge.importedDatasetsChanged], timeout=1000
    ):
        assert bridge.deleteImportedDataset(str(db), result.dataset_id) is True

    assert bridge.activeDatasetId == -1
    assert bridge.getImportedDatasets(str(db)) == []
    assert src.exists()


def test_compute_dataset_moran_on_the_pipeline_output(analysis, qtbot):
    # No imported dataset active: the pipeline output's own files.
    db, _src, _result = analysis
    bridge = AnalysisBridge()
    with qtbot.waitSignal(bridge.datasetMoranFinished, timeout=60000):
        bridge.computeDatasetMoran(str(db), "TIC", True, False)

    assert len(_load_spatial_correlation_table(str(db))) == 3 * 3


def test_compute_dataset_moran_populates_spatial_correlation(analysis, qtbot):
    db, _src, result = analysis
    bridge = AnalysisBridge()
    bridge.setActiveDataset(str(db), result.dataset_id)

    with qtbot.waitSignal(bridge.datasetMoranFinished, timeout=60000):
        bridge.computeDatasetMoran(str(db), "TIC", True, False)

    df = _load_spatial_correlation_table(str(db), rel_dir=result.rel_dir)
    assert len(df) == 3 * 2
    model = bridge.getSpatialCorrelationModel(str(db))
    qtbot.waitUntil(lambda: not model.rowsLoading, timeout=5000)
