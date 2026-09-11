from unittest.mock import patch

import anndata as ad
import numpy as np
import pandas as pd
from PySide6.QtGui import QImage
from scipy.sparse import csr_matrix

from msianalyzer.gui.utils.heatmap_provider import HeatmapImageProvider


def _write_sample_h5ad(path):
    obs = pd.DataFrame(index=["a", "b", "c", "d"])
    var = pd.DataFrame({"mz": [100.0]}, index=["mz_100.0000"])
    X = csr_matrix(np.array([0.0, 10.0, 5.0, 15.0], dtype=np.float32).reshape(-1, 1))
    adata = ad.AnnData(X=X, obs=obs, var=var)
    adata.obsm["spatial"] = np.array(
        [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)], dtype=float
    )
    adata.write_h5ad(path)


def test_request_image_renders_valid_heatmap(tmp_path):
    _write_sample_h5ad(tmp_path / "s1.h5ad")
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))

    image = provider.requestImage("s1|100.0|raw|viridis|0|15", None, None)

    assert not image.isNull()
    assert image.width() == 2
    assert image.height() == 2
    assert image.format() == QImage.Format.Format_RGBA8888


def test_request_image_auto_scale_matches_render_feature_heatmap(tmp_path):
    _write_sample_h5ad(tmp_path / "s1.h5ad")
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))

    image = provider.requestImage("s1|100.0|raw|viridis|auto|auto", None, None)

    from msianalyzer.core.plotting.heatmap import render_feature_heatmap

    adata = ad.read_h5ad(tmp_path / "s1.h5ad")
    expected = render_feature_heatmap(adata, 100.0, layer="raw", colormap="viridis")

    assert image.width() == expected.shape[1]
    assert image.height() == expected.shape[0]
    assert tuple(image.pixelColor(0, 0).getRgb()) == tuple(int(c) for c in expected[0, 0])


def test_request_image_null_without_analysis_db_path():
    provider = HeatmapImageProvider()
    image = provider.requestImage("s1|100.0|raw|viridis|auto|auto", None, None)
    assert image.isNull()


def test_request_image_null_for_missing_sample(tmp_path):
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))

    image = provider.requestImage("does_not_exist|100.0|raw|viridis|auto|auto", None, None)

    assert image.isNull()


def test_request_image_null_for_malformed_id(tmp_path):
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))

    assert provider.requestImage("not-enough-parts", None, None).isNull()


def test_request_image_reuses_cached_adata(tmp_path):
    _write_sample_h5ad(tmp_path / "s1.h5ad")
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))

    with patch("msianalyzer.gui.utils.heatmap_provider.ad.read_h5ad", wraps=ad.read_h5ad) as spy:
        provider.requestImage("s1|100.0|raw|viridis|0|15", None, None)
        provider.requestImage("s1|100.0|raw|magma|0|15", None, None)
        assert spy.call_count == 1


def test_set_analysis_db_path_clears_cache_on_change(tmp_path):
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    _write_sample_h5ad(dir_a / "s1.h5ad")
    _write_sample_h5ad(dir_b / "s1.h5ad")

    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(dir_a / "analysis.db"))

    with patch("msianalyzer.gui.utils.heatmap_provider.ad.read_h5ad", wraps=ad.read_h5ad) as spy:
        provider.requestImage("s1|100.0|raw|viridis|0|15", None, None)
        assert spy.call_count == 1

        # Same path again -> cache hit, no re-read.
        provider.setAnalysisDbPath(str(dir_a / "analysis.db"))
        provider.requestImage("s1|100.0|raw|viridis|0|15", None, None)
        assert spy.call_count == 1

        # Different analysis -> cache cleared, same sample *name* re-reads
        # from the new location rather than returning dir_a's stale data.
        provider.setAnalysisDbPath(str(dir_b / "analysis.db"))
        image = provider.requestImage("s1|100.0|raw|viridis|0|15", None, None)
        assert spy.call_count == 2
        assert not image.isNull()
