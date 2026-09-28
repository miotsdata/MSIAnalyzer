from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pytest

from msianalyzer.core.parser.mzml_parser import array_to_blob
from msianalyzer.core.utils.create_adata import _fetch_scan_rows, create_spatial_adata


def _build_raw_db(db_path: Path, pixels: list[tuple]) -> None:
    """Build a minimal raw database with the real parser/pixel-mapper schema.

    Args:
        pixels: One `(x, y, mz_array, intensity_array, tic)` per pixel —
            each gets exactly one MS1 scan.
    """
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE ms1_scans (
                scan_id INTEGER PRIMARY KEY,
                rt REAL NOT NULL,
                n_peaks INTEGER,
                mz_min REAL,
                mz_max REAL,
                tic REAL,
                polarity TEXT,
                mz_array BLOB NOT NULL,
                intensity_array BLOB NOT NULL
            );
            CREATE TABLE spatial_pixels (
                pixel_id INTEGER PRIMARY KEY AUTOINCREMENT,
                x INTEGER NOT NULL,
                y INTEGER NOT NULL,
                t_start REAL NOT NULL,
                t_end REAL NOT NULL
            );
            CREATE TABLE pixel_ms1_scans (
                pixel_id INTEGER NOT NULL,
                scan_id INTEGER NOT NULL,
                PRIMARY KEY (pixel_id, scan_id)
            );
            """
        )
        for i, (x, y, mz_arr, int_arr, tic) in enumerate(pixels, start=1):
            conn.execute(
                "INSERT INTO ms1_scans VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    i,
                    1.0,
                    len(mz_arr),
                    float(mz_arr.min()),
                    float(mz_arr.max()),
                    tic,
                    "positive",
                    array_to_blob(mz_arr),
                    array_to_blob(int_arr),
                ),
            )
            conn.execute(
                "INSERT INTO spatial_pixels (pixel_id, x, y, t_start, t_end) "
                "VALUES (?, ?, ?, ?, ?)",
                (i, x, y, 0.0, 1.0),
            )
            conn.execute("INSERT INTO pixel_ms1_scans VALUES (?, ?)", (i, i))
        conn.commit()


@pytest.fixture
def three_pixel_db(tmp_path):
    db_path = tmp_path / "sample.db"
    pixels = [
        (0, 0, np.array([100.0, 200.0]), np.array([10.0, 20.0]), 30.0),
        (1, 0, np.array([100.0, 200.0]), np.array([5.0, 8.0]), 13.0),
        # pixel 3 has no 200.0 peak at all -> should reindex to 0 for it.
        (2, 0, np.array([100.0]), np.array([7.0]), 7.0),
    ]
    _build_raw_db(db_path, pixels)
    return db_path


def test_fetch_scan_rows_returns_batched_rows(three_pixel_db):
    batches = _fetch_scan_rows(three_pixel_db, scan_handling="average", batch_size=2)
    rows = [row for batch in batches for row in batch]
    assert len(rows) == 3
    pixel_ids = sorted(row[0] for row in rows)
    assert pixel_ids == [1, 2, 3]


def test_fetch_scan_rows_rejects_invalid_scan_handling(three_pixel_db):
    with pytest.raises(ValueError, match="scan_handling must be"):
        _fetch_scan_rows(three_pixel_db, scan_handling="sideways")


def test_fetch_scan_rows_raises_when_no_rows(tmp_path):
    db_path = tmp_path / "empty.db"
    _build_raw_db(db_path, [])
    with pytest.raises(ValueError, match="No MS1 scans found"):
        _fetch_scan_rows(db_path, scan_handling="average")


def test_create_spatial_adata_still_works_after_fetch_extraction(three_pixel_db):
    # Regression check: create_spatial_adata now calls the shared
    # _fetch_scan_rows helper instead of querying inline.
    adata = create_spatial_adata(
        db_path=three_pixel_db, target_mz_set={100.0, 200.0}, project_id="proj1"
    )
    assert adata.n_obs == 3
    assert adata.n_vars == 2
    dense = adata.X.toarray()
    # pixel with no 200.0 peak gets 0 there, not dropped.
    assert dense.shape == (3, 2)
    assert np.count_nonzero(dense) == 5  # 2 pixels x2 peaks + 1 pixel x1 peak
