"""
analysis_db.py
Schema and provenance helpers for the per-analysis SQLite database.

Each analysis (one :class:`~msianalyzer.core.run.run.Run`) owns exactly one
database, written next to the run outputs. It holds everything that depends
on a parameter choice — averaged/centroided/filtered MS1 spectra, the aligned
cross-sample feature list, and (later) MS2 groupings and annotations. The raw
per-sample databases produced by the parser are never modified.

Tables
------
metadata             key/value store (analysis-level provenance)
commands             one row per parameter-dependent step (JSON arguments)
samples              one row per raw database feeding the analysis
aggregated_spectra   averaged MS1 / centroids / filtered peaks, per sample
features             aligned cross-sample master m/z list
ms2_associations     one row per MS2 scan snapped to a feature (grouper)
ms2_window_features  features inside each scan's isolation window
feature_ms2_summary  per-feature MS2 coverage roll-up
annotation_libraries one row per spectral library used to annotate
ms2_annotations      one row per (MS2 scan, library candidate) comparison
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

import logging

from msianalyzer.core.utils.db import safe_execute, safe_executemany
from msianalyzer.core.utils.logging_utils import log_call

logger = logging.getLogger(__name__)

DEFAULT_DB_TEMPLATE = "analysis_{run_id}.db"


# ---------------------------------------------------------------------------
# Path helper
# ---------------------------------------------------------------------------


def analysis_db_path(
    out_dir: Path | str, run_id: str, override: str | None = None
) -> Path:
    """Return the analysis-database path for a run.

    Args:
        out_dir: Directory where run outputs are written.
        run_id: Identifier of the run/analysis.
        override: Explicit file name to use instead of the default
            ``analysis_<run_id>.db``.

    Returns:
        ``out_dir / (override or "analysis_<run_id>.db")``.
    """
    name = override or DEFAULT_DB_TEMPLATE.format(run_id=run_id)
    return Path(out_dir) / name


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


@log_call
def create_analysis_schema(con: sqlite3.Connection) -> None:
    """Create every table and index of the analysis database on ``con``.

    Idempotent — every statement uses ``IF NOT EXISTS``.
    """
    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS commands (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id       UUID    NOT NULL,
            sample_id    INTEGER,
            command_name TEXT    NOT NULL,
            datetime     TEXT    NOT NULL,
            arguments    TEXT    NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_cmd_run_id ON commands(run_id)")
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_cmd_lookup ON commands(command_name, run_id, sample_id)"
    )
    con.execute("""
        CREATE TABLE IF NOT EXISTS samples (
            sample_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT    NOT NULL,
            raw_db_path  TEXT    NOT NULL UNIQUE,
            polarity     TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS aggregated_spectra (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id          TEXT    NOT NULL,
            sample_id       INTEGER,
            command_id      INTEGER,
            mz_array        BLOB,
            intensity_array BLOB,
            CONSTRAINT fk_agg_sample
            FOREIGN KEY (sample_id) REFERENCES samples(sample_id),
            CONSTRAINT fk_agg_command
            FOREIGN KEY (command_id) REFERENCES commands(id)
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_agg_run_id ON aggregated_spectra(run_id)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_agg_sample ON aggregated_spectra(sample_id)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_agg_command ON aggregated_spectra(command_id)"
    )
    con.execute("""
        CREATE TABLE IF NOT EXISTS features (
            feature_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            mz           REAL    NOT NULL,
            members_json TEXT    NOT NULL,
            command_id   INTEGER,
            CONSTRAINT fk_feat_command
            FOREIGN KEY (command_id) REFERENCES commands(id)
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_features_mz ON features(mz)")

    # --- MS2 -> feature association (Stage A of annotation) ---------------
    # Written by core.annotation.group_ms2. One row per MS2 scan; a re-run
    # replaces these rows without touching features / samples.
    con.execute("""
        CREATE TABLE IF NOT EXISTS ms2_associations (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            sample_id                   INTEGER,
            scan_id                     INTEGER NOT NULL,
            feature_id                  INTEGER,
            match_key                   TEXT    NOT NULL,
            precursor_mz                REAL,
            isolation_window_target     REAL,
            isolation_window_lower      REAL,
            isolation_window_upper      REAL,
            ppm_offset                  REAL,
            n_features_in_window        INTEGER NOT NULL,
            nearest_other_feature_ppm   REAL,
            precursor_target_delta_ppm  REAL,
            rt                          REAL,
            collision_energy            REAL,
            n_peaks                     INTEGER,
            polarity                    TEXT,
            precursor_only              INTEGER NOT NULL DEFAULT 0,
            command_id                  INTEGER,
            UNIQUE (sample_id, scan_id),
            FOREIGN KEY (feature_id) REFERENCES features(feature_id),
            FOREIGN KEY (command_id) REFERENCES commands(id)
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_assoc_feature ON ms2_associations(feature_id)"
    )
    # One row per (association, feature inside its isolation window). Always
    # populated: a clean single match is one row with is_primary = 1.
    con.execute("""
        CREATE TABLE IF NOT EXISTS ms2_window_features (
            association_id  INTEGER NOT NULL,
            feature_id      INTEGER NOT NULL,
            feature_mz      REAL    NOT NULL,
            ppm_diff        REAL,
            within_tol      INTEGER NOT NULL,
            is_primary      INTEGER NOT NULL,
            PRIMARY KEY (association_id, feature_id),
            FOREIGN KEY (association_id) REFERENCES ms2_associations(id) ON DELETE CASCADE,
            FOREIGN KEY (feature_id) REFERENCES features(feature_id)
        )
    """)
    # Per-feature MS2 coverage roll-up (materialised at grouper time).
    con.execute("""
        CREATE TABLE IF NOT EXISTS feature_ms2_summary (
            feature_id        INTEGER PRIMARY KEY,
            feature_mz        REAL    NOT NULL,
            n_ms2             INTEGER NOT NULL,
            n_samples         INTEGER NOT NULL,
            n_precursor_only  INTEGER NOT NULL,
            n_single_peak     INTEGER NOT NULL,
            n_chimeric        INTEGER NOT NULL,
            median_n_peaks    REAL    NOT NULL,
            FOREIGN KEY (feature_id) REFERENCES features(feature_id)
        )
    """)

    # --- MS2 -> library annotation (Stage B of annotation) ---------------
    # Written by core.annotation.annotate. One row per spectral library
    # actually used; a re-run replaces the ms2_annotations rows.
    con.execute("""
        CREATE TABLE IF NOT EXISTS annotation_libraries (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            path         TEXT    NOT NULL UNIQUE,
            name         TEXT,
            n_spectra    INTEGER,
            n_compounds  INTEGER,
            command_id   INTEGER,
            FOREIGN KEY (command_id) REFERENCES commands(id)
        )
    """)
    # One row per (MS2 scan, library candidate) comparison that shared at
    # least min_matched_peaks fragments. `rank` orders candidates within a
    # scan (1 = best); `rank_feature` orders scans within a feature by their
    # best hit. The four *_filtered_* blobs are the noise-filtered,
    # max-normalised spectra actually scored (for mirror plots); NULL when
    # store_filtered_spectra was off.
    con.execute("""
        CREATE TABLE IF NOT EXISTS ms2_annotations (
            id                     INTEGER PRIMARY KEY AUTOINCREMENT,
            sample_id              INTEGER,
            scan_id                INTEGER NOT NULL,
            feature_id             INTEGER,
            library_id             INTEGER NOT NULL,
            library_spectrum_id    INTEGER NOT NULL,
            compound_id            INTEGER,
            compound_name          TEXT,
            compound_formula       TEXT,
            inchikey               TEXT,
            score                  REAL    NOT NULL,
            dot_product_score      REAL    NOT NULL,
            lib_coverage           REAL    NOT NULL,
            emp_coverage           REAL    NOT NULL,
            coverage_score         REAL    NOT NULL,
            n_matched_peaks        INTEGER NOT NULL,
            n_lib_peaks            INTEGER NOT NULL,
            n_emp_peaks_raw        INTEGER NOT NULL,
            n_emp_peaks_filtered   INTEGER NOT NULL,
            rank                   INTEGER NOT NULL,
            rank_feature           INTEGER,
            is_chimeric            INTEGER NOT NULL DEFAULT 0,
            n_features_in_window   INTEGER,
            precursor_only         INTEGER NOT NULL DEFAULT 0,
            emp_filtered_mz        BLOB,
            emp_filtered_intensity BLOB,
            lib_filtered_mz        BLOB,
            lib_filtered_intensity BLOB,
            command_id             INTEGER,
            FOREIGN KEY (feature_id) REFERENCES features(feature_id),
            FOREIGN KEY (library_id) REFERENCES annotation_libraries(id),
            FOREIGN KEY (command_id) REFERENCES commands(id)
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_ann_scan "
        "ON ms2_annotations(sample_id, scan_id)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_ann_feature ON ms2_annotations(feature_id)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_ann_score ON ms2_annotations(score)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_ann_inchikey ON ms2_annotations(inchikey)"
    )


@log_call(source="db_path")
def init_analysis_db(db_path: Path | str) -> sqlite3.Connection:
    """Open (creating if needed) the analysis database and apply its schema.

    Returns:
        An open connection with WAL journalling and foreign keys enabled.
    """
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys = ON")
    create_analysis_schema(con)
    con.commit()
    return con


# ---------------------------------------------------------------------------
# Samples
# ---------------------------------------------------------------------------


@log_call(source="db_path")
def register_sample(
    db_path: Path | str,
    name: str,
    raw_db_path: Path | str,
    polarity: str | None = None,
) -> int:
    """Insert a sample row (or return the existing one) and return its id.

    Samples are keyed by ``raw_db_path``; calling this twice for the same
    raw database is a no-op that returns the original ``sample_id``.
    """
    raw_db_path = str(Path(raw_db_path))
    with sqlite3.connect(Path(db_path)) as con:
        cur = con.execute(
            "SELECT sample_id FROM samples WHERE raw_db_path = ?", (raw_db_path,)
        )
        row = cur.fetchone()
        if row is not None:
            return int(row[0])
        cur = safe_execute(
            con,
            "INSERT INTO samples (name, raw_db_path, polarity) VALUES (?, ?, ?)",
            (name, raw_db_path, polarity),
            table="samples",
            logger=logger,
            source=db_path,
        )
        con.commit()
        return int(cur.lastrowid)


# ---------------------------------------------------------------------------
# Commands / provenance
# ---------------------------------------------------------------------------


@log_call(source="db_path")
def log_command(
    db_path: Path | str,
    command_name: str,
    arguments: dict,
    run_id: str,
    sample_id: int | None = None,
) -> int:
    """Append a row to the analysis database's ``commands`` table.

    Args:
        db_path: Path to the analysis database.
        command_name: Short identifier, e.g. ``"detect_ms1_centroids"``.
        arguments: JSON-serialisable parameters describing the step.
        run_id: Identifier of the run the command belongs to.
        sample_id: Sample the step applies to, or None for run-wide steps
            (e.g. alignment).

    Returns:
        The new ``commands.id``.
    """
    with sqlite3.connect(Path(db_path)) as con:
        cur = safe_execute(
            con,
            "INSERT INTO commands (command_name, datetime, arguments, run_id, sample_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                command_name,
                datetime.now().astimezone().isoformat(),
                json.dumps(arguments),
                run_id,
                sample_id,
            ),
            table="commands",
            logger=logger,
            source=db_path,
        )
        con.commit()
        return int(cur.lastrowid)


def is_command_already_run(
    command_name: str,
    run_id: str,
    db_path: Path | str,
    sample_id: int | None = None,
) -> bool:
    """Return True if a matching ``commands`` row already exists.

    Args:
        command_name: Name recorded in the ``commands`` table.
        run_id: Identifier of the run to check.
        db_path: Path to the SQLite database holding the ``commands`` table.
        sample_id: If given, also require the row's ``sample_id`` to match.
            Only meaningful for analysis databases (the raw ``commands``
            table has no ``sample_id`` column).
    """
    with sqlite3.connect(Path(db_path)) as con:
        if sample_id is None:
            cur = con.execute(
                "SELECT 1 FROM commands WHERE command_name = ? AND run_id = ? LIMIT 1",
                (command_name, run_id),
            )
        else:
            cur = con.execute(
                "SELECT 1 FROM commands "
                "WHERE command_name = ? AND run_id = ? AND sample_id = ? LIMIT 1",
                (command_name, run_id, sample_id),
            )
        return cur.fetchone() is not None


@log_call(source="db_path")
def write_metadata(db_path: Path | str, rows: dict[str, str]) -> None:
    """Upsert key/value pairs into the analysis ``metadata`` table."""
    with sqlite3.connect(Path(db_path)) as con:
        safe_executemany(
            con,
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            [(k, str(v)) for k, v in rows.items()],
            table="metadata",
            logger=logger,
            source=db_path,
        )
        con.commit()


# ---------------------------------------------------------------------------
# Cross-database reads
# ---------------------------------------------------------------------------


@log_call(source="raw_db_path")
def attach_raw(
    con: sqlite3.Connection, raw_db_path: Path | str, alias: str = "raw"
) -> None:
    """Attach a raw database under ``alias`` for cross-database reads.

    Callers must treat the attached database as read-only — the analysis
    layer never writes back into a raw database.
    """
    if not alias.isidentifier():
        raise ValueError(f"invalid attach alias: {alias!r}")
    con.execute(f"ATTACH DATABASE ? AS {alias}", (str(Path(raw_db_path)),))


# ---------------------------------------------------------------------------
# Features (aligned cross-sample m/z list)
# ---------------------------------------------------------------------------


@log_call(source="db_path")
def save_features(
    db_path: Path | str, aligned_df: pd.DataFrame, command_id: int | None = None
) -> None:
    """Persist the output of ``align_mz_across_samples`` to ``features``.

    Each DataFrame row becomes one row: the consensus ``mz`` (the index)
    plus a JSON object mapping every sample column to its original peak
    index (or null when the sample did not contribute).
    """
    records = []
    for mz, row in aligned_df.iterrows():
        members = {
            col: (None if pd.isna(row[col]) else int(row[col]))
            for col in aligned_df.columns
        }
        records.append((float(mz), json.dumps(members), command_id))

    with sqlite3.connect(Path(db_path)) as con:
        con.execute("DELETE FROM features")
        safe_executemany(
            con,
            "INSERT INTO features (mz, members_json, command_id) VALUES (?, ?, ?)",
            records,
            table="features",
            logger=logger,
            source=db_path,
        )
        con.commit()


@log_call(source="db_path")
def load_features(db_path: Path | str) -> pd.DataFrame:
    """Reconstruct the aligned DataFrame previously stored by :func:`save_features`.

    Returns:
        A DataFrame indexed by consensus ``mz`` with one nullable-``Int64``
        column per sample, matching the shape returned by
        ``align_mz_across_samples``.
    """
    with sqlite3.connect(Path(db_path)) as con:
        rows = con.execute(
            "SELECT mz, members_json FROM features ORDER BY mz"
        ).fetchall()

    if not rows:
        return pd.DataFrame()

    index = [r[0] for r in rows]
    records = [json.loads(r[1]) for r in rows]
    df = pd.DataFrame(records, index=index)
    df.index.name = "mz"
    for col in df.columns:
        df[col] = df[col].astype("Int64")
    return df
