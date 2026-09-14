import anndata as ad
import numpy as np
import pandas as pd
import pytest
from scipy.sparse import csr_matrix

from msianalyzer.core.plotting.roi import (
    delete_roi_from_sample,
    load_sample_rois,
    polygon_pixel_mask,
    roi_obs_column,
    save_roi_to_sample,
    sync_roi_deletion_to_merged,
    sync_roi_to_merged,
)


def _make_grid_adata(n=4):
    """An n x n spatial grid, one feature, obs index "px0".."px{n*n-1}" in
    (x fastest-varying) row-major order — grid-index (col, row) for pixel
    (x, y) is exactly (x, y) since coordinates are already 0..n-1 integers."""
    coords = [(x, y) for y in range(n) for x in range(n)]
    obs = pd.DataFrame(index=[f"px{i}" for i in range(len(coords))])
    var = pd.DataFrame({"mz": [100.0]}, index=["mz_100.0000"])
    X = csr_matrix(np.arange(len(coords), dtype=np.float32).reshape(-1, 1))
    adata = ad.AnnData(X=X, obs=obs, var=var)
    adata.obsm["spatial"] = np.array(coords, dtype=float)
    return adata


def _write_sample_h5ad(path, n=4):
    adata = _make_grid_adata(n)
    adata.write_h5ad(path)
    return adata


def test_polygon_pixel_mask_selects_expected_pixels():
    # A 4x4 grid; a square spanning grid-index 1..3 covers pixel-CENTERS
    # (1.5,1.5), (2.5,1.5), (1.5,2.5), (2.5,2.5) -> the 4 pixels at
    # (x,y) in {1,2}x{1,2}, and no others.
    adata = _make_grid_adata(4)
    vertices = [[1, 1], [3, 1], [3, 3], [1, 3]]

    mask = polygon_pixel_mask(adata, vertices)

    coords = adata.obsm["spatial"]
    expected = np.array(
        [(x in (1, 2) and y in (1, 2)) for x, y in coords], dtype=bool
    )
    np.testing.assert_array_equal(mask, expected)
    assert mask.sum() == 4


def test_polygon_pixel_mask_fewer_than_3_vertices_is_all_false():
    adata = _make_grid_adata(4)
    assert not polygon_pixel_mask(adata, [[0, 0], [1, 1]]).any()


def test_roi_obs_column_naming():
    assert roi_obs_column("liver") == "roi_liver"


def test_save_roi_to_sample_writes_obs_and_uns_and_roundtrips(tmp_path):
    path = tmp_path / "s1.h5ad"
    _write_sample_h5ad(path)

    pixel_count = save_roi_to_sample(
        path, "liver", "#ff0000", [[1, 1], [3, 1], [3, 3], [1, 3]]
    )

    assert pixel_count == 4

    reread = ad.read_h5ad(path)
    assert reread.obs["roi_liver"].sum() == 4
    assert reread.uns["rois"]["liver"]["color"] == "#ff0000"
    # h5ad round-trips a uns list-of-lists as a numpy array.
    np.testing.assert_array_equal(
        reread.uns["rois"]["liver"]["vertices"],
        [[1.0, 1.0], [3.0, 1.0], [3.0, 3.0], [1.0, 3.0]],
    )


def test_save_roi_to_sample_rejects_fewer_than_3_vertices(tmp_path):
    path = tmp_path / "s1.h5ad"
    _write_sample_h5ad(path)

    with pytest.raises(ValueError):
        save_roi_to_sample(path, "liver", "#ff0000", [[0, 0], [1, 1]])


def test_save_roi_to_sample_preserves_other_rois(tmp_path):
    path = tmp_path / "s1.h5ad"
    _write_sample_h5ad(path)

    save_roi_to_sample(path, "liver", "#ff0000", [[0, 0], [2, 0], [2, 2], [0, 2]])
    save_roi_to_sample(path, "kidney", "#00ff00", [[1, 1], [3, 1], [3, 3], [1, 3]])

    reread = ad.read_h5ad(path)
    assert set(reread.uns["rois"].keys()) == {"liver", "kidney"}
    assert "roi_liver" in reread.obs.columns
    assert "roi_kidney" in reread.obs.columns


def test_delete_roi_from_sample_removes_obs_and_uns(tmp_path):
    path = tmp_path / "s1.h5ad"
    _write_sample_h5ad(path)
    save_roi_to_sample(path, "liver", "#ff0000", [[1, 1], [3, 1], [3, 3], [1, 3]])

    removed = delete_roi_from_sample(path, "liver")

    assert removed is True
    reread = ad.read_h5ad(path)
    assert "roi_liver" not in reread.obs.columns
    assert "liver" not in (reread.uns.get("rois") or {})


def test_delete_roi_from_sample_is_idempotent_no_op(tmp_path):
    path = tmp_path / "s1.h5ad"
    _write_sample_h5ad(path)

    assert delete_roi_from_sample(path, "never_existed") is False


def test_load_sample_rois_reads_back_saved_roi(tmp_path):
    path = tmp_path / "s1.h5ad"
    _write_sample_h5ad(path)
    save_roi_to_sample(path, "liver", "#ff0000", [[1, 1], [3, 1], [3, 3], [1, 3]])

    rois = load_sample_rois(path)

    assert rois["liver"]["color"] == "#ff0000"


def test_load_sample_rois_empty_when_none_saved(tmp_path):
    path = tmp_path / "s1.h5ad"
    _write_sample_h5ad(path)

    assert load_sample_rois(path) == {}


def _write_merged_h5ad(path, sample_names=("s1", "s2"), n=4):
    parts = {}
    for name in sample_names:
        parts[name] = _make_grid_adata(n)
    merged = ad.concat(parts, label="sample", index_unique="-", merge="same")
    merged.write_h5ad(path)
    return merged


def test_sync_roi_to_merged_no_op_when_file_missing(tmp_path):
    missing = tmp_path / "merged.h5ad"
    assert sync_roi_to_merged(
        missing, "s1", "liver", "#ff0000", [[1, 1], [3, 1], [3, 3], [1, 3]],
        np.zeros(16, dtype=bool),
    ) is False


def test_sync_roi_to_merged_patches_only_target_sample_rows(tmp_path):
    merged_path = tmp_path / "merged.h5ad"
    _write_merged_h5ad(merged_path, sample_names=("s1", "s2"))

    vertices = [[1, 1], [3, 1], [3, 3], [1, 3]]
    sample_adata = _make_grid_adata(4)
    mask = polygon_pixel_mask(sample_adata, vertices)

    ok = sync_roi_to_merged(merged_path, "s1", "liver", "#ff0000", vertices, mask)

    assert ok is True
    reread = ad.read_h5ad(merged_path)
    column = reread.obs["roi_liver"]
    s1_rows = reread.obs["sample"] == "s1"
    s2_rows = reread.obs["sample"] == "s2"
    np.testing.assert_array_equal(column[s1_rows].to_numpy(), mask)
    assert not column[s2_rows].any()
    assert reread.uns["rois"]["liver"]["color"] == "#ff0000"
    np.testing.assert_array_equal(
        reread.uns["rois"]["liver"]["samples"]["s1"]["vertices"],
        [[1.0, 1.0], [3.0, 1.0], [3.0, 3.0], [1.0, 3.0]],
    )
    assert "s2" not in reread.uns["rois"]["liver"]["samples"]


def test_sync_roi_to_merged_second_sample_keeps_first(tmp_path):
    merged_path = tmp_path / "merged.h5ad"
    _write_merged_h5ad(merged_path, sample_names=("s1", "s2"))
    vertices = [[1, 1], [3, 1], [3, 3], [1, 3]]
    sample_adata = _make_grid_adata(4)
    mask = polygon_pixel_mask(sample_adata, vertices)

    sync_roi_to_merged(merged_path, "s1", "liver", "#ff0000", vertices, mask)
    sync_roi_to_merged(merged_path, "s2", "liver", "#ff0000", vertices, mask)

    reread = ad.read_h5ad(merged_path)
    assert set(reread.uns["rois"]["liver"]["samples"].keys()) == {"s1", "s2"}
    assert reread.obs["roi_liver"].sum() == 8  # 4 pixels x 2 samples


def test_sync_roi_deletion_to_merged_no_op_when_file_missing(tmp_path):
    missing = tmp_path / "merged.h5ad"
    assert sync_roi_deletion_to_merged(missing, "s1", "liver") is False


def test_sync_roi_deletion_to_merged_clears_sample_and_drops_when_empty(tmp_path):
    merged_path = tmp_path / "merged.h5ad"
    _write_merged_h5ad(merged_path, sample_names=("s1", "s2"))
    vertices = [[1, 1], [3, 1], [3, 3], [1, 3]]
    sample_adata = _make_grid_adata(4)
    mask = polygon_pixel_mask(sample_adata, vertices)
    sync_roi_to_merged(merged_path, "s1", "liver", "#ff0000", vertices, mask)

    removed = sync_roi_deletion_to_merged(merged_path, "s1", "liver")

    assert removed is True
    reread = ad.read_h5ad(merged_path)
    # Only sample with this ROI was s1 -> the whole entry/column is gone.
    assert "liver" not in (reread.uns.get("rois") or {})
    assert "roi_liver" not in reread.obs.columns


def test_sync_roi_deletion_to_merged_keeps_other_samples(tmp_path):
    merged_path = tmp_path / "merged.h5ad"
    _write_merged_h5ad(merged_path, sample_names=("s1", "s2"))
    vertices = [[1, 1], [3, 1], [3, 3], [1, 3]]
    sample_adata = _make_grid_adata(4)
    mask = polygon_pixel_mask(sample_adata, vertices)
    sync_roi_to_merged(merged_path, "s1", "liver", "#ff0000", vertices, mask)
    sync_roi_to_merged(merged_path, "s2", "liver", "#ff0000", vertices, mask)

    sync_roi_deletion_to_merged(merged_path, "s1", "liver")

    reread = ad.read_h5ad(merged_path)
    assert set(reread.uns["rois"]["liver"]["samples"].keys()) == {"s2"}
    assert reread.obs.loc[reread.obs["sample"] == "s1", "roi_liver"].sum() == 0
    assert reread.obs.loc[reread.obs["sample"] == "s2", "roi_liver"].sum() == 4
