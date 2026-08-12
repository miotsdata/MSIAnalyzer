from pathlib import Path
import sqlite3
import pandas as pd
import pytest

from msianalyzer.core.utils.spectra_pixels_association import map_pixels_to_db


# ==============================================================================
# HELPER FIXTURES
# ==============================================================================


@pytest.fixture
def setup_ms1_db(tmp_path: Path):
    """
    Creates a temporary SQLite DB initialized with an ms1_scans table 
    and pre-populated test scans.
    """
    db_path = tmp_path / "test_msi.db"

    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE ms1_scans (
                scan_id INTEGER PRIMARY KEY,
                rt REAL NOT NULL
            );
        """)
        # Insert 5 scans at retention times: 1.0, 2.0, 3.0, 4.0, 5.0
        scans = [(1, 1.0), (2, 2.0), (3, 3.0), (4, 4.0), (5, 5.0)]
        conn.executemany("INSERT INTO ms1_scans (scan_id, rt) VALUES (?, ?);", scans)
        conn.commit()

    return db_path


# ==============================================================================
# 1. CORE FUNCTIONALITY & SUCCESS PATHS
# ==============================================================================


def test_map_pixels_to_db_success(setup_ms1_db: Path):
    """Verifies table schema creation, pixel insertion, and accurate range mapping."""
    db_path = setup_ms1_db

    df_pixels = pd.DataFrame([
        {"x": 0, "y": 0, "t_start": 0.5, "t_end": 2.5},  # Should match scans 1 (1.0) and 2 (2.0)
        {"x": 1, "y": 0, "t_start": 2.6, "t_end": 4.5},  # Should match scans 3 (3.0) and 4 (4.0)
    ])

    map_pixels_to_db(db_path, df_pixels)

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        # 1. Verify spatial_pixels table contents
        pixels = cursor.execute(
            "SELECT pixel_id, x, y, t_start, t_end FROM spatial_pixels ORDER BY pixel_id"
        ).fetchall()
        assert len(pixels) == 2
        assert pixels[0] == (1, 0, 0, 0.5, 2.5)
        assert pixels[1] == (2, 1, 0, 2.6, 4.5)

        # 2. Verify pixel_ms1_scans mappings
        mappings = cursor.execute(
            "SELECT pixel_id, scan_id FROM pixel_ms1_scans ORDER BY pixel_id, scan_id"
        ).fetchall()
        assert mappings == [
            (1, 1),  # Pixel 1 -> Scan 1
            (1, 2),  # Pixel 1 -> Scan 2
            (2, 3),  # Pixel 2 -> Scan 3
            (2, 4),  # Pixel 2 -> Scan 4
        ]

        # 3. Verify index creation on ms1_scans(rt)
        indices = cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_ms1_scans_rt'"
        ).fetchone()
        assert indices is not None


def test_map_pixels_to_db_accepts_path_or_str(setup_ms1_db: Path):
    """Verifies function works with both pathlib.Path and str argument types."""
    db_path_str = str(setup_ms1_db)
    df_pixels = pd.DataFrame([{"x": 0, "y": 0, "t_start": 0.0, "t_end": 1.5}])

    map_pixels_to_db(db_path_str, df_pixels)

    with sqlite3.connect(setup_ms1_db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM spatial_pixels").fetchone()[0]
        assert count == 1


# ==============================================================================
# 2. RANGE MAPPING CORNER CASES
# ==============================================================================


def test_map_pixels_scans_out_of_range(setup_ms1_db: Path):
    """Scans outside all pixel time ranges should not produce junction records."""
    db_path = setup_ms1_db

    # Pixel range (10.0 to 20.0) is far beyond max scan time (5.0)
    df_pixels = pd.DataFrame([{"x": 0, "y": 0, "t_start": 10.0, "t_end": 20.0}])

    map_pixels_to_db(db_path, df_pixels)

    with sqlite3.connect(db_path) as conn:
        mappings_count = conn.execute("SELECT COUNT(*) FROM pixel_ms1_scans").fetchone()[0]
        pixels_count = conn.execute("SELECT COUNT(*) FROM spatial_pixels").fetchone()[0]

        assert pixels_count == 1
        assert mappings_count == 0


def test_map_pixels_overlapping_time_windows(setup_ms1_db: Path):
    """When pixel time ranges overlap, scans in the overlap should map to both pixels."""
    db_path = setup_ms1_db

    # Overlapping time windows around retention time 3.0 (scan_id = 3)
    df_pixels = pd.DataFrame([
        {"x": 0, "y": 0, "t_start": 1.0, "t_end": 3.5},  # Covers scans 1, 2, 3
        {"x": 1, "y": 0, "t_start": 2.5, "t_end": 4.0},  # Covers scans 3, 4
    ])

    map_pixels_to_db(db_path, df_pixels)

    with sqlite3.connect(db_path) as conn:
        # Scan 3 should be mapped to both Pixel 1 and Pixel 2
        scan_3_mappings = conn.execute(
            "SELECT pixel_id FROM pixel_ms1_scans WHERE scan_id = 3 ORDER BY pixel_id"
        ).fetchall()

        assert scan_3_mappings == [(1,), (2,)]


def test_map_pixels_exact_inclusive_boundaries(setup_ms1_db: Path):
    """Verifies range join is inclusive on boundaries (t_start <= rt <= t_end)."""
    db_path = setup_ms1_db

    # Exact boundary match for scan_id = 2 (rt = 2.0)
    df_pixels = pd.DataFrame([{"x": 0, "y": 0, "t_start": 2.0, "t_end": 2.0}])

    map_pixels_to_db(db_path, df_pixels)

    with sqlite3.connect(db_path) as conn:
        mappings = conn.execute("SELECT scan_id FROM pixel_ms1_scans").fetchall()
        assert mappings == [(2,)]


# ==============================================================================
# 3. EMPTY & INVALID INPUTS
# ==============================================================================


def test_map_pixels_empty_dataframe(setup_ms1_db: Path):
    """Passing an empty DataFrame creates tables without errors or inserted records."""
    db_path = setup_ms1_db
    df_empty = pd.DataFrame(columns=["x", "y", "t_start", "t_end"])

    map_pixels_to_db(db_path, df_empty)

    with sqlite3.connect(db_path) as conn:
        pixels_count = conn.execute("SELECT COUNT(*) FROM spatial_pixels").fetchone()[0]
        mappings_count = conn.execute("SELECT COUNT(*) FROM pixel_ms1_scans").fetchone()[0]

        assert pixels_count == 0
        assert mappings_count == 0


def test_map_pixels_missing_required_columns_raises_key_error(setup_ms1_db: Path):
    """Missing expected columns in df_pixels should raise a KeyError."""
    db_path = setup_ms1_db
    df_invalid = pd.DataFrame([{"x": 0, "y": 0}])  # Missing 't_start' and 't_end'

    with pytest.raises(KeyError):
        map_pixels_to_db(db_path, df_invalid)


def test_map_pixels_missing_ms1_scans_table_raises_operational_error(tmp_path: Path):
    """Executing range join without prior creation of ms1_scans table raises sqlite3.OperationalError."""
    db_path = tmp_path / "empty_db.db"
    df_pixels = pd.DataFrame([{"x": 0, "y": 0, "t_start": 0.0, "t_end": 1.0}])

    with pytest.raises(sqlite3.OperationalError, match="no such table: main.ms1_scans"):
        map_pixels_to_db(db_path, df_pixels)


# ==============================================================================
# 4. OUTPUT / STDOUT VERIFICATION
# ==============================================================================


def test_map_pixels_prints_success_message(setup_ms1_db: Path, capsys):
    """Verifies the success stdout print statement."""
    db_path = setup_ms1_db
    df_pixels = pd.DataFrame([
        {"x": 0, "y": 0, "t_start": 0.0, "t_end": 1.0},
        {"x": 1, "y": 0, "t_start": 1.0, "t_end": 2.0},
    ])

    map_pixels_to_db(db_path, df_pixels)

    captured = capsys.readouterr()
    assert "Successfully processed 2 pixels." in captured.out