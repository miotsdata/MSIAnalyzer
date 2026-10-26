"""Tests for `core/imported_dataset.py` — re-importing a notebook-analyzed
`.h5ad` (merged or single-sample) into an analysis as a linked dataset
(ADR 49).

Fixtures deliberately use 3 samples, not 1 — a per-sample split/loop bug
that keeps only "the last one" is otherwise untestable (see the
`anndata-backed-mode` lesson in ADR 48's follow-up).
"""

from __future__ import annotations

import anndata as ad
import numpy as np
import pandas as pd
import pytest
from scipy.sparse import csr_matrix

from msianalyzer.core import analysis_db
from msianalyzer.core.imported_dataset import (
    GRID_UNS_KEY,
    SOURCE_OBS_NAME_COLUMN,
    ImportedDatasetError,
    delete_imported_dataset,
    import_analyzed_h5ad,
    inspect_h5ad,
    remove_roi_from_source,
    sample_h5ad_path,
    write_roi_to_source,
)
from msianalyzer.core.plotting.heatmap import pixel_grid_indices
from msianalyzer.core.plotting.roi import load_sample_rois, save_roi_to_sample

SAMPLES = ("s1", "s2", "s3")
N_X, N_Y = 5, 4
MZS = (100.0, 200.0, 300.0)


def _make_pipeline_sample(name: str, offset: float) -> ad.AnnData:
    """One pipeline-shaped sample `.h5ad`: pixel ids as obs_names, `x`/`y`
    obs columns + `obsm["spatial"]` (real, non-0-based coordinates so a
    grid-index bug can't hide behind coordinates == indices), `X` raw,
    `layers["TIC"]`, `var["mz"]`."""
    coords = [(10 + 2 * x, 50 + 2 * y) for y in range(N_Y) for x in range(N_X)]
    n = len(coords)
    obs = pd.DataFrame(
        {
            "x": [c[0] for c in coords],
            "y": [c[1] for c in coords],
            "tic": np.full(n, 1000.0),
        },
        index=[f"{name}_px{i}" for i in range(n)],
    )
    var = pd.DataFrame({"mz": list(MZS)}, index=[f"mz_{m:.4f}" for m in MZS])
    X = np.arange(n * len(MZS), dtype=np.float32).reshape(n, len(MZS)) + offset
    adata = ad.AnnData(X=csr_matrix(X), obs=obs, var=var)
    adata.obsm["spatial"] = np.array(coords, dtype=float)
    adata.layers["TIC"] = adata.X.copy()
    adata.uns["spatial"] = {name: {"images": {}, "scalefactors": {}, "metadata": {}}}
    adata.uns["project_id"] = "run1"  # create_spatial_adata stores the analysis id here
    return adata


@pytest.fixture
def analysis(tmp_path):
    """An analysis out_dir: analysis DB with 3 registered samples, and each
    sample's pipeline `.h5ad` next to it. Returns `(db_path, {name: adata})`."""
    out_dir = tmp_path / "analysis_out"
    out_dir.mkdir()
    db = out_dir / "analysis_run1.db"
    analysis_db.init_analysis_db(db).close()
    samples = {}
    for i, name in enumerate(SAMPLES):
        analysis_db.register_sample(db, name=name, raw_db_path=tmp_path / f"{name}.db")
        adata = _make_pipeline_sample(name, offset=1000.0 * i)
        adata.write_h5ad(out_dir / f"{name}.h5ad")
        samples[name] = adata
    return db, samples


def _notebook_merged(samples: dict, *, drop_obsm=True) -> ad.AnnData:
    """What a user's notebook plausibly hands back: the pipeline's own
    `ad.concat(label="sample", index_unique="-")`, then QC-filtered
    (s1 loses its entire first grid column — an edge column, the case
    that would collapse the grid without the stored grid axes; s2 loses
    two scattered pixels), one var filtered out, clusters/UMAP/a neighbor
    graph added, rows shuffled, and (optionally) `obsm["spatial"]` lost."""
    merged = ad.concat(
        samples, label="sample", index_unique="-", merge="same", uns_merge="unique"
    )
    x = merged.obs["x"].to_numpy()
    keep = ~((merged.obs["sample"] == "s1").to_numpy() & (x == 10))
    s2_rows = np.flatnonzero((merged.obs["sample"] == "s2").to_numpy())
    keep[s2_rows[[3, 7]]] = False
    merged = merged[keep, merged.var["mz"] != 200.0].copy()

    rng = np.random.default_rng(0)
    order = rng.permutation(merged.n_obs)
    merged = merged[order].copy()

    merged.obs["leiden"] = pd.Categorical(
        [str(i % 3) for i in range(merged.n_obs)]
    )
    merged.obsm["X_umap"] = rng.random((merged.n_obs, 2))
    merged.obsp["connectivities"] = csr_matrix(np.eye(merged.n_obs))
    if drop_obsm:
        del merged.obsm["spatial"]
    return merged


# ---------------------------------------------------------------------------
# inspect_h5ad
# ---------------------------------------------------------------------------


def test_inspect_merged_detects_sample_column_and_values(analysis, tmp_path):
    _db, samples = analysis
    src = tmp_path / "notebook_merged.h5ad"
    _notebook_merged(samples).write_h5ad(src)

    info = inspect_h5ad(src)

    assert info.kind == "merged"
    assert info.sample_column == "sample"
    assert sorted(info.sample_values["sample"]) == list(SAMPLES)
    assert "leiden" in info.sample_values  # low-cardinality candidate too
    assert info.n_vars == 2
    assert "TIC" in info.layers
    assert "leiden" in info.obs_columns
    assert info.analysis_id == "run1"  # survives ad.concat(uns_merge="unique")


def test_inspect_single_detects_sample_from_uns_spatial(analysis, tmp_path):
    _db, samples = analysis
    src = tmp_path / "s2_processed.h5ad"
    samples["s2"].write_h5ad(src)

    info = inspect_h5ad(src)

    assert info.kind == "single"
    assert info.sample_column is None
    assert info.spatial_keys == ["s2"]


# ---------------------------------------------------------------------------
# import_analyzed_h5ad — merged
# ---------------------------------------------------------------------------


def test_import_merged_splits_every_sample_with_pipeline_pixel_ids(analysis, tmp_path):
    db, samples = analysis
    src = tmp_path / "notebook_merged.h5ad"
    notebook = _notebook_merged(samples)
    notebook.write_h5ad(src)

    result = import_analyzed_h5ad(
        db, src, name="Clustered v1", sample_column="sample",
        sample_map={s: s for s in SAMPLES}, n_workers=2,
    )

    assert result.n_samples == 3
    assert result.n_pixels == notebook.n_obs
    for name in SAMPLES:
        path = sample_h5ad_path(db, name, result.rel_dir)
        assert path.exists()
        out = ad.read_h5ad(path)
        expected_rows = notebook.obs["sample"] == name
        assert out.n_obs == int(expected_rows.sum())
        # obs_names are back to the pipeline's own pixel ids ...
        assert set(out.obs_names) <= set(samples[name].obs_names)
        # ... and the notebook's own names are kept for ROI write-back.
        assert set(out.obs[SOURCE_OBS_NAME_COLUMN]) == set(
            notebook.obs_names[expected_rows.to_numpy()]
        )
        # notebook-derived data survived the split
        assert "leiden" in out.obs.columns
        assert "X_umap" in out.obsm
        assert len(out.obsp) == 0  # whole-dataset graphs dropped
        assert list(out.var["mz"]) == [100.0, 300.0]


def test_import_restores_spatial_coordinates_from_pipeline(analysis, tmp_path):
    db, samples = analysis
    src = tmp_path / "notebook_merged.h5ad"
    _notebook_merged(samples, drop_obsm=True).write_h5ad(src)

    result = import_analyzed_h5ad(
        db, src, name="v1", sample_column="sample",
        sample_map={s: s for s in SAMPLES}, n_workers=1,
    )

    for name in SAMPLES:
        out = ad.read_h5ad(sample_h5ad_path(db, name, result.rel_dir))
        pipeline = samples[name]
        expected = pipeline.obsm["spatial"][
            pipeline.obs_names.get_indexer(out.obs_names)
        ]
        np.testing.assert_array_equal(out.obsm["spatial"], expected)


def test_import_keeps_full_grid_when_an_edge_column_was_filtered(analysis, tmp_path):
    """s1 lost its whole first column in the notebook. Without the stored
    grid axes, `pixel_grid_indices` would rebuild a 4-column grid and
    every ROI / H&E registration (grid-index space) would shift by one."""
    db, samples = analysis
    src = tmp_path / "notebook_merged.h5ad"
    _notebook_merged(samples).write_h5ad(src)

    result = import_analyzed_h5ad(
        db, src, name="v1", sample_column="sample",
        sample_map={s: s for s in SAMPLES}, n_workers=1,
    )

    out = ad.read_h5ad(sample_h5ad_path(db, "s1", result.rel_dir))
    assert GRID_UNS_KEY in out.uns
    x_index, y_index, unique_x, unique_y = pixel_grid_indices(out)
    assert unique_x.size == N_X and unique_y.size == N_Y
    # Same grid cell as in the pipeline file, pixel by pixel.
    pipeline = samples["s1"]
    px, py, _, _ = pixel_grid_indices(pipeline)
    rows = pipeline.obs_names.get_indexer(out.obs_names)
    np.testing.assert_array_equal(x_index, px[rows])
    np.testing.assert_array_equal(y_index, py[rows])
    assert x_index.min() == 1  # column 0 really is gone


def test_import_with_renamed_sample_labels_uses_the_map(analysis, tmp_path):
    db, samples = analysis
    merged = _notebook_merged(samples)
    merged.obs["sample"] = merged.obs["sample"].map(
        {"s1": "liver_A", "s2": "liver_B", "s3": "kidney"}
    ).astype("category")
    # A renamed label also means index_unique suffixes no longer match —
    # re-derive them the way the user's rename would have left them.
    src = tmp_path / "renamed.h5ad"
    merged.write_h5ad(src)

    result = import_analyzed_h5ad(
        db, src, name="renamed", sample_column="sample",
        sample_map={"liver_A": "s1", "liver_B": "s2", "kidney": "s3"}, n_workers=1,
    )

    assert result.n_samples == 3
    out = ad.read_h5ad(sample_h5ad_path(db, "s3", result.rel_dir))
    assert set(out.obs_names) <= set(samples["s3"].obs_names)


def test_import_falls_back_to_coordinates_when_obs_names_were_reset(analysis, tmp_path):
    db, samples = analysis
    merged = _notebook_merged(samples, drop_obsm=False)
    merged.obs_names = [f"cell{i}" for i in range(merged.n_obs)]
    src = tmp_path / "reset_index.h5ad"
    merged.write_h5ad(src)

    result = import_analyzed_h5ad(
        db, src, name="reset", sample_column="sample",
        sample_map={s: s for s in SAMPLES}, n_workers=1,
    )

    for name in SAMPLES:
        out = ad.read_h5ad(sample_h5ad_path(db, name, result.rel_dir))
        assert set(out.obs_names) <= set(samples[name].obs_names)
        assert out.obs[SOURCE_OBS_NAME_COLUMN].str.startswith("cell").all()


def test_import_rejects_pixels_that_do_not_belong_to_the_sample(analysis, tmp_path):
    db, samples = analysis
    merged = _notebook_merged(samples, drop_obsm=False)
    merged.obs_names = [f"cell{i}" for i in range(merged.n_obs)]
    merged.obsm["spatial"] = merged.obsm["spatial"] + 1000.0  # off-grid
    merged.obs["x"] = merged.obsm["spatial"][:, 0]
    merged.obs["y"] = merged.obsm["spatial"][:, 1]
    src = tmp_path / "foreign.h5ad"
    merged.write_h5ad(src)

    with pytest.raises(ImportedDatasetError, match="s1|s2|s3"):
        import_analyzed_h5ad(
            db, src, name="foreign", sample_column="sample",
            sample_map={s: s for s in SAMPLES}, n_workers=1,
        )
    # Nothing half-written left behind, nothing registered.
    assert analysis_db.load_imported_datasets(db).empty
    assert not any((db.parent / "imported").glob("*/*.h5ad"))


def test_import_rejects_duplicate_name(analysis, tmp_path):
    db, samples = analysis
    src = tmp_path / "m.h5ad"
    _notebook_merged(samples).write_h5ad(src)
    kwargs = dict(sample_column="sample", sample_map={s: s for s in SAMPLES}, n_workers=1)
    import_analyzed_h5ad(db, src, name="v1", **kwargs)

    with pytest.raises(ImportedDatasetError, match="already exists"):
        import_analyzed_h5ad(db, src, name="v1", **kwargs)


def test_import_rejects_unknown_analysis_sample(analysis, tmp_path):
    db, samples = analysis
    src = tmp_path / "m.h5ad"
    _notebook_merged(samples).write_h5ad(src)

    with pytest.raises(ImportedDatasetError, match="nope"):
        import_analyzed_h5ad(
            db, src, name="v1", sample_column="sample",
            sample_map={"s1": "nope"}, n_workers=1,
        )


def test_import_subset_of_samples_only_writes_mapped_ones(analysis, tmp_path):
    db, samples = analysis
    src = tmp_path / "m.h5ad"
    _notebook_merged(samples).write_h5ad(src)

    result = import_analyzed_h5ad(
        db, src, name="two", sample_column="sample",
        sample_map={"s1": "s1", "s3": "s3"}, n_workers=1,
    )

    assert result.n_samples == 2
    assert not sample_h5ad_path(db, "s2", result.rel_dir).exists()


def test_import_registers_dataset_and_samples_in_analysis_db(analysis, tmp_path):
    db, samples = analysis
    src = tmp_path / "m.h5ad"
    _notebook_merged(samples).write_h5ad(src)

    result = import_analyzed_h5ad(
        db, src, name="v1", sample_column="sample",
        sample_map={s: s for s in SAMPLES}, n_workers=1,
    )

    datasets = analysis_db.load_imported_datasets(db)
    assert list(datasets["name"]) == ["v1"]
    info = analysis_db.load_imported_dataset(db, result.dataset_id)
    assert info["rel_dir"] == result.rel_dir
    assert info["source_path"] == str(src.resolve())
    assert info["source_kind"] == "merged"
    assert info["sample_column"] == "sample"
    assert info["layers"] == ["TIC"]  # ad.concat's stray `None` layer key left out
    assert sorted(s["name"] for s in info["samples"]) == list(SAMPLES)
    assert {s["source_label"] for s in info["samples"]} == set(SAMPLES)


def test_import_reports_progress_once_per_sample(analysis, tmp_path):
    db, samples = analysis
    src = tmp_path / "m.h5ad"
    _notebook_merged(samples).write_h5ad(src)
    calls = []

    import_analyzed_h5ad(
        db, src, name="v1", sample_column="sample",
        sample_map={s: s for s in SAMPLES}, n_workers=1,
        progress=lambda done, total, name: calls.append((done, total, name)),
    )

    assert [c[0] for c in calls] == [1, 2, 3]
    assert {c[2] for c in calls} == set(SAMPLES)
    assert all(c[1] == 3 for c in calls)


def test_import_parses_mz_from_var_names_when_mz_column_missing(analysis, tmp_path):
    db, samples = analysis
    merged = _notebook_merged(samples)
    merged.var = merged.var.drop(columns=["mz"])
    src = tmp_path / "no_mz.h5ad"
    merged.write_h5ad(src)

    result = import_analyzed_h5ad(
        db, src, name="v1", sample_column="sample",
        sample_map={s: s for s in SAMPLES}, n_workers=1,
    )

    out = ad.read_h5ad(sample_h5ad_path(db, "s1", result.rel_dir))
    assert list(out.var["mz"]) == [100.0, 300.0]


# ---------------------------------------------------------------------------
# import_analyzed_h5ad — single-sample
# ---------------------------------------------------------------------------


def test_import_single_sample_file(analysis, tmp_path):
    db, samples = analysis
    single = samples["s2"][samples["s2"].obs["x"] != 10].copy()
    single.obs["leiden"] = pd.Categorical(["0"] * single.n_obs)
    src = tmp_path / "s2_processed.h5ad"
    single.write_h5ad(src)

    result = import_analyzed_h5ad(
        db, src, name="s2 only", sample_column=None,
        sample_map={"s2": "s2"}, n_workers=1,
    )

    assert result.n_samples == 1
    out = ad.read_h5ad(sample_h5ad_path(db, "s2", result.rel_dir))
    assert out.n_obs == single.n_obs
    assert "leiden" in out.obs.columns
    info = analysis_db.load_imported_dataset(db, result.dataset_id)
    assert info["source_kind"] == "single"
    assert info["sample_column"] is None


# ---------------------------------------------------------------------------
# delete / resolver
# ---------------------------------------------------------------------------


def test_delete_imported_dataset_removes_files_and_rows(analysis, tmp_path):
    db, samples = analysis
    src = tmp_path / "m.h5ad"
    _notebook_merged(samples).write_h5ad(src)
    result = import_analyzed_h5ad(
        db, src, name="v1", sample_column="sample",
        sample_map={s: s for s in SAMPLES}, n_workers=1,
    )

    assert delete_imported_dataset(db, result.dataset_id) is True

    assert analysis_db.load_imported_datasets(db).empty
    assert not (db.parent / result.rel_dir).exists()
    assert src.exists()  # the user's own file is never deleted


def test_sample_h5ad_path_pipeline_default(tmp_path):
    db = tmp_path / "analysis_x.db"
    assert sample_h5ad_path(db, "s1") == tmp_path / "s1.h5ad"
    assert sample_h5ad_path(db, "s1", "imported/v1") == tmp_path / "imported" / "v1" / "s1.h5ad"


# ---------------------------------------------------------------------------
# ROI write-back to the user's source file (obs column only)
# ---------------------------------------------------------------------------


def _import_merged(analysis, tmp_path):
    db, samples = analysis
    src = tmp_path / "notebook_merged.h5ad"
    _notebook_merged(samples).write_h5ad(src)
    result = import_analyzed_h5ad(
        db, src, name="v1", sample_column="sample",
        sample_map={s: s for s in SAMPLES}, n_workers=1,
    )
    return db, src, result


def test_import_replaces_merged_format_rois_with_pipeline_polygons(analysis, tmp_path):
    db, samples = analysis
    square = [[0, 0], [2, 0], [2, 2], [0, 2]]
    save_roi_to_sample(db.parent / "s1.h5ad", "tumor", "#ff0000", square)
    merged = _notebook_merged(samples)
    # merged.h5ad's shape: no polygon, just which samples have the ROI.
    merged.uns["rois"] = {
        "tumor": {"color": "#ff0000", "samples": ["s1"]},
        "gone": {"color": "#00ff00", "samples": ["s2"]},
    }
    src = tmp_path / "notebook_merged.h5ad"
    merged.write_h5ad(src)

    result = import_analyzed_h5ad(
        db, src, name="v1", sample_column="sample",
        sample_map={s: s for s in SAMPLES}, n_workers=1,
    )

    s1 = load_sample_rois(sample_h5ad_path(db, "s1", result.rel_dir))
    assert list(s1) == ["tumor"] and s1["tumor"]["vertices"] == square
    assert load_sample_rois(sample_h5ad_path(db, "s2", result.rel_dir)) == {}


def test_write_roi_to_source_sets_only_that_samples_rows(analysis, tmp_path):
    db, src, result = _import_merged(analysis, tmp_path)
    x_before = ad.read_h5ad(src).X.toarray()
    for name in ("s1", "s2"):
        out = ad.read_h5ad(sample_h5ad_path(db, name, result.rel_dir))
        mask = np.zeros(out.n_obs, dtype=bool)
        mask[:3] = True
        write_roi_to_source(src, "roi_liver", out.obs[SOURCE_OBS_NAME_COLUMN], mask)

    reread = ad.read_h5ad(src)
    assert reread.obs["roi_liver"].dtype == bool
    assert int(reread.obs["roi_liver"].sum()) == 6
    per_sample = reread.obs.groupby("sample", observed=True)["roi_liver"].sum()
    assert per_sample["s1"] == 3 and per_sample["s2"] == 3 and per_sample["s3"] == 0
    # Matched by name, not position: exactly the rows we flagged.
    s1 = ad.read_h5ad(sample_h5ad_path(db, "s1", result.rel_dir))
    flagged = set(s1.obs[SOURCE_OBS_NAME_COLUMN].iloc[:3])
    assert set(reread.obs_names[reread.obs["roi_liver"].to_numpy()]) >= flagged
    # X is untouched, everything else still readable.
    np.testing.assert_array_equal(reread.X.toarray(), x_before)
    assert "X_umap" in reread.obsm and "leiden" in reread.obs


def test_write_roi_to_source_overwrite_clears_old_rows_of_that_sample(analysis, tmp_path):
    db, src, result = _import_merged(analysis, tmp_path)
    out = ad.read_h5ad(sample_h5ad_path(db, "s1", result.rel_dir))
    names = out.obs[SOURCE_OBS_NAME_COLUMN]
    first = np.zeros(out.n_obs, dtype=bool)
    first[:4] = True
    second = np.zeros(out.n_obs, dtype=bool)
    second[-1] = True

    write_roi_to_source(src, "roi_a", names, first)
    write_roi_to_source(src, "roi_a", names, second)

    assert int(ad.read_h5ad(src).obs["roi_a"].sum()) == 1


def test_remove_roi_from_source_drops_column_once_empty(analysis, tmp_path):
    db, src, result = _import_merged(analysis, tmp_path)
    outs = {
        n: ad.read_h5ad(sample_h5ad_path(db, n, result.rel_dir)) for n in ("s1", "s2")
    }
    for out in outs.values():
        write_roi_to_source(
            src, "roi_a", out.obs[SOURCE_OBS_NAME_COLUMN], np.ones(out.n_obs, dtype=bool)
        )

    assert remove_roi_from_source(src, "roi_a", outs["s1"].obs[SOURCE_OBS_NAME_COLUMN])
    reread = ad.read_h5ad(src)
    assert int(reread.obs["roi_a"].sum()) == outs["s2"].n_obs

    assert remove_roi_from_source(src, "roi_a", outs["s2"].obs[SOURCE_OBS_NAME_COLUMN])
    assert "roi_a" not in ad.read_h5ad(src).obs.columns


def test_remove_roi_from_source_missing_column_is_noop(analysis, tmp_path):
    db, src, result = _import_merged(analysis, tmp_path)
    out = ad.read_h5ad(sample_h5ad_path(db, "s1", result.rel_dir))
    assert remove_roi_from_source(src, "roi_nope", out.obs[SOURCE_OBS_NAME_COLUMN]) is False


# ---------------------------------------------------------------------------
# Moran's I on an imported dataset
# ---------------------------------------------------------------------------


def test_compute_dataset_spatial_correlation_writes_every_sample(analysis, tmp_path):
    from msianalyzer.core.imported_dataset import compute_dataset_spatial_correlation

    db, src, result = _import_merged(analysis, tmp_path)
    calls = []

    n = compute_dataset_spatial_correlation(
        db, result.dataset_id, layer="TIC", run_global=True, run_local=True,
        n_workers=1, progress=lambda d, t, s: calls.append(s),
    )

    assert n == 3
    assert sorted(calls) == list(SAMPLES)
    for name in SAMPLES:
        out = ad.read_h5ad(sample_h5ad_path(db, name, result.rel_dir))
        assert set(out.uns["moranI"].index) == set(out.var_names)
        assert out.layers["localMoranI"].shape == out.shape
    # pipeline files untouched
    assert "moranI" not in ad.read_h5ad(db.parent / "s1.h5ad").uns


def test_compute_spatial_correlation_on_the_pipeline_output(analysis):
    # dataset_id=None: the analysis' own sample files, each scored alone.
    from msianalyzer.core.imported_dataset import compute_dataset_spatial_correlation

    db, _samples = analysis
    n = compute_dataset_spatial_correlation(
        db, None, layer="TIC", run_global=True, run_local=False, n_workers=1,
    )

    assert n == 3
    for name in SAMPLES:
        out = ad.read_h5ad(sample_h5ad_path(db, name))
        assert set(out.uns["moranI"].index) == set(out.var_names)


def test_compute_dataset_spatial_correlation_needs_a_pass(analysis, tmp_path):
    from msianalyzer.core.imported_dataset import compute_dataset_spatial_correlation

    db, _src, result = _import_merged(analysis, tmp_path)
    with pytest.raises(ImportedDatasetError):
        compute_dataset_spatial_correlation(
            db, result.dataset_id, layer="TIC", run_global=False, run_local=False,
        )


def test_image_export_follows_the_imported_dataset(analysis, tmp_path):
    from msianalyzer.core.export import export_visual_inspection_images

    db, _src, result = _import_merged(analysis, tmp_path)
    dest = tmp_path / "images"

    n = export_visual_inspection_images(
        db, dest, "png", "obs", obs_column="leiden", rel_dir=result.rel_dir
    )

    assert n == 3  # "leiden" only exists in the imported files
    assert sorted(p.name for p in dest.iterdir()) == [f"{s}.png" for s in SAMPLES]
