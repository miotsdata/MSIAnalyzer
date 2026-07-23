from pathlib import Path
import pandas as pd
import sqlite3


def map_pixels_to_db(db_path: Path | str, df_pixels: pd.DataFrame) -> None:
    """
    Creates spatial_pixels and pixel_ms1_scans tables in SQLite, inserts pixels
    from df_pixels, and executes an indexed SQL range join to map scans to pixels.

    Parameters:
    -----------
    db_path : Path | str
        Path to SQLite database file.
    df_pixels : pd.DataFrame
        DataFrame containing columns: 'x', 'y', 't_start', 't_end'
    """
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
        """)

        # 2. Bulk insert pixels from df_pixels
        cursor.executemany(
            """
            INSERT INTO spatial_pixels (x, y, t_start, t_end)
            VALUES (?, ?, ?, ?);
        """,
            pixel_tuples,
        )

        # 3. Fast SQL range-based mapping into the junction table
        cursor.execute("""
            INSERT INTO pixel_ms1_scans (pixel_id, scan_id)
            SELECT 
                p.pixel_id,
                s.scan_id
            FROM spatial_pixels p
            JOIN ms1_scans s
              ON s.rt >= p.t_start AND s.rt <= p.t_end;
        """)

        conn.commit()
        print(f"Successfully processed {len(pixel_tuples)} pixels.")
