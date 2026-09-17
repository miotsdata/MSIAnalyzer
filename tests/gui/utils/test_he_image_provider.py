from pathlib import Path

import numpy as np
from PIL import Image
from PySide6.QtGui import QImage

from msianalyzer.core.analysis_db import init_analysis_db, register_sample
from msianalyzer.core.registration import attach_he_image
from msianalyzer.gui.utils.he_image_provider import HEImageProvider


def _make_png(path: Path, size: tuple[int, int] = (6, 4)) -> None:
    width, height = size
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    Image.fromarray(arr).save(path, format="PNG")


def _seed_sample(tmp_path: Path, sample_name: str = "s1") -> tuple[Path, Path]:
    analysis_db_path = tmp_path / "analysis.db"
    init_analysis_db(analysis_db_path).close()
    raw_db_path = tmp_path / f"{sample_name}.db"
    register_sample(analysis_db_path, name=sample_name, raw_db_path=raw_db_path)
    return analysis_db_path, raw_db_path


def test_request_image_renders_attached_image(tmp_path):
    analysis_db_path, raw_db_path = _seed_sample(tmp_path)
    image_path = tmp_path / "slide.png"
    _make_png(image_path, size=(6, 4))
    attach_he_image(raw_db_path, image_path)

    provider = HEImageProvider()
    provider.setAnalysisDbPath(str(analysis_db_path))

    image = provider.requestImage("s1|0", None, None)

    assert not image.isNull()
    assert image.width() == 6
    assert image.height() == 4
    assert image.format() == QImage.Format.Format_RGBA8888


def test_request_image_null_when_no_analysis_set():
    provider = HEImageProvider()
    assert provider.requestImage("s1|0", None, None).isNull()


def test_request_image_null_when_sample_has_no_raw_db(tmp_path):
    analysis_db_path = tmp_path / "analysis.db"
    init_analysis_db(analysis_db_path).close()

    provider = HEImageProvider()
    provider.setAnalysisDbPath(str(analysis_db_path))

    assert provider.requestImage("nope|0", None, None).isNull()


def test_request_image_null_when_no_image_attached(tmp_path):
    analysis_db_path, _raw_db_path = _seed_sample(tmp_path)

    provider = HEImageProvider()
    provider.setAnalysisDbPath(str(analysis_db_path))

    assert provider.requestImage("s1|0", None, None).isNull()


def test_request_image_handles_percent_encoded_id(tmp_path):
    # QML's Image element percent-encodes "|" before this provider ever
    # sees the id — see HeatmapImageProvider's identical test/comment.
    analysis_db_path, raw_db_path = _seed_sample(tmp_path)
    image_path = tmp_path / "slide.png"
    _make_png(image_path)
    attach_he_image(raw_db_path, image_path)

    provider = HEImageProvider()
    provider.setAnalysisDbPath(str(analysis_db_path))

    image = provider.requestImage("s1%7C0", None, None)

    assert not image.isNull()


def test_request_image_revision_suffix_is_ignored(tmp_path):
    # The "|revision" suffix is only a QML-side cache-buster (see the
    # provider's docstring) — any value must resolve the same image.
    analysis_db_path, raw_db_path = _seed_sample(tmp_path)
    image_path = tmp_path / "slide.png"
    _make_png(image_path)
    attach_he_image(raw_db_path, image_path)

    provider = HEImageProvider()
    provider.setAnalysisDbPath(str(analysis_db_path))

    image = provider.requestImage("s1|42", None, None)

    assert not image.isNull()
