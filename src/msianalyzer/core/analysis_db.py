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
metadata            key/value store (analysis-level provenance)
commands            one row per parameter-dependent step (JSON arguments)
samples             one row per raw database feeding the analysis
aggregated_spectra  averaged MS1 / centroids / filtered peaks, per sample
features            aligned cross-sample master m/z list
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

import logging

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
        cur = con.execute(
            "INSERT INTO samples (name, raw_db_path, polarity) VALUES (?, ?, ?)",
            (name, raw_db_path, polarity),
        )
        con.commit()
        return int(cur.lastrowid)


# ---------------------------------------------------------------------------
# Commands / provenance
# ---------------------------------------------------------------------------


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
        cur = con.execute(
            "INSERT INTO commands (command_name, datetime, arguments, run_id, sample_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                command_name,
                datetime.now().astimezone().isoformat(),
                json.dumps(arguments),
                run_id,
                sample_id,
            ),
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


def write_metadata(db_path: Path | str, rows: dict[str, str]) -> None:
    """Upsert key/value pairs into the analysis ``metadata`` table."""
    with sqlite3.connect(Path(db_path)) as con:
        con.executemany(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            [(k, str(v)) for k, v in rows.items()],
        )
        con.commit()


# ---------------------------------------------------------------------------
# Cross-database reads
# ---------------------------------------------------------------------------


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
        con.executemany(
            "INSERT INTO features (mz, members_json, command_id) VALUES (?, ?, ?)",
            records,
        )
        con.commit()


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
