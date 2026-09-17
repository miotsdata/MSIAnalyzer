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
feature_ms2_summary  per-feature MS2 coverage roll-up
precursor_purity     per-MS2 precursor purity (purity_score) vs. its parent MS1
feature_ms2_consensus per-feature "best MS2 scan" pick
annotation_libraries one row per spectral library used to annotate
ms2_annotations      one row per (MS2 scan, library candidate) comparison
target_list_compounds one row per parsed target-list compound (name/formula/InChIKey)
target_list_matches  one row per (target compound, adduct) matched to a feature
rois                 analysis-wide ROI name/color catalog (geometry lives per-sample in h5ad)
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import logging

from msianalyzer.core.utils.db import safe_execute, safe_executemany
from msianalyzer.core.utils.logging_utils import log_call

logger = logging.getLogger(__name__)

DEFAULT_DB_TEMPLATE = "analysis_{run_id}.db"

#: how long a connection waits for a competing writer before raising
#: ``SQLITE_BUSY``. The per-sample workers all append to this one file in
#: parallel; WAL serialises writers, this makes them queue rather than fail.
_BUSY_TIMEOUT_MS = 60_000


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open the analysis database the way every caller must.

    WAL + a long busy timeout so the parallel per-sample workers queue on
    the single-writer lock instead of racing or raising. Callers issue
    **no** DDL — the schema is owned solely by :func:`create_analysis_schema`
    (see :func:`init_analysis_db`) so concurrent writers never take a
    schema lock.

    The mode is only *set* to WAL when it isn't already — re-issuing
    `PRAGMA journal_mode = WAL` is a no-op once the file header says WAL,
    but it isn't a *read-only* no-op: several worker processes opening
    fresh connections to the same file at nearly the same instant (as
    happens right after `ProcessPoolExecutor` forks them) can still race
    on it and corrupt the WAL/shm state. Checking first avoids issuing it
    at all in the overwhelmingly common case (the schema step already put
    the file in WAL mode), which is what actually closes the race —
    "harmless because it's already WAL" was true for the file header, not
    for concurrent callers touching it at once.
    """
    con = sqlite3.connect(Path(db_path), timeout=_BUSY_TIMEOUT_MS / 1000)
    con.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    if con.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
        con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = NORMAL")
    con.execute("PRAGMA foreign_keys = ON")
    return con


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


def _ensure_column(con: sqlite3.Connection, table: str, column: str, coltype: str) -> None:
    """Add ``column`` to ``table`` if it doesn't already have one.

    Unlike ``CREATE INDEX IF NOT EXISTS`` (a separate object, added
    independently of the table it indexes), ``CREATE TABLE IF NOT EXISTS``
    is a total no-op on a table that already exists — it never
    retroactively adds a column a newer schema version introduced. This
    is what makes a later column addition (e.g. ``ms2_annotations.adduct``)
    retroactive on an already-run analysis via
    ``AnalysisBridge.ensureSchemaCurrent`` (ADR 37) the same way a new
    index already was, instead of silently doing nothing for the one
    class of schema change ADR 37 didn't originally cover.
    """
    existing = {row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


@log_call
def create_analysis_schema(con: sqlite3.Connection) -> None:
    """Create every table and index of the analysis database on ``con``.

    Idempotent — every ``CREATE`` statement uses ``IF NOT EXISTS``, and
    a column added to an existing table (rather than a whole new table)
    goes through ``_ensure_column`` instead, for the same effect.
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
            origin       TEXT    NOT NULL DEFAULT 'detected'
                CHECK (origin IN ('detected', 'injected')),
            CONSTRAINT fk_feat_command
            FOREIGN KEY (command_id) REFERENCES commands(id)
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_features_mz ON features(mz)")

    # --- MS2 -> feature association (Stage A of annotation) ---------------
    # Written by core.annotation.group_ms2. One row per MS2 scan; a re-run
    # replaces these rows without touching features / samples. A scan's
    # physical isolation window narrows the search for its matching feature
    # internally, but the count of *other* aligned features that also fall
    # inside it is no longer stored or exposed — it measures feature-list
    # density, not what actually co-fragmented into this scan's own
    # spectrum, and over-flagged badly once a feature list grew dense (see
    # ADR 0019). precursor_purity.purity_score is the scan-intrinsic
    # replacement.
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
            nearest_other_feature_ppm   REAL,
            precursor_target_delta_ppm  REAL,
            rt                          REAL,
            collision_energy            REAL,
            n_peaks                     INTEGER,
            polarity                    TEXT,
            fragmentation_factor        REAL,
            flat_fragmentation          INTEGER NOT NULL DEFAULT 0,
            command_id                  INTEGER,
            UNIQUE (sample_id, scan_id),
            FOREIGN KEY (feature_id) REFERENCES features(feature_id),
            FOREIGN KEY (command_id) REFERENCES commands(id)
        )
    """)
    # Retrofit before the index below — an old table (pre-ADR-43 rename)
    # has neither this column nor anything an index could reference yet.
    _ensure_column(con, "ms2_associations", "fragmentation_factor", "REAL")
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_assoc_feature ON ms2_associations(feature_id)"
    )
    # Per-feature MS2 coverage roll-up (materialised at grouper time).
    con.execute("""
        CREATE TABLE IF NOT EXISTS feature_ms2_summary (
            feature_id        INTEGER PRIMARY KEY,
            feature_mz        REAL    NOT NULL,
            n_ms2             INTEGER NOT NULL,
            n_samples         INTEGER NOT NULL,
            mean_fragmentation_factor REAL,
            n_single_peak     INTEGER NOT NULL,
            n_flat_fragmentation INTEGER NOT NULL DEFAULT 0,
            median_n_peaks    REAL    NOT NULL,
            FOREIGN KEY (feature_id) REFERENCES features(feature_id)
        )
    """)
    _ensure_column(con, "feature_ms2_summary", "mean_fragmentation_factor", "REAL")

    # --- MS2 precursor ion purity (Stage A' of annotation) --------------
    # Written by core.annotation.precursor_purity.run_precursor_purity. One
    # row per MS2 scan; a re-run replaces every row. Chimericity measured
    # against the scan's own parent MS1, not the analysis-wide feature
    # list — purity_score (peak-detection-free) is the metric; see
    # ADR 0019 for why the old peak-picking-based purity/n_peaks_in_window/
    # runner_up_rel_int (and the parent+next-MS1 raster interpolation that
    # fed them) were retired: on real MALDI-imaging data the peak-picker
    # failed to resolve the precursor as a discrete peak in ~56% of dense,
    # matrix-heavy, low-m/z windows, even when the signal was plainly
    # present.
    con.execute("""
        CREATE TABLE IF NOT EXISTS precursor_purity (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            sample_id               INTEGER,
            ms2_scan_id             INTEGER NOT NULL,
            parent_ms1_scan_id      INTEGER,
            window_lo_mz            REAL,
            window_hi_mz            REAL,
            precursor_confirmed     INTEGER,
            purity_score            REAL,
            precursor_mz_snapped    REAL,
            snap_shift_ppm          REAL,
            command_id              INTEGER,
            UNIQUE (sample_id, ms2_scan_id),
            FOREIGN KEY (sample_id) REFERENCES samples(sample_id),
            FOREIGN KEY (command_id) REFERENCES commands(id)
        )
    """)
    # Retrofit before the indexes below — same ordering reason as
    # ms2_associations.fragmentation_factor above.
    _ensure_column(con, "precursor_purity", "purity_score", "REAL")
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_purity_scan "
        "ON precursor_purity(sample_id, ms2_scan_id)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_purity_value "
        "ON precursor_purity(purity_score)"
    )

    # --- per-feature "best MS2 scan" pick (Stage A'' of annotation) ------
    # Written by core.annotation.consensus.run_consensus. One row per
    # feature that carries at least one (kept) MS2 scan; a re-run replaces
    # every row. Folds annotation score (when available), precursor purity
    # and peak count into a single pick so downstream can grab one spectrum
    # per feature without re-deriving the ranking.
    con.execute("""
        CREATE TABLE IF NOT EXISTS feature_ms2_consensus (
            feature_id             INTEGER PRIMARY KEY,
            feature_mz             REAL    NOT NULL,
            best_sample_id         INTEGER,
            best_scan_id           INTEGER NOT NULL,
            n_ms2                  INTEGER NOT NULL,
            n_ms2_considered       INTEGER NOT NULL,
            n_ms2_scored           INTEGER NOT NULL,
            consensus_score        REAL    NOT NULL,
            purity_score            REAL,
            n_peaks                INTEGER,
            best_annotation_score  REAL,
            best_compound_name     TEXT,
            best_inchikey          TEXT,
            command_id             INTEGER,
            FOREIGN KEY (feature_id) REFERENCES features(feature_id),
            FOREIGN KEY (command_id) REFERENCES commands(id)
        )
    """)
    _ensure_column(con, "feature_ms2_consensus", "purity_score", "REAL")
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_consensus_score "
        "ON feature_ms2_consensus(consensus_score)"
    )

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
    # least min_matched_peaks fragments. Ranks (all 1 = best):
    #   rank_ms2                  - candidates within one scan
    #   rank_feature              - every row of the feature (all samples);
    #                              rank_feature = 1 IS the feature's best hit
    #   rank_feature_sample       - every row of one (feature, sample)
    #   rank_scan_feature         - the feature's scans by their best hit
    #                              (all samples), broadcast onto the scan's rows
    #   rank_scan_feature_sample  - same, within one sample
    # Every MS2 scan associated with a feature is scored unconditionally,
    # EXCEPT a scan flagged flat_fragmentation (see group_ms2) — never
    # library-matched, a flat/noisy spectrum isn't real fragmentation to
    # begin with. There is no more feature-list-density "chimeric" gate
    # (see ADR 0019). precursor_confirmed/purity_score are carried through
    # from precursor_purity as the real, per-scan quality signal.
    # emp_raw_*/lib_raw_* are the *untouched* (pre-noise-filtering) spectra
    # on both sides: emp_raw_* is the scan's own raw fragment arrays
    # (identical across every candidate row of that scan), lib_raw_* is the
    # matched candidate's raw spectrum (candidate.mz/intensity, before
    # noise-filtering) — gated by store_raw_spectra, NULL when it was off
    # or for rows written before these columns existed. The noise-filtered,
    # max-normalised view actually scored is NOT stored — it's a pure,
    # deterministic function of the raw arrays plus this run's
    # noise_threshold (recovered from commands.arguments), reconstructed on
    # demand by spectral_match.normalize_and_filter_spectrum (see ADR 18).
    # Persisting the raw arrays here means the GUI's mirror plot never
    # needs to re-open the raw per-sample database or the library file
    # itself (either of which can be a slow/remote mount — see ADR 16),
    # falling back to a live re-read only when the columns are NULL.
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
            adduct                 TEXT,
            cas                    TEXT,
            hmdb                   TEXT,
            score                  REAL    NOT NULL,
            dot_product_score      REAL    NOT NULL,
            lib_coverage           REAL    NOT NULL,
            emp_coverage           REAL    NOT NULL,
            coverage_score         REAL    NOT NULL,
            n_matched_peaks        INTEGER NOT NULL,
            n_lib_peaks            INTEGER NOT NULL,
            n_emp_peaks_raw        INTEGER NOT NULL,
            n_emp_peaks_filtered   INTEGER NOT NULL,
            rank_ms2                 INTEGER NOT NULL,
            rank_feature             INTEGER,
            rank_feature_sample      INTEGER,
            rank_scan_feature        INTEGER,
            rank_scan_feature_sample INTEGER,
            precursor_confirmed    INTEGER,
            purity_score           REAL,
            fragmentation_factor   REAL,
            flat_fragmentation     INTEGER NOT NULL DEFAULT 0,
            emp_raw_mz             BLOB,
            emp_raw_intensity      BLOB,
            lib_raw_mz             BLOB,
            lib_raw_intensity      BLOB,
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
    # Partial index on exactly the predicate `load_feature_representative_annotations`'s
    # `ms2_rep` CTE and `load_feature_list`'s join both filter on — one row
    # per feature that has an MS2 representative, not the full (much
    # larger) candidate table. Without it, `WHERE rank_feature = 1` is a
    # full-table scan of `ms2_annotations` (one row per scored candidate
    # per scan per feature per library), the dominant cost behind the GUI
    # Annotations table being slow to load. See ADR 33.
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_ann_rank_feature_1 "
        "ON ms2_annotations(feature_id) WHERE rank_feature = 1"
    )
    # Retrofit for an analysis run before these columns existed — see
    # ADR (export feature) and `_ensure_column`'s own docstring for why
    # this can't just be part of the CREATE TABLE above like the columns
    # a brand-new analysis already gets.
    for _col, _coltype in (("adduct", "TEXT"), ("cas", "TEXT"), ("hmdb", "TEXT")):
        _ensure_column(con, "ms2_annotations", _col, _coltype)

    # Retrofit for the precursor_only/precursor_frac -> fragmentation_factor/
    # purity_score rename (ADR 43) — the other four tables' own retrofits
    # sit right after their own CREATE TABLE (ordering matters there: each
    # table's own CREATE INDEX below it references the new column name).
    # This one has no such index to worry about.
    for _col, _coltype in (
        ("fragmentation_factor", "REAL"),
        ("purity_score", "REAL"),
    ):
        _ensure_column(con, "ms2_annotations", _col, _coltype)

    # Convenience view: for every (feature, distinct compound) the best
    # library score and the row it came from. Pure aggregation over
    # ms2_annotations (no stored data), so it always reflects the current
    # rows. SQLite fills the bare columns from the MAX(score) row.
    con.execute("DROP VIEW IF EXISTS feature_compound_scores")
    con.execute("""
        CREATE VIEW feature_compound_scores AS
        SELECT
            feature_id,
            inchikey,
            compound_name,
            compound_formula,
            library_id                                   AS best_library_id,
            sample_id                                    AS best_sample_id,
            scan_id                                      AS best_scan_id,
            MAX(score)                                   AS best_score,
            dot_product_score                            AS best_dot_product_score,
            COUNT(*)                                     AS n_candidate_rows,
            COUNT(DISTINCT COALESCE(sample_id, -1) || ':' || scan_id) AS n_scans
        FROM ms2_annotations
        WHERE inchikey IS NOT NULL
        GROUP BY feature_id, inchikey
    """)

    # --- target-list compound matching -------------------------------------
    # Written by core.annotation.target_list.run_target_list_matching. A
    # target compound (name/formula/InChIKey, no m/z of its own) is matched
    # by *theoretical* m/z (formula mass + a searched adduct) against
    # `features` — deliberately its own tables, never merged into
    # ms2_annotations/rank_feature: a target-list match has no scan, no
    # spectral score, nothing that machinery's ranking is built around. See
    # ADR 0026.
    con.execute("""
        CREATE TABLE IF NOT EXISTS target_list_compounds (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT    NOT NULL,
            formula      TEXT    NOT NULL,
            inchikey     TEXT,
            neutral_mass REAL    NOT NULL,
            source_file  TEXT    NOT NULL,
            row_number   INTEGER NOT NULL,
            command_id   INTEGER,
            FOREIGN KEY (command_id) REFERENCES commands(id)
        )
    """)
    # One row per (target compound, adduct) that matched a feature — either
    # an already-detected one (match_type='existing') or one created for it
    # because nothing was close enough (match_type='injected', see
    # features.origin). Multiple compounds/adducts can and do share one
    # feature_id — every match is kept, none collapsed to "the" answer.
    con.execute("""
        CREATE TABLE IF NOT EXISTS target_list_matches (
            id                     INTEGER PRIMARY KEY AUTOINCREMENT,
            target_compound_id     INTEGER NOT NULL,
            feature_id             INTEGER NOT NULL,
            adduct_label           TEXT    NOT NULL,
            adduct_charge          INTEGER NOT NULL,
            adduct_delta_mass      REAL    NOT NULL,
            multiplication_factor  INTEGER NOT NULL,
            theoretical_mz         REAL    NOT NULL,
            ppm_diff               REAL    NOT NULL,
            match_type             TEXT    NOT NULL
                CHECK (match_type IN ('existing', 'injected')),
            command_id             INTEGER,
            FOREIGN KEY (target_compound_id) REFERENCES target_list_compounds(id),
            FOREIGN KEY (feature_id) REFERENCES features(feature_id),
            FOREIGN KEY (command_id) REFERENCES commands(id)
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_tlm_feature ON target_list_matches(feature_id)"
    )

    # --- predicted formulas (on-demand, GUI-triggered — never a RUN_STEPS
    # pipeline step) ---------------------------------------------------
    # Written by core.annotation.formula_prediction.run_formula_prediction,
    # invoked from the Annotations page's "Predict formula" window for a
    # user-picked subset of features (unannotated or annotated-but-
    # unconvincing) — not automatically for every unannotated feature on
    # every run. See ADR 0027 for why this is deliberately kept out of the
    # ordinary pipeline (msbuddy's reference database is ~420MB / ~600-
    # 700MB resident once loaded — a real cost not worth paying on runs
    # that don't need it). A re-run for a given feature (different
    # adducts/settings) replaces exactly that feature's prior rows — see
    # save_predicted_formulas.
    con.execute("""
        CREATE TABLE IF NOT EXISTS predicted_formulas (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            feature_id      INTEGER NOT NULL,
            adduct          TEXT    NOT NULL,
            formula         TEXT    NOT NULL,
            mass_error      REAL    NOT NULL,
            mass_error_ppm  REAL    NOT NULL,
            rank            INTEGER NOT NULL,
            command_id      INTEGER,
            FOREIGN KEY (feature_id) REFERENCES features(feature_id),
            FOREIGN KEY (command_id) REFERENCES commands(id)
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_predf_feature ON predicted_formulas(feature_id)"
    )

    # --- ROI catalog (GUI's "ROI Design" window) --------------------------
    # Analysis-wide name/color registry only — geometry is independent per
    # sample and lives solely in that sample's own h5ad (uns["rois"], see
    # core/plotting/roi.py); this table exists so the same ROI name renders
    # in the same color on every sample it's drawn on, and so the GUI can
    # list/manage ROI names without opening every sample's h5ad.
    con.execute("""
        CREATE TABLE IF NOT EXISTS rois (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT    NOT NULL UNIQUE,
            color      TEXT    NOT NULL,
            created_at TEXT    NOT NULL
        )
    """)


@log_call(source="db_path")
def init_analysis_db(db_path: Path | str) -> sqlite3.Connection:
    """Open (creating if needed) the analysis database and apply its schema.

    Returns:
        An open connection with WAL journalling and foreign keys enabled.
    """
    con = connect(db_path)
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
    with connect(db_path) as con:
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
    with connect(db_path) as con:
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
    with connect(db_path) as con:
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
    with connect(db_path) as con:
        safe_executemany(
            con,
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            [(k, str(v)) for k, v in rows.items()],
            table="metadata",
            logger=logger,
            source=db_path,
        )
        con.commit()


def load_metadata_value(db_path: Path | str, key: str) -> str | None:
    """One value from the analysis ``metadata`` table, e.g. ``"analysis_id"``
    (written by ``run.py`` as ``Run.id`` — used as ``commands.run_id`` for
    every step of that run). `None` if `key` isn't set or the table doesn't
    exist (an ad-hoc/test database that never went through a full run).
    """
    with connect(db_path) as con:
        try:
            cur = con.execute("SELECT value FROM metadata WHERE key = ?", (key,))
        except sqlite3.OperationalError:
            return None
        row = cur.fetchone()
        return row[0] if row else None


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

    with connect(db_path) as con:
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
    with connect(db_path) as con:
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


@log_call(source="db_path")
def load_feature_ids_and_mzs(db_path: Path | str) -> pd.DataFrame:
    """Every ``(feature_id, mz)`` pair — the minimal shape
    ``target_list.match_target_list`` searches against, independent of
    per-sample membership (``load_features``'s shape, which has no
    ``feature_id`` column at all).

    Returns:
        A DataFrame with ``feature_id``, ``mz``, ordered by ``mz``. Empty
        when there are no features.
    """
    with connect(db_path) as con:
        try:
            return pd.read_sql_query(
                "SELECT feature_id, mz FROM features ORDER BY mz", con
            )
        except (pd.errors.DatabaseError, sqlite3.OperationalError):
            return pd.DataFrame(columns=["feature_id", "mz"])


def append_injected_features(
    db_path: Path | str,
    features: list,
    command_id: int | None = None,
) -> list[int]:
    """Insert new synthetic features (``origin='injected'``) — never
    deletes existing rows, unlike :func:`save_features` (which replaces the
    whole table on an ``align_mz`` re-run). So running target-list matching
    after alignment never loses real detected features.

    Args:
        db_path: The analysis database.
        features: A list of ``target_list.InjectedFeature`` (or anything
            with ``.mz``/``.members_json`` attributes).
        command_id: The ``match_target_list`` command this insert belongs to.

    Returns:
        The new ``feature_id``\\ s, in input order.
    """
    ids: list[int] = []
    with connect(db_path) as con:
        for f in features:
            cur = safe_execute(
                con,
                "INSERT INTO features (mz, members_json, command_id, origin) "
                "VALUES (?, ?, ?, 'injected')",
                (f.mz, f.members_json, command_id),
                table="features",
                logger=logger,
                source=db_path,
            )
            ids.append(int(cur.lastrowid))
        con.commit()
    return ids


def load_injected_feature_mzs(db_path: Path | str) -> list[float]:
    """Every ``features.mz`` with ``origin = 'injected'``.

    Feeds ``core.run.run``'s ``target_mz_set`` union before
    ``create_spatial_adata`` runs — read fresh from the DB (not carried
    over in memory) so it's correct whether target-list matching just ran
    or was already-done and skipped on this call.
    """
    with connect(db_path) as con:
        try:
            rows = con.execute(
                "SELECT mz FROM features WHERE origin = 'injected'"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [float(r[0]) for r in rows]


def save_target_list_compounds(
    db_path: Path | str,
    compounds: list,
    command_id: int | None = None,
) -> list[int]:
    """Persist parsed target-list compounds.

    Args:
        compounds: A list of ``target_list.TargetCompound``.
        command_id: The ``match_target_list`` command this insert belongs to.

    Returns:
        The new ``target_list_compounds.id``\\ s, in input order.
    """
    ids: list[int] = []
    with connect(db_path) as con:
        for c in compounds:
            cur = safe_execute(
                con,
                "INSERT INTO target_list_compounds "
                "(name, formula, inchikey, neutral_mass, source_file, "
                " row_number, command_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    c.name, c.formula, c.inchikey, c.neutral_mass,
                    c.source_file, c.row_number, command_id,
                ),
                table="target_list_compounds",
                logger=logger,
                source=db_path,
            )
            ids.append(int(cur.lastrowid))
        con.commit()
    return ids


def save_target_list_matches(
    db_path: Path | str,
    matches: list,
    command_id: int | None = None,
) -> None:
    """Persist target-list matches.

    Args:
        matches: A list of ``(target_list.TargetMatch, target_compound_id)``
            pairs — each match paired with its already-resolved
            ``target_list_compounds.id`` (the caller, ``target_list.
            run_target_list_matching``, resolves this after
            :func:`save_target_list_compounds`).
        command_id: The ``match_target_list`` command this insert belongs to.
    """
    rows = [
        (
            compound_id, m.feature_id, m.adduct.label, m.adduct.charge,
            m.adduct.delta_mass, m.adduct.multiplication_factor,
            m.theoretical_mz, m.ppm_diff, m.match_type, command_id,
        )
        for m, compound_id in matches
    ]
    with connect(db_path) as con:
        safe_executemany(
            con,
            "INSERT INTO target_list_matches "
            "(target_compound_id, feature_id, adduct_label, adduct_charge, "
            " adduct_delta_mass, multiplication_factor, theoretical_mz, "
            " ppm_diff, match_type, command_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            rows,
            table="target_list_matches",
            logger=logger,
            source=db_path,
        )
        con.commit()


@log_call(source="db_path")
def load_target_list_matches_for_feature(
    db_path: Path | str, feature_id: int
) -> pd.DataFrame:
    """Every target-list match for one feature.

    The GUI's top-hits list (Phase B) reads this the same way
    :func:`load_ms2_annotations_for_feature` reads MS2 candidates.

    Returns:
        A DataFrame with ``id``, ``compound_name``, ``compound_formula``,
        ``inchikey``, ``adduct_label``, ``theoretical_mz``, ``ppm_diff``,
        ``match_type``, ordered by closeness (``ABS(ppm_diff)``). Empty
        when the feature has no target-list match.
    """
    sql = """
        SELECT tlm.id, tlc.name AS compound_name, tlc.formula AS compound_formula,
               tlc.inchikey, tlm.adduct_label, tlm.theoretical_mz, tlm.ppm_diff,
               tlm.match_type
        FROM target_list_matches tlm
        JOIN target_list_compounds tlc ON tlc.id = tlm.target_compound_id
        WHERE tlm.feature_id = ?
        ORDER BY ABS(tlm.ppm_diff)
    """
    with connect(db_path) as con:
        try:
            return pd.read_sql_query(sql, con, params=(int(feature_id),))
        except (pd.errors.DatabaseError, sqlite3.OperationalError):
            return pd.DataFrame()


def save_predicted_formulas(
    db_path: Path | str,
    rows: list,
    command_id: int | None = None,
) -> None:
    """Persist predicted formulas, replacing any prior predictions for
    exactly the touched features.

    Unlike :func:`append_injected_features` (never deletes), a re-run of
    formula prediction on the same feature with different settings should
    *update* its guesses, not accumulate duplicates alongside the old
    ones — so every existing ``predicted_formulas`` row for a
    ``feature_id`` appearing in ``rows`` is deleted first. Every other
    feature's predictions (not in this call) are untouched.

    Args:
        db_path: The analysis database.
        rows: A list of ``formula_prediction.PredictedFormula``.
        command_id: The ``predict_formula`` command this insert belongs to.
    """
    if not rows:
        return
    feature_ids = sorted({r.feature_id for r in rows})
    with connect(db_path) as con:
        placeholders = ",".join("?" * len(feature_ids))
        con.execute(
            f"DELETE FROM predicted_formulas WHERE feature_id IN ({placeholders})",
            feature_ids,
        )
        safe_executemany(
            con,
            "INSERT INTO predicted_formulas "
            "(feature_id, adduct, formula, mass_error, mass_error_ppm, rank, "
            " command_id) VALUES (?,?,?,?,?,?,?)",
            [
                (
                    r.feature_id, r.adduct, r.formula, r.mass_error,
                    r.mass_error_ppm, r.rank, command_id,
                )
                for r in rows
            ],
            table="predicted_formulas",
            logger=logger,
            source=db_path,
        )
        con.commit()


@log_call(source="db_path")
def load_predicted_formulas_for_feature(
    db_path: Path | str, feature_id: int
) -> pd.DataFrame:
    """Every predicted-formula candidate for one feature.

    Returns:
        A DataFrame with ``id``, ``adduct``, ``formula``, ``mass_error``,
        ``mass_error_ppm``, ``rank``, ordered by ``adduct`` then ``rank``.
        Empty when the feature has no predicted formula.
    """
    sql = """
        SELECT id, adduct, formula, mass_error, mass_error_ppm, rank
        FROM predicted_formulas
        WHERE feature_id = ?
        ORDER BY adduct, rank
    """
    with connect(db_path) as con:
        try:
            return pd.read_sql_query(sql, con, params=(int(feature_id),))
        except (pd.errors.DatabaseError, sqlite3.OperationalError):
            return pd.DataFrame()


@log_call(source="db_path")
def load_top_predicted_formulas_for_feature(
    db_path: Path | str, feature_id: int, top_n: int
) -> pd.DataFrame:
    """The `top_n` best predicted-formula candidates for one feature,
    ranked globally by closeness (`ABS(mass_error_ppm)`) — **not** grouped
    or capped per adduct the way `load_predicted_formulas_for_feature`'s
    own `rank` column is. Backs the Annotations top-hits panel: "top N
    predictions, regardless of adduct" (see `AnalysisBridge.
    getFeatureTopHits`), as distinct from the per-adduct candidate list
    `load_predicted_formulas_for_feature`/the target-list picker use.

    Returns:
        A DataFrame with ``id``, ``adduct``, ``formula``, ``mass_error``,
        ``mass_error_ppm``, ordered by ascending ``ABS(mass_error_ppm)``,
        at most ``top_n`` rows. Empty when the feature has no predicted
        formula.
    """
    sql = """
        SELECT id, adduct, formula, mass_error, mass_error_ppm
        FROM predicted_formulas
        WHERE feature_id = ?
        ORDER BY ABS(mass_error_ppm)
        LIMIT ?
    """
    with connect(db_path) as con:
        try:
            return pd.read_sql_query(
                sql, con, params=(int(feature_id), max(int(top_n), 0))
            )
        except (pd.errors.DatabaseError, sqlite3.OperationalError):
            return pd.DataFrame()


@log_call(source="db_path")
def load_all_features_for_prediction(db_path: Path | str) -> pd.DataFrame:
    """Every feature with its current representative identity (if any) —
    backs the "Predict formula" window's feature picker, so the user can
    select any mix of unannotated and annotated-but-unconvincing features.

    Same three-tier precedence as `load_feature_representative_annotations`
    (target_list > ms2 > predicted) — a feature already carrying only a
    predicted formula from an earlier run shows that formula here too
    (as `"<formula> + <adduct>"`), not a blank/unannotated row, so the
    picker reflects what just happened after a run instead of looking
    unchanged. It stays fully re-selectable regardless — this list is
    for picking *candidates to (re-)predict*, not just unlabelled ones.

    Returns:
        A DataFrame with ``feature_id``, ``mz``, ``compound_name`` (`None`
        if nothing matched yet), ``best_score`` (`None` for a target-
        list-, predicted-, or un-annotated feature), ordered by ``mz``.
        Empty when there are no features.
    """
    sql = f"""
        WITH {_TARGET_REP_CTE},
        ms2_rep AS (
            SELECT feature_id, compound_name, score AS best_score
            FROM ms2_annotations
            WHERE rank_feature = 1
        ),
        {_PREDICTED_REP_CTE}
        SELECT f.feature_id, f.mz,
               COALESCE(
                   tr.compound_name, m.compound_name,
                   pr.formula || ' + ' || pr.adduct
               ) AS compound_name,
               m.best_score
        FROM features f
        LEFT JOIN target_rep tr ON tr.feature_id = f.feature_id
        LEFT JOIN ms2_rep m ON m.feature_id = f.feature_id
        LEFT JOIN predicted_rep pr ON pr.feature_id = f.feature_id
        ORDER BY f.mz
    """
    with connect(db_path) as con:
        try:
            return pd.read_sql_query(sql, con)
        except (pd.errors.DatabaseError, sqlite3.OperationalError):
            return pd.DataFrame(columns=["feature_id", "mz", "compound_name", "best_score"])


@log_call(source="db_path")
def load_feature_compound_scores(
    db_path: Path | str, feature_id: int | None = None
) -> pd.DataFrame:
    """Best library score per (feature, distinct compound) from the analysis DB.

    Thin reader over the ``feature_compound_scores`` view: one row per
    ``(feature_id, inchikey)`` with the top ``best_score`` and the row it
    came from (``best_sample_id`` / ``best_scan_id`` / ``best_library_id``),
    plus ``n_candidate_rows`` / ``n_scans``. Joined with ``features`` for
    ``mz`` — the view itself doesn't carry it (pure aggregation over
    ``ms2_annotations``). Empty when annotation never ran.

    Every distinct compound that scored *any* candidate row, not just the
    winner — use :func:`load_feature_representative_annotations` instead
    when what you want is the single row a feature is labelled by
    elsewhere (GUI Annotations table, Visual Inspection): the top row here
    by plain ``best_score`` is not necessarily that one (see
    ``AnnotateConfig.representative_score_tolerance``).

    Args:
        db_path: The analysis database.
        feature_id: Restrict to one feature; ``None`` returns every feature.

    Returns:
        A DataFrame ordered by ``feature_id`` then ``best_score`` desc.
    """
    sql = (
        "SELECT fcs.*, f.mz AS mz FROM feature_compound_scores fcs "
        "LEFT JOIN features f ON f.feature_id = fcs.feature_id"
    )
    params: tuple = ()
    if feature_id is not None:
        sql += " WHERE fcs.feature_id = ?"
        params = (int(feature_id),)
    sql += " ORDER BY fcs.feature_id, fcs.best_score DESC"
    with connect(db_path) as con:
        try:
            return pd.read_sql_query(sql, con, params=params)
        except (pd.errors.DatabaseError, sqlite3.OperationalError):
            # view absent (schema predates it) or ms2_annotations missing
            return pd.DataFrame()


#: Every feature with >= 1 target-list match, one row each, its compound
#: name(s)/formula(s)/InChIKey(s) joined with "; " when several distinct
#: target compounds collapsed onto the same feature. Shared by every
#: "representative compound per feature" reader below — a feature with a
#: target-list match displays that identity unconditionally over any MS2
#: `rank_feature = 1` pick (see ADR 0026: a target compound was explicitly
#: asked for by name, unlike an automatically-scored MS2 hit).
_TARGET_REP_CTE = """
    target_rep AS (
        SELECT tlm.feature_id,
               GROUP_CONCAT(DISTINCT tlc.name) AS compound_name,
               GROUP_CONCAT(DISTINCT tlc.formula) AS compound_formula,
               GROUP_CONCAT(DISTINCT tlc.inchikey) AS inchikey,
               GROUP_CONCAT(DISTINCT tlm.adduct_label) AS adduct
        FROM target_list_matches tlm
        JOIN target_list_compounds tlc ON tlc.id = tlm.target_compound_id
        GROUP BY tlm.feature_id
    )
"""

#: Each feature's single best predicted formula (closest ``ABS(
#: mass_error_ppm)`` across every adduct searched, see
#: ``core.annotation.formula_prediction``) — the weakest tier of the
#: representative-identity precedence (target_list > ms2 > predicted),
#: only ever shown when a feature has neither. Not folded into
#: ``_TARGET_REP_CTE`` — this one is optional/on-demand per feature
#: (``predicted_formulas`` may be empty or only cover a hand-picked
#: subset), unlike target-list matching which runs analysis-wide.
_PREDICTED_REP_CTE = """
    predicted_rep AS (
        SELECT feature_id, formula, adduct FROM (
            SELECT feature_id, formula, adduct,
                   ROW_NUMBER() OVER (
                       PARTITION BY feature_id ORDER BY ABS(mass_error_ppm)
                   ) AS rn
            FROM predicted_formulas
        )
        WHERE rn = 1
    )
"""


@log_call(source="db_path")
def load_feature_representative_annotations(
    db_path: Path | str, *, include_unidentified: bool = False
) -> pd.DataFrame:
    """One row per feature that has an MS2 annotation, a target-list match,
    and/or a predicted formula — the representative compound it's labelled
    by everywhere in the GUI/report.

    Three-tier precedence: a target-list match (see
    ``core.annotation.target_list``) wins unconditionally; else the MS2
    ``rank_feature = 1`` pick (stamped at annotate time by
    ``annotate.assign_feature_ranks`` — see
    ``AnnotateConfig.representative_score_tolerance``) wins; else, only if
    neither of those exists, the feature's best predicted formula (see
    ``core.annotation.formula_prediction`` — closest ``mass_error_ppm``
    across every adduct searched) is shown as ``"<formula> + <adduct>"``,
    e.g. ``"C6H12O6 + [M+H]+"``, with `compound_formula` set to the bare
    formula and `inchikey`/`best_score`/peak-count columns `None` (a
    predicted formula has no real compound identity or spectral score —
    it's a mass-decomposition guess, not an annotation). None of this
    touches the underlying `ms2_annotations`/`target_list_matches`/
    `predicted_formulas` rows themselves, only which identity gets
    *displayed*. ``source`` says which tier supplied the name
    (``"target_list"``/``"ms2"``/``"predicted"``). Unlike
    :func:`load_feature_compound_scores` (every distinct compound that
    scored anything), this is exactly one row per feature — the GUI
    Annotations table's backing reader. Empty when none of the three ever
    ran.

    Args:
        include_unidentified: When True, also include a row for every
            feature with *no* representative identity at all (every
            tier-specific column `None`/`NaN`, `source` `None`) — the
            Export menu's Annotation export wants every feature
            regardless of whether it was ever identified; the GUI
            Annotations table (the default, `False`) only ever wants to
            show features that have something to show.

    Returns:
        A DataFrame with ``feature_id``, ``mz``, ``compound_name``,
        ``compound_formula``, ``inchikey``, ``adduct``, ``cas``,
        ``hmdb``, ``library_name``, ``source``, ``best_score`` (``None``
        for a target-list-, predicted-, or unidentified row),
        ``n_matched_peaks``, ``n_lib_peaks``, ``best_sample_id``,
        ``best_scan_id``, ``best_library_id``, ordered by ``feature_id``.
        ``adduct`` comes from ``target_list_matches.adduct_label`` (target
        tier) or the MS2 library candidate's own adduct (ms2 tier, see
        the export feature's own ADR) or ``predicted_formulas.adduct``
        (predicted tier, already part of ``compound_name`` there too).
        ``cas``/``hmdb`` are ms2-tier-only and best-effort — see
        ``annotate._gather_spectrum_extras``'s own docstring for why
        they're not guaranteed even for a library that has adduct info.
    """
    sql = f"""
        WITH {_TARGET_REP_CTE},
        ms2_rep AS (
            SELECT a.feature_id, a.compound_name, a.compound_formula,
                   a.inchikey, a.adduct, a.cas, a.hmdb,
                   a.score AS best_score, a.n_matched_peaks,
                   a.n_lib_peaks, a.sample_id AS best_sample_id,
                   a.scan_id AS best_scan_id, a.library_id AS best_library_id,
                   al.name AS library_name
            FROM ms2_annotations a
            LEFT JOIN annotation_libraries al ON al.id = a.library_id
            WHERE a.rank_feature = 1
        ),
        {_PREDICTED_REP_CTE}
        SELECT
            f.feature_id, f.mz AS mz,
            COALESCE(
                tr.compound_name, m.compound_name,
                pr.formula || ' + ' || pr.adduct
            ) AS compound_name,
            COALESCE(tr.compound_formula, m.compound_formula, pr.formula) AS compound_formula,
            COALESCE(tr.inchikey, m.inchikey) AS inchikey,
            COALESCE(tr.adduct, m.adduct, pr.adduct) AS adduct,
            m.cas, m.hmdb, m.library_name,
            CASE
                WHEN tr.feature_id IS NOT NULL THEN 'target_list'
                WHEN m.feature_id IS NOT NULL THEN 'ms2'
                WHEN pr.feature_id IS NOT NULL THEN 'predicted'
                ELSE NULL
            END AS source,
            m.best_score, m.n_matched_peaks, m.n_lib_peaks,
            m.best_sample_id, m.best_scan_id, m.best_library_id
        FROM features f
        LEFT JOIN target_rep tr ON tr.feature_id = f.feature_id
        LEFT JOIN ms2_rep m ON m.feature_id = f.feature_id
        LEFT JOIN predicted_rep pr ON pr.feature_id = f.feature_id
        {"" if include_unidentified else '''
        WHERE tr.feature_id IS NOT NULL
           OR m.feature_id IS NOT NULL
           OR pr.feature_id IS NOT NULL
        '''}
        ORDER BY f.feature_id
    """
    with connect(db_path) as con:
        try:
            return pd.read_sql_query(sql, con)
        except (pd.errors.DatabaseError, sqlite3.OperationalError):
            return pd.DataFrame()


@log_call(source="db_path")
def load_ms2_annotations_for_feature(
    db_path: Path | str, feature_id: int
) -> pd.DataFrame:
    """Every `ms2_annotations` candidate row for one feature.

    Unlike `feature_compound_scores` (best row per *distinct compound*),
    this is every row — every (sample, scan, library candidate) — with the
    sample and library name joined in for display. Used by the GUI
    Annotations section's per-feature candidate popup.

    Args:
        db_path: The analysis database.
        feature_id: The feature to fetch candidates for.

    Returns:
        A DataFrame ordered by `rank_feature`, then `score` desc. Empty
        when the feature has no annotation rows (or annotation never ran).
    """
    sql = """
        SELECT a.id, a.sample_id, s.name AS sample_name, a.scan_id,
               a.library_id, lib.name AS library_name, a.compound_name,
               a.compound_formula, a.inchikey, a.score, a.dot_product_score,
               a.lib_coverage, a.emp_coverage, a.n_matched_peaks,
               a.n_lib_peaks, a.rank_ms2, a.rank_feature,
               a.rank_feature_sample
        FROM ms2_annotations a
        LEFT JOIN samples s ON s.sample_id = a.sample_id
        LEFT JOIN annotation_libraries lib ON lib.id = a.library_id
        WHERE a.feature_id = ?
        ORDER BY a.rank_feature, a.score DESC
    """
    with connect(db_path) as con:
        try:
            return pd.read_sql_query(sql, con, params=(int(feature_id),))
        except (pd.errors.DatabaseError, sqlite3.OperationalError):
            return pd.DataFrame()


def _scalar_count(con: sqlite3.Connection, sql: str) -> int:
    try:
        row = con.execute(sql).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0]) if row and row[0] is not None else 0


@log_call(source="db_path")
def load_summary_counts(db_path: Path | str) -> dict:
    """Cheap, always-available headline counts for the analysis.

    Every count is a plain ``COUNT(*)``/``COUNT(DISTINCT ...)``, computed
    fresh from the current tables — unlike ``summary.json`` (the report
    stage's own, much richer roll-up, built for the HTML report), this
    works whether or not the report stage ran, and is a small, stable
    contract independent of the report's internal schema. Used by the
    GUI's Analysis-workspace Summary section.

    Returns:
        ``{"n_samples", "n_features", "n_ms2_associated_features",
        "annotation_ran", "n_annotated_features", "n_distinct_compounds"}``.
        ``n_annotated_features``/``n_distinct_compounds`` are ``0`` (not
        ``None``) when ``annotation_ran`` is ``False`` — the caller decides
        how to show "no library configured" vs. "library ran, 0 hits".
    """
    with connect(db_path) as con:
        n_samples = _scalar_count(con, "SELECT COUNT(*) FROM samples")
        n_features = _scalar_count(con, "SELECT COUNT(*) FROM features")
        n_ms2_associated_features = _scalar_count(
            con, "SELECT COUNT(*) FROM feature_ms2_summary WHERE n_ms2 > 0"
        )
        n_libraries = _scalar_count(con, "SELECT COUNT(*) FROM annotation_libraries")
        n_annotated_features = _scalar_count(
            con, "SELECT COUNT(DISTINCT feature_id) FROM feature_compound_scores"
        )
        n_distinct_compounds = _scalar_count(
            con, "SELECT COUNT(DISTINCT inchikey) FROM feature_compound_scores"
        )

    return {
        "n_samples": n_samples,
        "n_features": n_features,
        "n_ms2_associated_features": n_ms2_associated_features,
        "annotation_ran": n_libraries > 0,
        "n_annotated_features": n_annotated_features,
        "n_distinct_compounds": n_distinct_compounds,
    }


@log_call(source="db_path")
def load_samples(db_path: Path | str) -> pd.DataFrame:
    """Every row of the `samples` table, for a GUI sample selector.

    Returns:
        A DataFrame with `sample_id`, `name`, `polarity`, ordered by
        `sample_id`. Empty when there are no samples.
    """
    sql = "SELECT sample_id, name, polarity FROM samples ORDER BY sample_id"
    with connect(db_path) as con:
        try:
            return pd.read_sql_query(sql, con)
        except (pd.errors.DatabaseError, sqlite3.OperationalError):
            return pd.DataFrame()


@log_call(source="db_path")
def find_nearest_feature(db_path: Path | str, mz: float) -> dict | None:
    """The feature whose consensus m/z is closest to `mz`.

    Used by the GUI's MS1 spectra section: map a clicked spectrum point back
    to a feature.

    Args:
        db_path: The analysis database.
        mz: Query m/z (e.g. the x-coordinate of a spectrum click).

    Returns:
        `{"feature_id", "mz", "members"}` where `members` is
        `{sample_name: peak_index_or_None}` (`None` = not present in that
        sample — filtered out or never picked, no distinction made). `None`
        when the analysis has no features at all.
    """
    with connect(db_path) as con:
        try:
            row = con.execute(
                "SELECT feature_id, mz, members_json FROM features "
                "ORDER BY ABS(mz - ?) LIMIT 1",
                (float(mz),),
            ).fetchone()
        except sqlite3.OperationalError:
            return None

    if row is None:
        return None

    feature_id, feature_mz, members_json = row
    members = json.loads(members_json) if members_json else {}
    return {"feature_id": feature_id, "mz": feature_mz, "members": members}


@log_call(source="db_path")
def load_feature_ms2_count(db_path: Path | str, feature_id: int) -> int:
    """`feature_ms2_summary.n_ms2` for one feature — 0 if it has no MS2 at all."""
    with connect(db_path) as con:
        try:
            row = con.execute(
                "SELECT n_ms2 FROM feature_ms2_summary WHERE feature_id = ?",
                (int(feature_id),),
            ).fetchone()
        except sqlite3.OperationalError:
            return 0
    return int(row[0]) if row is not None else 0


@log_call(source="db_path")
def load_feature_list(db_path: Path | str) -> pd.DataFrame:
    """Every feature, with its representative compound name if any.

    For the Visual Inspection section's feature selector: features get
    labelled by compound name when annotated, by `"<formula> + <adduct>"`
    when only a predicted formula exists, else by bare m/z. Three-tier
    precedence (target-list > MS2 > predicted) — see
    `load_feature_representative_annotations`.

    Returns:
        A DataFrame with `feature_id`, `mz`, `compound_name` (`None` when
        unannotated), ordered by `mz`. Empty when there are no features.
    """
    sql = f"""
        WITH {_TARGET_REP_CTE},
        {_PREDICTED_REP_CTE}
        SELECT f.feature_id, f.mz,
               COALESCE(
                   tr.compound_name, best.compound_name,
                   pr.formula || ' + ' || pr.adduct
               ) AS compound_name
        FROM features f
        LEFT JOIN target_rep tr ON tr.feature_id = f.feature_id
        LEFT JOIN ms2_annotations best
            ON best.feature_id = f.feature_id AND best.rank_feature = 1
        LEFT JOIN predicted_rep pr ON pr.feature_id = f.feature_id
        ORDER BY f.mz
    """
    with connect(db_path) as con:
        try:
            return pd.read_sql_query(sql, con)
        except (pd.errors.DatabaseError, sqlite3.OperationalError):
            return pd.DataFrame()


@log_call(source="db_path")
def load_feature_categories(db_path: Path | str) -> pd.DataFrame:
    """Every feature's MS1-spectrum coloring category, for the GUI's MS1
    Spectra section: `"target_list"` (has a target-list match — regardless
    of MS2 status, since it was explicitly searched for by name),
    `"annotated"` (has a library match), `"predicted"` (no target-list
    match and no library match, but has a predicted formula — see
    `core.annotation.formula_prediction` — from a user explicitly running
    prediction on it), `"no_ms2"` (never got an MS2 scan and has no
    predicted formula either), or `"non_annotated"` (has MS2 but no
    library match, and no predicted formula).

    Returns:
        A DataFrame with `feature_id`, `mz`, `category`, ordered by `mz`
        (ascending — callers that nearest-match spectrum peaks against
        this rely on that order). Empty when there are no features.
    """
    sql = f"""
        WITH {_TARGET_REP_CTE}
        SELECT f.feature_id, f.mz,
               COALESCE(ms2.n_ms2, 0) AS n_ms2,
               best.compound_name,
               tr.feature_id IS NOT NULL AS has_target,
               pf.feature_id IS NOT NULL AS has_predicted
        FROM features f
        LEFT JOIN feature_ms2_summary ms2 ON ms2.feature_id = f.feature_id
        LEFT JOIN ms2_annotations best
            ON best.feature_id = f.feature_id AND best.rank_feature = 1
        LEFT JOIN target_rep tr ON tr.feature_id = f.feature_id
        LEFT JOIN (SELECT DISTINCT feature_id FROM predicted_formulas) pf
            ON pf.feature_id = f.feature_id
        ORDER BY f.mz
    """
    with connect(db_path) as con:
        try:
            df = pd.read_sql_query(sql, con)
        except (pd.errors.DatabaseError, sqlite3.OperationalError):
            return pd.DataFrame(columns=["feature_id", "mz", "category"])

    if df.empty:
        return pd.DataFrame(columns=["feature_id", "mz", "category"])

    has_target = df["has_target"].astype(bool)
    has_predicted = df["has_predicted"].astype(bool)
    annotated = df["compound_name"].notna() & ~has_target
    predicted = has_predicted & ~has_target & ~annotated
    no_ms2 = (df["n_ms2"] == 0) & ~has_target & ~predicted
    df["category"] = np.select(
        [has_target, annotated, predicted, no_ms2],
        ["target_list", "annotated", "predicted", "no_ms2"],
        default="non_annotated",
    )
    return df[["feature_id", "mz", "category"]]


# ---------------------------------------------------------------------------
# ROI catalog
# ---------------------------------------------------------------------------


@log_call(source="db_path")
def register_roi(db_path: Path | str, name: str, color: str) -> int:
    """Insert a new ROI catalog row and return its id.

    Args:
        db_path: The analysis database.
        name: ROI name — must not already exist (`UNIQUE(name)`); the GUI
            checks `load_rois` before calling this, but the DB constraint
            is the real guard.
        color: `"#rrggbb"`.

    Returns:
        The new `rois.id`.

    Raises:
        sqlite3.IntegrityError: `name` already exists.
    """
    with connect(db_path) as con:
        cur = safe_execute(
            con,
            "INSERT INTO rois (name, color, created_at) VALUES (?, ?, ?)",
            (name, color, datetime.now().astimezone().isoformat()),
            table="rois",
            logger=logger,
            source=db_path,
        )
        con.commit()
        return int(cur.lastrowid)


@log_call(source="db_path")
def load_rois(db_path: Path | str) -> pd.DataFrame:
    """Every ROI catalog entry — for a ROI-name picker / management list.

    Returns:
        A DataFrame with `id`, `name`, `color`, `created_at`, ordered by
        `name`. Empty when there are no ROIs yet, or `db_path` doesn't
        resolve to a database with this table.
    """
    sql = "SELECT id, name, color, created_at FROM rois ORDER BY name"
    with connect(db_path) as con:
        try:
            return pd.read_sql_query(sql, con)
        except (pd.errors.DatabaseError, sqlite3.OperationalError):
            return pd.DataFrame()


@log_call(source="db_path")
def delete_roi_catalog_entry(db_path: Path | str, name: str) -> bool:
    """Remove one ROI's catalog row (name/color registration only — does
    NOT touch any sample's h5ad; see `core/plotting/roi.py` for the
    per-sample and `merged.h5ad` deletion, which callers must do
    separately, e.g. `AnalysisBridge.deleteRoiEverywhere`).

    Returns:
        True if a row was actually deleted.
    """
    with connect(db_path) as con:
        cur = safe_execute(
            con, "DELETE FROM rois WHERE name = ?", (name,),
            table="rois", logger=logger, source=db_path,
        )
        con.commit()
        return cur.rowcount > 0
