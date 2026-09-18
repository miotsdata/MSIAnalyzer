import sqlite3
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from msianalyzer.core.registration.image_registration import (
    attach_he_image,
    fit_and_save_registration,
    load_registration,
    resolve_image_path,
)


def _make_image(path: Path, size: tuple[int, int] = (40, 30), fmt: str = "PNG") -> None:
    width, height = size
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    Image.fromarray(arr).save(path, format=fmt)


@pytest.fixture
def raw_db_path(tmp_path: Path) -> Path:
    return tmp_path / "sample.db"


def test_attach_he_image_copies_file_and_records_row(tmp_path: Path, raw_db_path: Path):
    image_path = tmp_path / "slide.png"
    _make_image(image_path, size=(40, 30))

    attached = attach_he_image(raw_db_path, image_path)

    assert attached.image_format == "PNG"
    assert attached.width == 40
    assert attached.height == 30

    resolved = resolve_image_path(raw_db_path, attached)
    assert resolved.exists()
    assert resolved.read_bytes() == image_path.read_bytes()

    with sqlite3.connect(raw_db_path) as conn:
        rows = conn.execute("SELECT image_id, filename FROM registered_images").fetchall()
    assert rows == [(attached.image_id, attached.filename)]


def test_attach_he_image_rejects_unsupported_format(tmp_path: Path, raw_db_path: Path):
    image_path = tmp_path / "slide.bmp"
    _make_image(image_path, size=(10, 10), fmt="BMP")

    with pytest.raises(ValueError, match="unsupported"):
        attach_he_image(raw_db_path, image_path)


def test_attach_he_image_replaces_previous_image(tmp_path: Path, raw_db_path: Path):
    first = tmp_path / "first.png"
    _make_image(first, size=(20, 20), fmt="PNG")
    attach_he_image(raw_db_path, first)

    second = tmp_path / "second.tiff"
    _make_image(second, size=(15, 25), fmt="TIFF")
    attached2 = attach_he_image(raw_db_path, second)

    with sqlite3.connect(raw_db_path) as conn:
        rows = conn.execute("SELECT image_id FROM registered_images").fetchall()
    assert rows == [(attached2.image_id,)]

    images_dir = raw_db_path.parent / "images"
    remaining = sorted(p.name for p in images_dir.rglob("he_image.*"))
    assert remaining == [attached2.filename]


def test_attach_he_image_does_not_collide_across_samples_sharing_a_parent_dir(
    tmp_path: Path,
):
    # Regression: every sample's raw db lives in the same shared
    # `<project>/parsed/` folder (see IOConfig.raw_db_paths) —
    # attach_he_image used to write every sample's image to the same
    # fixed `images/he_image.<ext>` path, so attaching a second sample's
    # image silently overwrote the first sample's file on disk (reported
    # by the user: reopening the first sample after attaching to a
    # second showed the second sample's image).
    raw_db_1 = tmp_path / "s1.db"
    raw_db_2 = tmp_path / "s2.db"

    image_1 = tmp_path / "slide1.png"
    _make_image(image_1, size=(20, 20))
    image_2 = tmp_path / "slide2.png"
    _make_image(image_2, size=(50, 40))

    attached_1 = attach_he_image(raw_db_1, image_1)
    attached_2 = attach_he_image(raw_db_2, image_2)

    path_1 = resolve_image_path(raw_db_1, attached_1)
    path_2 = resolve_image_path(raw_db_2, attached_2)

    assert path_1 != path_2
    assert path_1.exists() and path_2.exists()
    with Image.open(path_1) as img1, Image.open(path_2) as img2:
        assert img1.size == (20, 20)
        assert img2.size == (50, 40)


def test_load_registration_none_when_db_missing(tmp_path: Path):
    assert load_registration(tmp_path / "missing.db") is None


def test_load_registration_none_when_no_image_attached(raw_db_path: Path):
    raw_db_path.touch()
    assert load_registration(raw_db_path) is None


def test_load_registration_returns_image_without_fit(tmp_path: Path, raw_db_path: Path):
    image_path = tmp_path / "slide.png"
    _make_image(image_path)
    attach_he_image(raw_db_path, image_path)

    info = load_registration(raw_db_path)

    assert info is not None
    assert info.fit is None
    assert info.image.width == 40


def test_fit_and_save_registration_round_trips(tmp_path: Path, raw_db_path: Path):
    image_path = tmp_path / "slide.png"
    _make_image(image_path)
    attached = attach_he_image(raw_db_path, image_path)

    landmarks = [
        ((0.0, 0.0), (0.0, 0.0)),
        ((10.0, 0.0), (5.0, 0.0)),
        ((0.0, 10.0), (0.0, 5.0)),
    ]

    fit = fit_and_save_registration(raw_db_path, attached.image_id, landmarks, "affine")

    assert fit.transform_type == "affine"
    assert fit.rmse == pytest.approx(0.0, abs=1e-9)

    info = load_registration(raw_db_path)
    assert info.fit is not None
    np.testing.assert_allclose(info.fit.matrix, fit.matrix)
    assert len(info.fit.landmarks) == 3
    assert info.fit.reprojection_errors == pytest.approx([0.0, 0.0, 0.0], abs=1e-9)


def test_fit_and_save_registration_rejects_too_few_landmarks(tmp_path: Path, raw_db_path: Path):
    image_path = tmp_path / "slide.png"
    _make_image(image_path)
    attached = attach_he_image(raw_db_path, image_path)

    with pytest.raises(ValueError, match="at least 3"):
        fit_and_save_registration(
            raw_db_path,
            attached.image_id,
            [((0.0, 0.0), (0.0, 0.0)), ((1.0, 0.0), (1.0, 0.0))],
            "affine",
        )


def test_fit_and_save_registration_rejects_unknown_image_id(raw_db_path: Path):
    with pytest.raises(ValueError, match="no attached image"):
        fit_and_save_registration(
            raw_db_path,
            999,
            [
                ((0.0, 0.0), (0.0, 0.0)),
                ((1.0, 0.0), (1.0, 0.0)),
                ((0.0, 1.0), (0.0, 1.0)),
            ],
            "affine",
        )


def test_fit_and_save_registration_rejects_unknown_transform_type(
    tmp_path: Path, raw_db_path: Path
):
    image_path = tmp_path / "slide.png"
    _make_image(image_path)
    attached = attach_he_image(raw_db_path, image_path)

    with pytest.raises(ValueError, match="transform_type"):
        fit_and_save_registration(
            raw_db_path,
            attached.image_id,
            [
                ((0.0, 0.0), (0.0, 0.0)),
                ((1.0, 0.0), (1.0, 0.0)),
                ((0.0, 1.0), (0.0, 1.0)),
            ],
            "projective",
        )


def test_fit_and_save_registration_replaces_previous_fit(tmp_path: Path, raw_db_path: Path):
    image_path = tmp_path / "slide.png"
    _make_image(image_path)
    attached = attach_he_image(raw_db_path, image_path)

    landmarks_v1 = [
        ((0.0, 0.0), (0.0, 0.0)),
        ((10.0, 0.0), (5.0, 0.0)),
        ((0.0, 10.0), (0.0, 5.0)),
    ]
    fit_and_save_registration(raw_db_path, attached.image_id, landmarks_v1, "affine")

    landmarks_v2 = [
        ((0.0, 0.0), (0.0, 0.0)),
        ((10.0, 0.0), (2.0, 0.0)),
        ((0.0, 10.0), (0.0, 2.0)),
    ]
    fit_and_save_registration(raw_db_path, attached.image_id, landmarks_v2, "affine")

    with sqlite3.connect(raw_db_path) as conn:
        landmark_count = conn.execute(
            "SELECT COUNT(*) FROM registration_landmarks WHERE image_id = ?",
            (attached.image_id,),
        ).fetchone()[0]
        reg_count = conn.execute(
            "SELECT COUNT(*) FROM image_registrations WHERE image_id = ?",
            (attached.image_id,),
        ).fetchone()[0]
    assert landmark_count == 3
    assert reg_count == 1

    info = load_registration(raw_db_path)
    assert info.fit.matrix[0, 0] == pytest.approx(0.2, abs=1e-9)
