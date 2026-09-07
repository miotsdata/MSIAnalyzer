import sqlite3
import pandas as pd

from pathlib import Path

import logging

from msianalyzer.core.utils.db import safe_execute, safe_executemany
from msianalyzer.core.utils.logging_utils import log_call

logger = logging.getLogger(__name__)


@log_call(source="db_path")
def map_pixels_to_db(db_path: Path | str, df_pixels: pd.DataFrame) -> None:
    """
    Associate scans to pixels.

    Creates spatial_pixels and pixel_ms1_scans tables in db (if not
    existing), inserts pixels information from df_pixels, and executes
    an indexed SQL range join to map scans to pixels.

    Args:
        db_path   (Path | str)  : Path to SQLite database file containing
            spectra info.
        df_pixels (pd.DataFrame): DataFrame containing columns: 'x', 'y',
            't_start', 't_end'.

    Raises:
        KeyError: If df_pixels is missing one of the required columns
            ('x', 'y', 't_start', 't_end').
        sqlite3.OperationalError: If the database is locked or the
            ms1_scans table does not exist (required for the foreign
            key and range join).

    Note:
        If db_path does not exist, SQLite creates a new empty database
        file rather than raising an error.
    """
    logger.debug(
        "Start to map pixels from db to %s",
        db_path,
        extra={"source_file": Path(db_path)},
    )

    # Convert required columns to list of tuples for fast bulk insert
    pixel_tuples = list(
        df_pixels[["x", "y", "t_start", "t_end"]].itertuples(index=False, name=None)
    )

    with sqlite3.connect(Path(db_path)) as conn:
        cursor = conn.cursor()

        # 1. Create Schema & Index on ms1_scans(rt)
        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS spatial_pixels (
                pixel_id INTEGER PRIMARY KEY AUTOINCREMENT,
                x        INTEGER NOT NULL,
                y        INTEGER NOT NULL,
                t_start  REAL    NOT NULL,
                t_end    REAL    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pixel_ms1_scans (
                pixel_id INTEGER NOT NULL,
                scan_id  INTEGER NOT NULL,
                PRIMARY KEY (pixel_id, scan_id),
                FOREIGN KEY (pixel_id) REFERENCES spatial_pixels(pixel_id),
                FOREIGN KEY (scan_id) REFERENCES ms1_scans(scan_id)
            );

            -- Index required for fast B-Tree range matching
            CREATE INDEX IF NOT EXISTS idx_ms1_scans_rt ON ms1_scans(rt);
            -- the (pixel_id, scan_id) PK can't serve a lookup by scan_id;
            -- downstream stages join / probe pixel_ms1_scans by scan_id
            CREATE INDEX IF NOT EXISTS idx_pms1_scan ON pixel_ms1_scans(scan_id);
        """)

        # 1b. These two tables are fully derived from df_pixels + ms1_scans.rt,
        # so a re-run rebuilds them from scratch — clear first, otherwise the
        # junction insert below hits the (pixel_id, scan_id) UNIQUE constraint.
        cursor.execute("DELETE FROM pixel_ms1_scans")
        cursor.execute("DELETE FROM spatial_pixels")

        # 2. Bulk insert pixels from df_pixels
        safe_executemany(
            conn,
            """
            INSERT INTO spatial_pixels (x, y, t_start, t_end)
            VALUES (?, ?, ?, ?);
        """,
            pixel_tuples,
            table="spatial_pixels",
            logger=logger,
            source=db_path,
        )

        # 3. Fast SQL range-based mapping into the junction table
        safe_execute(
            conn,
            """
            INSERT INTO pixel_ms1_scans (pixel_id, scan_id)
            SELECT
                p.pixel_id,
                s.scan_id
            FROM spatial_pixels p
            JOIN ms1_scans s
              ON s.rt >= p.t_start AND s.rt <= p.t_end;
        """,
            table="pixel_ms1_scans",
            logger=logger,
            source=db_path,
        )

        conn.commit()

    logger.info(
        "Successfully processed %d pixels.",
        len(pixel_tuples),
        extra={"source_file": Path(db_path)},
    )
