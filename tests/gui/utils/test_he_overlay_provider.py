from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
from PIL import Image
from PySide6.QtGui import QImage
from scipy.sparse import csr_matrix

from msianalyzer.core.analysis_db import init_analysis_db, register_sample
from msianalyzer.core.registration import attach_he_image, fit_and_save_registration
from msianalyzer.gui.utils.he_overlay_provider import HEOverlayImageProvider


def _make_png(path: Path, size: tuple[int, int] = (8, 8)) -> None:
    width, height = size
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    Image.fromarray(arr).save(path, format="PNG")


def _write_sample_h5ad(path: Path) -> None:
    coords = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)]
    obs = pd.DataFrame(index=[f"px{i}" for i in range(4)])
    var = pd.DataFrame({"mz": [100.0]}, index=["mz_100.0000"])
    X = csr_matrix(np.array([0.0, 10.0, 5.0, 15.0], dtype=np.float32).reshape(-1, 1))
    adata = ad.AnnData(X=X, obs=obs, var=var)
    adata.obsm["spatial"] = np.array(coords, dtype=float)
    adata.write_h5ad(path)


def _seed_fully_registered_sample(tmp_path: Path, sample_name: str = "s1"):
    analysis_db_path = tmp_path / "analysis.db"
    init_analysis_db(analysis_db_path).close()
    raw_db_path = tmp_path / f"{sample_name}.db"
    register_sample(analysis_db_path, name=sample_name, raw_db_path=raw_db_path)
    _write_sample_h5ad(tmp_path / f"{sample_name}.h5ad")

    image_path = tmp_path / "slide.png"
    _make_png(image_path, size=(8, 8))
    attached = attach_he_image(raw_db_path, image_path)
    fit_and_save_registration(
        raw_db_path,
        attached.image_id,
        [
            ((0.0, 0.0), (0.0, 0.0)),
            ((4.0, 0.0), (2.0, 0.0)),
            ((0.0, 4.0), (0.0, 2.0)),
        ],
        "affine",
    )
    return analysis_db_path, raw_db_path


def test_request_image_warps_heatmap_to_he_dimensions(tmp_path):
    analysis_db_path, _raw_db_path = _seed_fully_registered_sample(tmp_path)

    provider = HEOverlayImageProvider()
    provider.setAnalysisDbPath(str(analysis_db_path))

    image = provider.requestImage("s1|100.0|raw|gray|0|15", None, None)

    assert not image.isNull()
    assert image.width() == 8
    assert image.height() == 8
    assert image.format() == QImage.Format.Format_RGBA8888


def test_request_image_null_when_no_analysis_set():
    provider = HEOverlayImageProvider()
    assert provider.requestImage("s1|100.0|raw|gray|0|15", None, None).isNull()


def test_request_image_null_when_no_registration_fit(tmp_path):
    analysis_db_path = tmp_path / "analysis.db"
    init_analysis_db(analysis_db_path).close()
    raw_db_path = tmp_path / "s1.db"
    register_sample(analysis_db_path, name="s1", raw_db_path=raw_db_path)
    _write_sample_h5ad(tmp_path / "s1.h5ad")
    image_path = tmp_path / "slide.png"
    _make_png(image_path)
    attach_he_image(raw_db_path, image_path)  # attached, but never fitted

    provider = HEOverlayImageProvider()
    provider.setAnalysisDbPath(str(analysis_db_path))

    assert provider.requestImage("s1|100.0|raw|gray|0|15", None, None).isNull()


def test_request_image_null_when_sample_has_no_h5ad(tmp_path):
    analysis_db_path = tmp_path / "analysis.db"
    init_analysis_db(analysis_db_path).close()
    raw_db_path = tmp_path / "s1.db"
    register_sample(analysis_db_path, name="s1", raw_db_path=raw_db_path)
    image_path = tmp_path / "slide.png"
    _make_png(image_path)
    attached = attach_he_image(raw_db_path, image_path)
    fit_and_save_registration(
        raw_db_path,
        attached.image_id,
        [((0.0, 0.0), (0.0, 0.0)), ((4.0, 0.0), (2.0, 0.0)), ((0.0, 4.0), (0.0, 2.0))],
        "affine",
    )
    # No .h5ad written for s1.

    provider = HEOverlayImageProvider()
    provider.setAnalysisDbPath(str(analysis_db_path))

    assert provider.requestImage("s1|100.0|raw|gray|0|15", None, None).isNull()


def test_request_image_handles_obs_target(tmp_path):
    analysis_db_path, _raw_db_path = _seed_fully_registered_sample(tmp_path)
    adata = ad.read_h5ad(tmp_path / "s1.h5ad")
    adata.obs["tic"] = [0.0, 10.0, 5.0, 15.0]
    adata.write_h5ad(tmp_path / "s1.h5ad")

    provider = HEOverlayImageProvider()
    provider.setAnalysisDbPath(str(analysis_db_path))

    image = provider.requestImage("s1|obs:tic|gray|0|15", None, None)

    assert not image.isNull()
    assert image.width() == 8
    assert image.height() == 8
