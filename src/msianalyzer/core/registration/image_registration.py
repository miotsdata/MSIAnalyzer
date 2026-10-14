"""
image_registration.py
Attach a brightfield/H&E microscopy image to a sample's raw per-sample
database and fit/store a coordinate transform mapping that image's pixel
space onto the MSI pixel-grid-index space (see
`core/plotting/heatmap.py::pixel_grid_indices`) from user-picked landmark
pairs.

Lives in the RAW per-sample database (alongside `spatial_pixels`, see
`core/utils/spectra_pixels_association.py`), not in any per-analysis
`.h5ad` — the pixel grid, and therefore this registration, is identical
across every re-analysis of a sample regardless of annotation/library/
scoring settings, so it's registered once and reused, unlike ROIs
(`core/plotting/roi.py`) which are per-analysis.

At most one attached image (and its registration) exists per sample at a
time: re-attaching replaces the previous image file and every row below.
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

from msianalyzer.core.registration.transform import (
    Landmark,
    RegistrationFit,
    fit_affine_transform,
    fit_similarity_transform,
    reprojection_errors,
)
from msianalyzer.core.utils.db import safe_execute, safe_executemany
from msianalyzer.core.utils.logging_utils import log_call

logger = logging.getLogger(__name__)

__all__ = [
    "AttachedImage",
    "RegistrationInfo",
    "attach_he_image",
    "fit_and_save_registration",
    "load_registration",
    "resolve_image_path",
]

IMAGES_SUBDIR = "images"
SUPPORTED_FORMATS = {"PNG", "JPEG", "TIFF"}
# Longest side an attached image is downsampled to. MSI pixels are tens of
# µm, so full whole-slide resolution adds nothing to landmark picking and
# only costs memory in `he_image_provider` (one RGBA QImage per render).
MAX_IMAGE_SIDE = 5000
# Pillow's decompression-bomb guard errors at 2x this. Its default (~89 MP)
# rejects ordinary slide exports; this lets ~1 GP through.
# ponytail: whole-image decode; past ~1 GP the user must export a lower
# pyramid level (a tiled/pyramid reader would lift this ceiling).
_MAX_DECODE_PIXELS = 500_000_000
# Page modes a channel-split TIFF (e.g. an ImageJ/Bio-Formats composite
# saved from a CZI) stores each colour channel in.
_CHANNEL_PAGE_MODES = {"L", "P", "I", "I;16", "I;16B", "I;16L", "F"}
_FIT_FUNCS = {"affine": fit_affine_transform, "similarity": fit_similarity_transform}
_MIN_LANDMARKS = {"affine": 3, "similarity": 2}

_SCHEMA = """
    CREATE TABLE IF NOT EXISTS registered_images (
        image_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        filename     TEXT NOT NULL,
        image_format TEXT NOT NULL,
        width        INTEGER NOT NULL,
        height       INTEGER NOT NULL,
        attached_at  TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS image_registrations (
        image_id       INTEGER PRIMARY KEY,
        transform_type TEXT NOT NULL,
        matrix         TEXT NOT NULL,
        fitted_at      TEXT NOT NULL,
        FOREIGN KEY (image_id) REFERENCES registered_images(image_id)
    );

    CREATE TABLE IF NOT EXISTS registration_landmarks (
        landmark_id  INTEGER PRIMARY KEY AUTOINCREMENT,
        image_id     INTEGER NOT NULL,
        he_x         REAL NOT NULL,
        he_y         REAL NOT NULL,
        grid_x       REAL NOT NULL,
        grid_y       REAL NOT NULL,
        reproj_error REAL,
        FOREIGN KEY (image_id) REFERENCES registered_images(image_id)
    );
"""


@dataclass(frozen=True)
class AttachedImage:
    """One sample's attached H&E/brightfield image, as recorded in its raw
    database's `registered_images` table."""

    image_id: int
    filename: str  # relative to `resolve_image_path`'s images subdir
    image_format: str
    width: int
    height: int


@dataclass(frozen=True)
class RegistrationInfo:
    """An attached image plus its fitted registration, if any has been
    saved yet (`fit` is None right after `attach_he_image`, before the
    user has placed landmarks and called `fit_and_save_registration`)."""

    image: AttachedImage
    fit: RegistrationFit | None


def _images_dir(raw_db_path: Path) -> Path:
    """Where one sample's attached image lives on disk.

    Namespaced by the raw database's own filename stem, not just
    `raw_db_path.parent / IMAGES_SUBDIR` — every sample's raw database
    lives in the same shared `<project_folder>/parsed/` directory
    (differentiated only by filename, see `IOConfig.raw_db_paths`), so
    without this every sample's fixed `he_image.<ext>` filename collided
    in one shared `images/` folder: attaching a second sample's image
    silently overwrote the first sample's file on disk (both samples'
    own DB rows stayed correct — this was a real, reported bug, not just
    a display/cache issue).
    """
    return raw_db_path.parent / IMAGES_SUBDIR / raw_db_path.stem


def _is_channel_split(img: Image.Image) -> bool:
    """A 3-page TIFF of same-size single-channel pages — colour stored as
    separate R/G/B planes instead of interleaved RGB, which Pillow alone
    would show as page 1 in grayscale."""
    if img.format != "TIFF" or getattr(img, "n_frames", 1) != 3:
        return False
    sizes, modes = set(), set()
    for i in range(3):
        img.seek(i)
        sizes.add(img.size)
        modes.add(img.mode)
    img.seek(0)
    return len(sizes) == 1 and modes <= _CHANNEL_PAGE_MODES


def _fit_size(size: tuple[int, int]) -> tuple[int, int]:
    """`size` scaled to fit `MAX_IMAGE_SIDE`, aspect ratio kept."""
    scale = min(1.0, MAX_IMAGE_SIDE / max(size))
    return max(1, round(size[0] * scale)), max(1, round(size[1] * scale))


def _merge_channel_pages(img: Image.Image) -> Image.Image:
    """Merge a channel-split TIFF's 3 pages into one 8-bit RGB image,
    downsampled to `MAX_IMAGE_SIDE` per page before stacking (keeps peak
    memory at one full-size page). >8-bit data is scaled by one shared
    factor so the channels' relative balance (the H&E colour) survives."""
    target = _fit_size(img.size)
    channels = []
    for i in range(3):
        img.seek(i)
        page = Image.fromarray(np.asarray(img).astype(np.float32), mode="F")
        if page.size != target:
            page = page.resize(target, Image.Resampling.LANCZOS)
        channels.append(np.asarray(page))
    rgb = np.clip(np.stack(channels, axis=-1), 0, None)
    peak = float(rgb.max())
    if peak > 255:
        rgb *= 255.0 / peak
    return Image.fromarray(np.clip(np.rint(rgb), 0, 255).astype(np.uint8), mode="RGB")


def resolve_image_path(raw_db_path: Path | str, image: AttachedImage) -> Path:
    """Full path to an attached image's file on disk, given the raw
    database it's attached to."""
    return _images_dir(Path(raw_db_path)) / image.filename


@log_call(source="raw_db_path")
def attach_he_image(raw_db_path: Path | str, image_path: Path | str) -> AttachedImage:
    """Copy `image_path` into the sample's raw data folder and record it as
    that sample's H&E/brightfield image, replacing any previously attached
    image (and its registration/landmarks, which no longer apply to a
    different image).

    A channel-split TIFF (3 single-channel pages, as ImageJ saves a CZI
    composite) is merged into RGB, and anything longer than
    `MAX_IMAGE_SIDE` is downsampled; either way the stored copy is a PNG
    and `width`/`height` (and so every landmark) refer to that copy, not
    the original file. Images needing neither are copied byte-for-byte.

    Args:
        raw_db_path: The sample's raw per-sample SQLite database (same file
            `map_pixels_to_db` writes `spatial_pixels` into).
        image_path: A PNG/JPEG/TIFF image file to attach.

    Returns:
        The newly attached image's record.

    Raises:
        ValueError: `image_path` isn't a PNG/JPEG/TIFF file Pillow can read,
            or is too large to decode (see `_MAX_DECODE_PIXELS`).
    """
    raw_db_path = Path(raw_db_path)
    image_path = Path(image_path)

    default_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = _MAX_DECODE_PIXELS
    try:
        with Image.open(image_path) as img:
            img_format = img.format
            if img_format not in SUPPORTED_FORMATS:
                raise ValueError(
                    f"{image_path}: unsupported image format {img_format!r}, "
                    f"expected one of {sorted(SUPPORTED_FORMATS)}"
                )
            converted = None
            if _is_channel_split(img):
                converted = _merge_channel_pages(img)
            elif max(img.size) > MAX_IMAGE_SIDE:
                converted = img.resize(_fit_size(img.size), Image.Resampling.LANCZOS)
            width, height = (converted or img).size
    except Image.DecompressionBombError as e:
        raise ValueError(
            f"{image_path}: image too large to load ({e}). Export a lower "
            "resolution (e.g. a smaller pyramid level in Bio-Formats)."
        ) from e
    finally:
        Image.MAX_IMAGE_PIXELS = default_limit

    images_dir = _images_dir(raw_db_path)
    images_dir.mkdir(parents=True, exist_ok=True)
    for old_file in images_dir.glob("he_image.*"):
        old_file.unlink()
    if converted is None:
        filename = f"he_image{image_path.suffix.lower()}"
        shutil.copyfile(image_path, images_dir / filename)
    else:
        img_format = "PNG"
        filename = "he_image.png"
        converted.save(images_dir / filename, format="PNG")
        logger.info(
            "Converted %s for display: stored %dx%d RGB PNG",
            image_path,
            width,
            height,
            extra={"source_file": raw_db_path},
        )

    attached_at = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(raw_db_path) as conn:
        conn.executescript(_SCHEMA)
        conn.execute("DELETE FROM registration_landmarks")
        conn.execute("DELETE FROM image_registrations")
        conn.execute("DELETE FROM registered_images")
        cursor = safe_execute(
            conn,
            """
            INSERT INTO registered_images
                (filename, image_format, width, height, attached_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (filename, img_format, width, height, attached_at),
            table="registered_images",
            logger=logger,
            source=raw_db_path,
        )
        image_id = cursor.lastrowid
        conn.commit()

    logger.info(
        "Attached %s image (%dx%d) to %s",
        img_format,
        width,
        height,
        raw_db_path,
        extra={"source_file": raw_db_path},
    )
    return AttachedImage(
        image_id=image_id,
        filename=filename,
        image_format=img_format,
        width=width,
        height=height,
    )


@log_call(source="raw_db_path")
def fit_and_save_registration(
    raw_db_path: Path | str,
    image_id: int,
    landmarks: list[tuple[tuple[float, float], tuple[float, float]]],
    transform_type: str = "affine",
) -> RegistrationFit:
    """Fit a transform from user-picked landmark pairs and save it (plus
    the landmarks and each one's reprojection error) to the raw database,
    replacing any previous registration for `image_id`.

    Args:
        raw_db_path: The sample's raw per-sample SQLite database.
        image_id: An `AttachedImage.image_id` already attached via
            `attach_he_image`.
        landmarks: `[((he_x, he_y), (grid_x, grid_y)), ...]` — H&E pixel
            coordinates paired with the corresponding MSI
            pixel-grid-index coordinates (see
            `core/plotting/heatmap.py::pixel_grid_indices`).
        transform_type: `"affine"` (needs >= 3 points) or `"similarity"`
            (needs >= 2 points).

    Returns:
        The fitted `RegistrationFit`, including per-landmark reprojection
        error for the caller to surface as alignment-quality feedback.

    Raises:
        ValueError: Unknown `transform_type`, too few landmarks for it, or
            `image_id` has no matching row in `registered_images`.
    """
    if transform_type not in _FIT_FUNCS:
        raise ValueError(
            f"transform_type must be one of {sorted(_FIT_FUNCS)}, got {transform_type!r}"
        )
    min_points = _MIN_LANDMARKS[transform_type]
    if len(landmarks) < min_points:
        raise ValueError(
            f"{transform_type} registration needs at least {min_points} "
            f"landmark pairs, got {len(landmarks)}"
        )

    he_points = np.array([he for he, _grid in landmarks], dtype=float)
    grid_points = np.array([grid for _he, grid in landmarks], dtype=float)
    matrix = _FIT_FUNCS[transform_type](he_points, grid_points)
    errors = reprojection_errors(matrix, he_points, grid_points)
    rmse = float(np.sqrt(np.mean(errors**2)))

    fit = RegistrationFit(
        transform_type=transform_type,
        matrix=matrix,
        landmarks=[
            Landmark(he_x=he[0], he_y=he[1], grid_x=grid[0], grid_y=grid[1])
            for he, grid in landmarks
        ],
        reprojection_errors=[float(e) for e in errors],
        rmse=rmse,
    )

    fitted_at = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(raw_db_path) as conn:
        conn.executescript(_SCHEMA)
        exists = conn.execute(
            "SELECT 1 FROM registered_images WHERE image_id = ?", (image_id,)
        ).fetchone()
        if exists is None:
            raise ValueError(f"no attached image with image_id={image_id} in {raw_db_path}")

        conn.execute("DELETE FROM registration_landmarks WHERE image_id = ?", (image_id,))
        conn.execute("DELETE FROM image_registrations WHERE image_id = ?", (image_id,))

        safe_executemany(
            conn,
            """
            INSERT INTO registration_landmarks
                (image_id, he_x, he_y, grid_x, grid_y, reproj_error)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (image_id, lm.he_x, lm.he_y, lm.grid_x, lm.grid_y, err)
                for lm, err in zip(fit.landmarks, fit.reprojection_errors)
            ],
            table="registration_landmarks",
            logger=logger,
            source=raw_db_path,
        )
        safe_execute(
            conn,
            """
            INSERT INTO image_registrations (image_id, transform_type, matrix, fitted_at)
            VALUES (?, ?, ?, ?)
            """,
            (image_id, transform_type, json.dumps(matrix.tolist()), fitted_at),
            table="image_registrations",
            logger=logger,
            source=raw_db_path,
        )
        conn.commit()

    logger.info(
        "Fitted %s registration for image_id=%d in %s (rmse=%.3f grid units)",
        transform_type,
        image_id,
        raw_db_path,
        rmse,
        extra={"source_file": raw_db_path},
    )
    return fit


def load_registration(raw_db_path: Path | str) -> RegistrationInfo | None:
    """The sample's currently attached image and its fitted registration,
    if any.

    Returns:
        `None` if the raw database doesn't exist, predates this feature
        (no `registered_images` table), or has no image attached.
        Otherwise a `RegistrationInfo` whose `.fit` is `None` if the image
        was attached but no landmarks have been fitted/saved yet.
    """
    raw_db_path = Path(raw_db_path)
    if not raw_db_path.exists():
        return None

    with sqlite3.connect(f"file:{raw_db_path}?mode=ro", uri=True) as conn:
        try:
            row = conn.execute(
                """
                SELECT image_id, filename, image_format, width, height
                FROM registered_images
                ORDER BY image_id DESC LIMIT 1
                """
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        if row is None:
            return None

        image = AttachedImage(
            image_id=row[0], filename=row[1], image_format=row[2], width=row[3], height=row[4],
        )

        reg_row = conn.execute(
            "SELECT transform_type, matrix FROM image_registrations WHERE image_id = ?",
            (image.image_id,),
        ).fetchone()
        if reg_row is None:
            return RegistrationInfo(image=image, fit=None)

        transform_type, matrix_json = reg_row
        matrix = np.array(json.loads(matrix_json), dtype=float)

        landmark_rows = conn.execute(
            """
            SELECT he_x, he_y, grid_x, grid_y, reproj_error
            FROM registration_landmarks
            WHERE image_id = ?
            ORDER BY landmark_id
            """,
            (image.image_id,),
        ).fetchall()

    landmarks = [Landmark(he_x=r[0], he_y=r[1], grid_x=r[2], grid_y=r[3]) for r in landmark_rows]
    errors = [r[4] for r in landmark_rows]
    rmse = float(np.sqrt(np.mean(np.array(errors) ** 2))) if errors else 0.0

    fit = RegistrationFit(
        transform_type=transform_type,
        matrix=matrix,
        landmarks=landmarks,
        reprojection_errors=errors,
        rmse=rmse,
    )
    return RegistrationInfo(image=image, fit=fit)
