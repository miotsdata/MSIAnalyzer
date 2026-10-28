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
feature_isotopes     isotope features taken out of `features`, linked to their mono (ADR 54)
ms2_associations     one row per MS2 scan snapped to a feature (grouper)
feature_ms2_summary  per-feature MS2 coverage roll-up
precursor_purity     per-MS2 precursor purity (purity_score) vs. its parent MS1
feature_ms2_consensus per-feature "best MS2 scan" pick
annotation_libraries one row per spectral library used to annotate
ms2_annotations      one row per (MS2 scan, library candidate) comparison
target_list_compounds one row per parsed target-list compound (name/formula/InChIKey)
target_list_matches  one row per (target compound, adduct) matched to a feature
rois                 analysis-wide ROI name/color catalog (geometry lives per-sample in h5ad)
calibration_curves   fitted calibration curves (targeted analyses), append-only history
calibration_points   one row per calibration standard ROI feeding a curve
calibration_unknowns one row per quantified ROI (interpolated against a curve)
imported_datasets    notebook-analyzed .h5ad re-imported as a linked dataset (ADR 49)
imported_dataset_samples which analysis samples each imported dataset covers
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import logging

from msianalyzer.core.project.project import NotInProjectFolderError, get_project_folder
from msianalyzer.core.utils.db import safe_execute, safe_executemany
from msianalyzer.core.utils.paths import relative_to_project, resolve_in_project
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

    # --- Isotopes taken out of `features` (ADR 54) --------------------------
    # Written by core.spectra.isotopes. A row keeps the feature's own id, m/z
    # and members, so a wrong removal can be inspected (and restored).
    con.execute("""
        CREATE TABLE IF NOT EXISTS feature_isotopes (
            feature_id       INTEGER PRIMARY KEY,
            mz               REAL    NOT NULL,
            members_json     TEXT    NOT NULL,
            mono_feature_id  INTEGER NOT NULL,
            isotope_index    INTEGER NOT NULL,
            charge           INTEGER NOT NULL,
            n_samples_linked INTEGER NOT NULL,
            n_samples_both   INTEGER NOT NULL,
            intensity_ratio  REAL,
            spatial_corr     REAL,
            command_id       INTEGER,
            FOREIGN KEY (mono_feature_id) REFERENCES features(feature_id),
            FOREIGN KEY (command_id) REFERENCES commands(id)
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_feature_isotopes_mono "
        "ON feature_isotopes(mono_feature_id)"
    )

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

    # --- why each MS2-bearing feature did or didn't get a library hit -----
    # Written by core.annotation.annotate.persist_annotations, one row per
    # feature with MS2 associations; a re-run replaces every row. A feature
    # with no row has no MS2 (or annotation never ran). See ADR 0062.
    con.execute("""
        CREATE TABLE IF NOT EXISTS feature_annotation_outcome (
            feature_id            INTEGER PRIMARY KEY,
            outcome               TEXT    NOT NULL,
            n_scans               INTEGER NOT NULL,
            n_skipped_flat        INTEGER NOT NULL,
            n_skipped_purity      INTEGER NOT NULL,
            n_skipped_unreadable  INTEGER NOT NULL,
            n_candidates          INTEGER NOT NULL,
            best_n_matched_peaks  INTEGER NOT NULL,
            command_id            INTEGER,
            FOREIGN KEY (feature_id) REFERENCES features(feature_id),
            FOREIGN KEY (command_id) REFERENCES commands(id)
        )
    """)

    # --- MS2 cleaning (ADR 0064) -------------------------------------------
    # Written by the grouping stage (core.annotation.ms2_cleaning): the
    # settings spectra are cleaned with, and the fragments shared by many
    # precursors (`fraction` of the polarity's precursors; `removed` ones
    # are taken out of every spectrum when background removal is on).
    con.execute("""
        CREATE TABLE IF NOT EXISTS ms2_cleaning (
            background_enabled       INTEGER NOT NULL,
            background_ppm           REAL    NOT NULL,
            background_min_percent   REAL    NOT NULL,
            above_precursor_enabled  INTEGER NOT NULL,
            above_precursor_ppm      REAL    NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS ms2_background_fragments (
            polarity      TEXT,
            mz            REAL    NOT NULL,
            n_precursors  INTEGER NOT NULL,
            fraction      REAL    NOT NULL,
            removed       INTEGER NOT NULL
        )
    """)

    # --- annotation by precursor m/z alone (ADR 0065) -----------------------
    # Written by core.annotation.precursor_annotation: every library
    # candidate within `annotate.precursor_ppm` of each feature, one per
    # (library, compound, adduct), `rank` 1 = smallest |ppm_error|.
    # `ref_source` says whether `ref_mz` is the library's stored precursor
    # ("library") or formula + adduct ("formula").
    con.execute("""
        CREATE TABLE IF NOT EXISTS precursor_annotations (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            feature_id           INTEGER NOT NULL,
            library_id           INTEGER,
            library_spectrum_id  INTEGER,
            compound_id          INTEGER,
            compound_name        TEXT,
            compound_formula     TEXT,
            inchikey             TEXT,
            adduct               TEXT,
            ref_mz               REAL    NOT NULL,
            ref_source           TEXT    NOT NULL,
            ppm_error            REAL    NOT NULL,
            rank                 INTEGER NOT NULL,
            command_id           INTEGER,
            FOREIGN KEY (feature_id) REFERENCES features(feature_id),
            FOREIGN KEY (library_id) REFERENCES annotation_libraries(id),
            FOREIGN KEY (command_id) REFERENCES commands(id)
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_precursor_annotations_feature "
        "ON precursor_annotations(feature_id, rank)"
    )

    # --- every MS2 annotation state of the analysis (ADR 0063) ------------
    # Written by core.annotation.reannotation: the pipeline's own state
    # (recorded at the first re-annotation), each re-annotation and each
    # restore. `snapshot` is a file under <analysis folder>/annotation_history/
    # holding that state's MS2 tables. The newest row is the current state.
    con.execute("""
        CREATE TABLE IF NOT EXISTS annotation_history (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at            TEXT    NOT NULL,
            kind                  TEXT    NOT NULL,
            settings_json         TEXT    NOT NULL,
            snapshot              TEXT,
            restored_from         INTEGER,
            n_annotated_features  INTEGER
        )
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
            target_mz    REAL,
            FOREIGN KEY (command_id) REFERENCES commands(id)
        )
    """)
    # The ion m/z of a target given by m/z instead of (or over) a formula;
    # NULL for a formula-only target. Added later, hence the ensure.
    _ensure_column(con, "target_list_compounds", "target_mz", "REAL")
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

    # --- Calibration curve quantification (targeted analyses) -------------
    # v1 is single-analyte/single-curve per analysis (see the feature's own
    # ADR) — calibration_points/calibration_unknowns each hold one
    # analysis-wide set (not scoped per curve_id: there's only ever one
    # "current" set of standards/results), while calibration_curves is
    # append-only fit *history* over that one set (a re-fit adds a new row,
    # same "keep history" convention as predicted_formulas). ROI geometry
    # is unchanged, still per-sample h5ad uns["rois"] + the `rois` catalog
    # table above — these tables only add concentration/measurement/fit
    # metadata referencing ROI names.
    con.execute("""
        CREATE TABLE IF NOT EXISTS calibration_curves (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            analyte_mz         REAL    NOT NULL,
            fit_type           TEXT    NOT NULL DEFAULT 'linear'
                CHECK (fit_type IN ('linear', 'weighted_linear')),
            weighting          TEXT CHECK (weighting IS NULL OR weighting IN ('1/x', '1/x2')),
            concentration_unit TEXT    NOT NULL,
            slope              REAL    NOT NULL,
            intercept          REAL    NOT NULL,
            r_squared          REAL    NOT NULL,
            lod                REAL,
            loq                REAL,
            n_points           INTEGER NOT NULL,
            command_id         INTEGER REFERENCES commands(id),
            created_at         TEXT    NOT NULL
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_calcurve_analyte ON calibration_curves(analyte_mz)"
    )
    # A standard's known quantity is entered as a total *amount* in the
    # spot (e.g. pmol), not a concentration directly — concentration
    # (this table's own `concentration`, the curve's actual x-value) is
    # derived from `amount / area`, `area` from the ROI's own pixel count
    # times a user-provided (assumed square) pixel edge length. All of
    # amount/pixel_size/area are kept per point (not just derived and
    # discarded) so every row is independently auditable/exportable
    # without needing to look anything else up. `UNIQUE(roi_name)` — one
    # ROI is one standard; re-adding the same ROI updates it (see
    # upsert_calibration_point) rather than duplicating.
    con.execute("""
        CREATE TABLE IF NOT EXISTS calibration_points (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            sample_id       INTEGER REFERENCES samples(sample_id),
            roi_name        TEXT    NOT NULL UNIQUE,
            amount          REAL    NOT NULL,
            amount_unit     TEXT    NOT NULL,
            pixel_size      REAL    NOT NULL,
            pixel_size_unit TEXT    NOT NULL CHECK (pixel_size_unit IN ('um', 'mm')),
            area            REAL    NOT NULL,
            concentration   REAL    NOT NULL,
            mean_intensity  REAL    NOT NULL,
            n_pixels        INTEGER NOT NULL,
            created_at      TEXT    NOT NULL
        )
    """)
    # Same amount/area bookkeeping as calibration_points, but derived in
    # the other direction: `interpolated_conc` (from the curve fit) times
    # this ROI's own `area` gives `amount` — "how much analyte is in this
    # region", not just "how concentrated" — both null together until a
    # curve actually exists to interpolate against (see
    # save_calibration_unknown); `curve_id` records which fit produced the
    # current result, updated in place on every re-fit
    # (update_calibration_unknown_result) rather than versioned, since v1
    # has only one curve active at a time.
    con.execute("""
        CREATE TABLE IF NOT EXISTS calibration_unknowns (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            sample_id         INTEGER REFERENCES samples(sample_id),
            roi_name          TEXT    NOT NULL UNIQUE,
            pixel_size        REAL    NOT NULL,
            pixel_size_unit   TEXT    NOT NULL CHECK (pixel_size_unit IN ('um', 'mm')),
            area              REAL    NOT NULL,
            mean_intensity    REAL    NOT NULL,
            n_pixels          INTEGER NOT NULL,
            curve_id          INTEGER REFERENCES calibration_curves(id),
            interpolated_conc REAL,
            amount            REAL,
            created_at        TEXT    NOT NULL
        )
    """)

    # --- Imported analyzed .h5ad datasets (ADR 49) --------------------------
    # A notebook-analyzed .h5ad (merged or single-sample) re-imported and
    # split into one .h5ad per sample under `rel_dir` (relative to this
    # database's own folder, so an analysis folder stays relocatable).
    # `source_path` is the user's own file, kept only so ROIs drawn on the
    # imported data can be written back into its obs (never deleted).
    con.execute("""
        CREATE TABLE IF NOT EXISTS imported_datasets (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT    NOT NULL UNIQUE,
            rel_dir       TEXT    NOT NULL UNIQUE,
            source_path   TEXT    NOT NULL,
            source_kind   TEXT    NOT NULL CHECK (source_kind IN ('merged', 'single')),
            sample_column TEXT,
            layers        TEXT    NOT NULL DEFAULT '[]',
            imported_at   TEXT    NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS imported_dataset_samples (
            dataset_id   INTEGER NOT NULL REFERENCES imported_datasets(id) ON DELETE CASCADE,
            sample_id    INTEGER NOT NULL REFERENCES samples(sample_id),
            source_label TEXT,
            n_pixels     INTEGER NOT NULL,
            PRIMARY KEY (dataset_id, sample_id)
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
# Stored paths (ADR 59): relative to the project when inside it
# ---------------------------------------------------------------------------


def _project_of(db_path: Path | str) -> Path | None:
    try:
        return get_project_folder(Path(db_path).parent)
    except NotInProjectFolderError:
        return None


def _path_for_storage(db_path: Path | str, path: Path | str) -> str:
    """`path` relative to the project holding `db_path` when inside it,
    else absolute — what gets written to the analysis DB."""
    return relative_to_project(path, _project_of(db_path))


def _stored_path_here(db_path: Path | str, stored: str) -> str:
    """A stored path located in the project holding `db_path` on this
    machine (see `resolve_in_project`); unchanged outside a project."""
    root = _project_of(db_path)
    return stored if root is None else str(resolve_in_project(stored, root))


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

    Samples are keyed by ``raw_db_path``, stored relative to the project
    when inside it (ADR 59); calling this twice for the same raw database
    is a no-op that returns the original ``sample_id``.
    """
    raw_db_path = _path_for_storage(db_path, raw_db_path)
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


#: `metadata` key holding the scale bar settings (ADR 53) as JSON.
SCALE_BAR_KEY = "scale_bar"


def load_scale_bar(db_path: Path | str) -> dict:
    """The analysis' scale bar settings (ADR 53), defaults filled in.

    Returns:
        `{"enabled": bool, "bar_um": float, "color": "#rrggbb",
        "pixel_um": {sample_name: float}}` — a sample missing from
        `pixel_um` has no known pixel size and gets no bar.
    """
    settings = {"enabled": False, "bar_um": 50.0, "color": "#ffffff", "pixel_um": {}}
    raw = load_metadata_value(db_path, SCALE_BAR_KEY)
    if raw:
        settings.update(json.loads(raw))
    return settings


def save_scale_bar(db_path: Path | str, settings: dict) -> None:
    """Persist the scale bar settings (`load_scale_bar`'s shape)."""
    write_metadata(db_path, {SCALE_BAR_KEY: json.dumps(settings)})


#: `metadata` key holding the custom categorical colors (ADR 57) as JSON:
#: `{obs_column: {category: "#rrggbb"}}`, analysis-wide.
OBS_CATEGORY_COLORS_KEY = "obs_category_colors"


def _load_all_obs_category_colors(db_path: Path | str) -> dict:
    raw = load_metadata_value(db_path, OBS_CATEGORY_COLORS_KEY)
    return json.loads(raw) if raw else {}


def load_obs_category_colors(db_path: Path | str, obs_column: str) -> dict:
    """The user's custom colors for one discrete obs column (ADR 57).

    Returns:
        `{category: "#rrggbb"}`; categories never customised are absent
        (they keep the default palette, see `heatmap.category_colors`).
    """
    return _load_all_obs_category_colors(db_path).get(obs_column, {})


def save_obs_category_color(
    db_path: Path | str, obs_column: str, category: str, color: str
) -> None:
    """Set one category's custom color (`"#rrggbb"`)."""
    colors = _load_all_obs_category_colors(db_path)
    colors.setdefault(obs_column, {})[category] = color.lower()
    write_metadata(db_path, {OBS_CATEGORY_COLORS_KEY: json.dumps(colors)})


def reset_obs_category_colors(db_path: Path | str, obs_column: str) -> None:
    """Drop every custom color of one obs column, back to the defaults."""
    colors = _load_all_obs_category_colors(db_path)
    colors.pop(obs_column, None)
    write_metadata(db_path, {OBS_CATEGORY_COLORS_KEY: json.dumps(colors)})


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


@log_call(source="db_path")
def load_protected_feature_ids(db_path: Path | str) -> set[int]:
    """Features the isotope stage must never remove: injected by, or matched
    to, a target-list compound (ADR 54)."""
    with connect(db_path) as con:
        rows = con.execute(
            "SELECT feature_id FROM features WHERE origin = 'injected' "
            "UNION SELECT feature_id FROM target_list_matches"
        ).fetchall()
    return {int(r[0]) for r in rows}


@log_call(source="db_path")
def move_features_to_isotopes(
    db_path: Path | str, isotopes: list, command_id: int | None = None
) -> None:
    """Move each isotope's `features` row to `feature_isotopes`, in one
    transaction.

    Args:
        isotopes: `core.spectra.isotopes.AcceptedIsotope` items.
    """
    with connect(db_path) as con:
        con.execute("DELETE FROM feature_isotopes")
        for iso in isotopes:
            mz, members = con.execute(
                "SELECT mz, members_json FROM features WHERE feature_id = ?",
                (iso.feature_id,),
            ).fetchone()
            safe_execute(
                con,
                "INSERT INTO feature_isotopes (feature_id, mz, members_json, "
                "mono_feature_id, isotope_index, charge, n_samples_linked, "
                "n_samples_both, intensity_ratio, spatial_corr, command_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    iso.feature_id, mz, members, iso.mono_feature_id,
                    iso.isotope_index, iso.charge, iso.n_samples_linked,
                    iso.n_samples_both, iso.intensity_ratio, iso.spatial_corr,
                    command_id,
                ),
                table="feature_isotopes",
                logger=logger,
                source=db_path,
            )
            con.execute("DELETE FROM features WHERE feature_id = ?", (iso.feature_id,))
        con.commit()


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
                " row_number, command_id, target_mz) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    c.name, c.formula, c.inchikey, c.neutral_mass,
                    c.source_file, c.row_number, command_id,
                    getattr(c, "target_mz", None),
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
def count_command_target_list_matches(db_path: Path | str, command_name: str) -> int:
    """How many `target_list_matches` rows the `command_name` commands
    wrote (e.g. post-run target-list matching, ADR 60). 0 when none, or
    on an analysis DB without the tables."""
    with connect(db_path) as con:
        try:
            row = con.execute(
                "SELECT COUNT(*) FROM target_list_matches WHERE command_id IN "
                "(SELECT id FROM commands WHERE command_name = ?)",
                (command_name,),
            ).fetchone()
        except sqlite3.OperationalError:
            return 0
    return int(row[0])


@log_call(source="db_path")
def delete_command_target_list_rows(db_path: Path | str, command_name: str) -> int:
    """Delete every target-list match and compound written by the
    `command_name` commands, and those commands themselves (the undo of
    post-run target-list matching, ADR 60). Rows from the pipeline's own
    target-list step are untouched.

    Returns:
        The number of matches deleted.
    """
    ids = "(SELECT id FROM commands WHERE command_name = ?)"
    with connect(db_path) as con:
        n = con.execute(
            f"DELETE FROM target_list_matches WHERE command_id IN {ids}", (command_name,)
        ).rowcount
        con.execute(
            f"DELETE FROM target_list_compounds WHERE command_id IN {ids}", (command_name,)
        )
        con.execute("DELETE FROM commands WHERE command_name = ?", (command_name,))
        con.commit()
    return int(n)


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
               GROUP_CONCAT(DISTINCT NULLIF(tlc.formula, '')) AS compound_formula,
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

    Tier precedence: a target-list match (see
    ``core.annotation.target_list``) wins unconditionally; else the MS2
    ``rank_feature = 1`` pick (stamped at annotate time by
    ``annotate.assign_feature_ranks`` — see
    ``AnnotateConfig.representative_score_tolerance``) wins; else, only if
    neither of those exists, its best precursor-m/z candidate (ADR 0065,
    named ``"<formula> + <adduct> (<n> candidates)"``); else the feature's
    best predicted formula (see
    ``core.annotation.formula_prediction`` — closest ``mass_error_ppm``
    across every adduct searched) is shown as ``"<formula> + <adduct>"``,
    e.g. ``"C6H12O6 + [M+H]+"``, with `compound_formula` set to the bare
    formula and `inchikey`/`best_score`/peak-count columns `None` (a
    predicted formula has no real compound identity or spectral score —
    it's a mass-decomposition guess, not an annotation). None of this
    touches the underlying `ms2_annotations`/`target_list_matches`/
    `predicted_formulas` rows themselves, only which identity gets
    *displayed*. ``source`` says which tier supplied the name
    (``"target_list"``/``"ms2"``/``"precursor"``/``"predicted"``). Unlike
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
        ``best_scan_id``, ``best_library_id``, ``ms2_annotation_outcome``,
        ``precursor_name``, ``precursor_ppm``, ``n_precursor_candidates``,
        ordered by ``feature_id``.
        The last three are the precursor-m/z tier (ADR 0065) whichever
        tier names the feature: its best candidate as
        ``"<formula> + <adduct> (<n> candidates)"``, that candidate's
        signed ppm error, and how many candidates it has (``0``: none).
        ``ms2_annotation_outcome`` is the feature's
        ``feature_annotation_outcome.outcome`` (ADR 0062), ``"no_ms2"`` for
        a feature without one, and ``None`` everywhere when annotation
        never recorded outcomes (not run, or run before ADR 0062).
        ``adduct`` comes from ``target_list_matches.adduct_label`` (target
        tier) or the MS2 library candidate's own adduct (ms2 tier, see
        the export feature's own ADR) or ``predicted_formulas.adduct``
        (predicted tier, already part of ``compound_name`` there too).
        ``cas``/``hmdb`` are ms2-tier-only and best-effort — see
        ``annotate._gather_spectrum_extras``'s own docstring for why
        they're not guaranteed even for a library that has adduct info.
    """
    # Not every analysis has the table: one never opened since ADR 0062.
    with connect(db_path) as con:
        has_outcomes = con.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'feature_annotation_outcome'"
        ).fetchone() is not None
    outcome_col, outcome_join = (
        (
            "COALESCE(o.outcome, CASE WHEN EXISTS "
            "(SELECT 1 FROM feature_annotation_outcome) THEN 'no_ms2' END)",
            "LEFT JOIN feature_annotation_outcome o ON o.feature_id = f.feature_id",
        )
        if has_outcomes else ("NULL", "")
    )
    with connect(db_path) as con:
        has_precursor = con.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'precursor_annotations'"
        ).fetchone() is not None
    # A precursor-only hit (ADR 0065) is named by formula + adduct with how
    # many candidates share them (isomers tie on m/z).
    precursor_cte = (
        """
        precursor_rep AS (
            SELECT p.feature_id, p.compound_formula, p.adduct, p.ppm_error,
                   COALESCE(p.compound_formula, p.compound_name)
                     || COALESCE(' + ' || p.adduct, '') || ' (' || p.n_same
                     || CASE p.n_same WHEN 1 THEN ' candidate)' ELSE ' candidates)' END
                     AS name,
                   (SELECT COUNT(*) FROM precursor_annotations q
                    WHERE q.feature_id = p.feature_id) AS n_candidates
            FROM (
                SELECT r.*,
                       (SELECT COUNT(*) FROM precursor_annotations q
                        WHERE q.feature_id = r.feature_id
                          AND q.compound_formula IS r.compound_formula
                          AND q.adduct IS r.adduct) AS n_same
                FROM precursor_annotations r
                WHERE r.rank = 1
            ) p
        ),"""
        if has_precursor else
        """
        precursor_rep AS (
            SELECT NULL AS feature_id, NULL AS compound_formula, NULL AS adduct,
                   NULL AS ppm_error, NULL AS name, NULL AS n_candidates
            WHERE 0
        ),"""
    )
    sql = f"""
        WITH {_TARGET_REP_CTE},
        {precursor_cte}
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
                tr.compound_name, m.compound_name, pc.name,
                pr.formula || ' + ' || pr.adduct
            ) AS compound_name,
            COALESCE(
                tr.compound_formula, m.compound_formula, pc.compound_formula, pr.formula
            ) AS compound_formula,
            COALESCE(tr.inchikey, m.inchikey) AS inchikey,
            COALESCE(tr.adduct, m.adduct, pc.adduct, pr.adduct) AS adduct,
            m.cas, m.hmdb, m.library_name,
            CASE
                WHEN tr.feature_id IS NOT NULL THEN 'target_list'
                WHEN m.feature_id IS NOT NULL THEN 'ms2'
                WHEN pc.feature_id IS NOT NULL THEN 'precursor'
                WHEN pr.feature_id IS NOT NULL THEN 'predicted'
                ELSE NULL
            END AS source,
            m.best_score, m.n_matched_peaks, m.n_lib_peaks,
            m.best_sample_id, m.best_scan_id, m.best_library_id,
            {outcome_col} AS ms2_annotation_outcome,
            pc.name AS precursor_name, pc.ppm_error AS precursor_ppm,
            COALESCE(pc.n_candidates, 0) AS n_precursor_candidates
        FROM features f
        LEFT JOIN target_rep tr ON tr.feature_id = f.feature_id
        LEFT JOIN ms2_rep m ON m.feature_id = f.feature_id
        LEFT JOIN predicted_rep pr ON pr.feature_id = f.feature_id
        LEFT JOIN precursor_rep pc ON pc.feature_id = f.feature_id
        {outcome_join}
        {"" if include_unidentified else '''
        WHERE tr.feature_id IS NOT NULL
           OR m.feature_id IS NOT NULL
           OR pc.feature_id IS NOT NULL
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


@log_call(source="db_path")
def load_precursor_annotations_for_feature(db_path: Path | str, feature_id: int) -> pd.DataFrame:
    """One feature's precursor-m/z candidates (ADR 0065), best first, with
    the library name joined in. Empty without any (or before ADR 0065)."""
    sql = """
        SELECT p.id, p.compound_name, p.compound_formula, p.inchikey, p.adduct,
               p.ref_mz, p.ref_source, p.ppm_error, p.rank, lib.name AS library_name
        FROM precursor_annotations p
        LEFT JOIN annotation_libraries lib ON lib.id = p.library_id
        WHERE p.feature_id = ?
        ORDER BY p.rank
    """
    with connect(db_path) as con:
        try:
            return pd.read_sql_query(sql, con, params=(int(feature_id),))
        except (pd.errors.DatabaseError, sqlite3.OperationalError):
            return pd.DataFrame()


@log_call(source="db_path")
def load_feature_best_ms2_scans(db_path: Path | str) -> pd.DataFrame:
    """Each feature's best MS2 scan (`feature_ms2_consensus`), with where
    to read its peaks — so the GUI Annotations table can show the
    spectrum of a feature no library matched.

    Returns:
        A DataFrame with ``feature_id``, ``ms2_sample_name``,
        ``ms2_raw_db_path`` (located like `get_sample_raw_db_path`),
        ``ms2_scan_id``, ``ms2_n_scans``, ``ms2_purity_score``,
        ``ms2_n_peaks``. Only features with a consensus row; empty when
        the consensus step never ran.
    """
    sql = """
        SELECT c.feature_id, s.name AS ms2_sample_name,
               s.raw_db_path AS ms2_raw_db_path, c.best_scan_id AS ms2_scan_id,
               c.n_ms2 AS ms2_n_scans, c.purity_score AS ms2_purity_score,
               c.n_peaks AS ms2_n_peaks
        FROM feature_ms2_consensus c
        LEFT JOIN samples s ON s.sample_id = c.best_sample_id
    """
    with connect(db_path) as con:
        try:
            df = pd.read_sql_query(sql, con)
        except (pd.errors.DatabaseError, sqlite3.OperationalError):
            return pd.DataFrame()
    df["ms2_raw_db_path"] = [
        _stored_path_here(db_path, p) if p else None for p in df["ms2_raw_db_path"]
    ]
    return df


@log_call(source="db_path")
def load_feature_annotation_outcomes(db_path: Path | str) -> pd.DataFrame:
    """Every `feature_annotation_outcome` row (ADR 0062) — the counts
    behind each MS2-bearing feature's outcome, for the GUI Annotations
    details panel. Empty when annotation never recorded outcomes."""
    sql = """
        SELECT feature_id, n_scans, n_skipped_flat, n_skipped_purity,
               n_skipped_unreadable, n_candidates, best_n_matched_peaks
        FROM feature_annotation_outcome
    """
    with connect(db_path) as con:
        try:
            return pd.read_sql_query(sql, con)
        except (pd.errors.DatabaseError, sqlite3.OperationalError):
            return pd.DataFrame()


def _scalar_count(con: sqlite3.Connection, sql: str) -> int:
    try:
        row = con.execute(sql).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0]) if row and row[0] is not None else 0


_OUTCOMES = ("annotated", "no_shared_fragments", "no_candidates", "all_scans_skipped")


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
        "annotation_ran", "n_annotated_features", "n_distinct_compounds",
        "outcomes_recorded", "n_outcome_<outcome>"}`` — one count per
        ADR 0062 outcome plus ``no_ms2``, and ``"ms2_background"``: the
        ``ms2_background_fragments`` rows (ADR 0064), most shared first;
        ``"precursor_ran"``/``"n_precursor_matched_features"`` for the
        precursor-m/z annotation (ADR 0065).
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
        outcomes = {o: 0 for o in _OUTCOMES}
        try:
            outcomes.update(con.execute(
                "SELECT outcome, COUNT(*) FROM feature_annotation_outcome GROUP BY outcome"
            ).fetchall())
        except sqlite3.OperationalError:
            pass
        # annotation by precursor m/z (ADR 0065)
        n_precursor_matched = _scalar_count(
            con, "SELECT COUNT(DISTINCT feature_id) FROM precursor_annotations"
        )
        precursor_ran = _scalar_count(
            con, "SELECT COUNT(*) FROM commands WHERE command_name = 'annotate_precursor'"
        ) > 0
        try:  # MS2 cleaning (ADR 0064): the fragments shared by many precursors
            background = [
                {"polarity": p, "mz": mz, "n_precursors": n, "percent": 100 * f,
                 "removed": bool(r)}
                for p, mz, n, f, r in con.execute(
                    "SELECT polarity, mz, n_precursors, fraction, removed "
                    "FROM ms2_background_fragments ORDER BY fraction DESC, mz"
                )
            ]
        except sqlite3.OperationalError:
            background = []
    recorded = any(outcomes.values())

    return {
        "n_samples": n_samples,
        "n_features": n_features,
        "n_ms2_associated_features": n_ms2_associated_features,
        "annotation_ran": n_libraries > 0,
        "n_annotated_features": n_annotated_features,
        "n_distinct_compounds": n_distinct_compounds,
        # Where MS2 annotation lost each feature (ADR 0062); all 0 when
        # outcomes were never recorded — `outcomes_recorded` tells which.
        "outcomes_recorded": recorded,
        "n_outcome_no_ms2": n_features - sum(outcomes.values()) if recorded else 0,
        **{f"n_outcome_{o}": n for o, n in outcomes.items()},
        "ms2_background": background,
        "precursor_ran": precursor_ran,
        "n_precursor_matched_features": n_precursor_matched,
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
def get_sample_raw_db_path(db_path: Path | str, sample_name: str) -> str | None:
    """One sample's `raw_db_path` — how GUI code that needs the RAW
    per-sample database (e.g. H&E image coregistration, see
    `core/registration/`) gets there from an analysis DB + sample name,
    the same two values every other `AnalysisBridge` slot already takes.

    Args:
        db_path: The analysis' SQLite database.
        sample_name: `samples.name` to look up.

    Returns:
        The raw database path, located in the project folder holding
        `db_path` when the stored absolute path is from before the project
        moved or from another OS (see `resolve_in_project`); `None` if no
        sample named `sample_name` exists (or the `samples` table doesn't
        exist yet).
    """
    with connect(db_path) as con:
        try:
            row = con.execute(
                "SELECT raw_db_path FROM samples WHERE name = ?", (sample_name,)
            ).fetchone()
        except sqlite3.OperationalError:
            return None
    return _stored_path_here(db_path, row[0]) if row else None


@log_call(source="db_path")
def get_sample_id(db_path: Path | str, sample_name: str) -> int | None:
    """One sample's `sample_id` — how GUI code (e.g. calibration point/
    unknown bookkeeping) resolves a sample name to the id
    `calibration_points.sample_id`/`calibration_unknowns.sample_id`
    reference, the same as `get_sample_raw_db_path`'s lookup shape.

    Returns:
        The sample's id, or `None` if no sample named `sample_name` exists.
    """
    with connect(db_path) as con:
        try:
            row = con.execute(
                "SELECT sample_id FROM samples WHERE name = ?", (sample_name,)
            ).fetchone()
        except sqlite3.OperationalError:
            return None
    return int(row[0]) if row else None


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


# ---------------------------------------------------------------------------
# Calibration curve quantification (targeted analyses)
# ---------------------------------------------------------------------------


@log_call(source="db_path")
def register_calibration_curve(
    db_path: Path | str,
    analyte_mz: float,
    fit_type: str,
    weighting: str | None,
    concentration_unit: str,
    slope: float,
    intercept: float,
    r_squared: float,
    lod: float | None,
    loq: float | None,
    n_points: int,
    command_id: int | None = None,
) -> int:
    """Insert a new calibration curve row and return its id.

    Append-only, like `predicted_formulas` — re-fitting never overwrites a
    previous curve, it adds a new row (`load_calibration_curve` defaults to
    the most recent one).

    Args:
        db_path: The analysis database.
        analyte_mz: The m/z the curve quantifies.
        fit_type: `"linear"` or `"weighted_linear"`.
        weighting: `"1/x"`/`"1/x2"` for a weighted fit, `None` otherwise.
        concentration_unit: e.g. `"pmol/mm2"` — see
            `core.calibration.units.concentration_unit`. Every point
            feeding this curve must share this same unit (checked before
            fitting, not here — this is just where it's recorded).
        slope, intercept, r_squared, lod, loq, n_points: See
            `core.calibration.curve_fit.CalibrationFitResult`.
        command_id: The `fit_calibration_curve` command's id, if logged.

    Returns:
        The new `calibration_curves.id`.
    """
    with connect(db_path) as con:
        cur = safe_execute(
            con,
            "INSERT INTO calibration_curves "
            "(analyte_mz, fit_type, weighting, concentration_unit, slope, "
            "intercept, r_squared, lod, loq, n_points, command_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                analyte_mz, fit_type, weighting, concentration_unit,
                slope, intercept, r_squared, lod, loq, n_points, command_id,
                datetime.now().astimezone().isoformat(),
            ),
            table="calibration_curves",
            logger=logger,
            source=db_path,
        )
        con.commit()
        return int(cur.lastrowid)


@log_call(source="db_path")
def load_calibration_curve(db_path: Path | str, curve_id: int | None = None) -> dict | None:
    """One calibration curve — the most recently fitted one by default.

    Args:
        db_path: The analysis database.
        curve_id: A specific `calibration_curves.id`, or `None` for the
            most recent curve (by `id`, which is also insertion order).

    Returns:
        A dict of every column, or `None` if no curve exists yet (or
        `curve_id` doesn't match one).
    """
    sql = (
        "SELECT id, analyte_mz, fit_type, weighting, concentration_unit, "
        "slope, intercept, r_squared, lod, loq, n_points, command_id, created_at "
        "FROM calibration_curves "
    )
    with connect(db_path) as con:
        try:
            if curve_id is None:
                row = con.execute(sql + "ORDER BY id DESC LIMIT 1").fetchone()
            else:
                row = con.execute(sql + "WHERE id = ?", (curve_id,)).fetchone()
        except sqlite3.OperationalError:
            return None

    if row is None:
        return None
    columns = [
        "id", "analyte_mz", "fit_type", "weighting", "concentration_unit",
        "slope", "intercept", "r_squared", "lod", "loq", "n_points",
        "command_id", "created_at",
    ]
    return dict(zip(columns, row))


@log_call(source="db_path")
def upsert_calibration_point(
    db_path: Path | str,
    sample_id: int | None,
    roi_name: str,
    amount: float,
    amount_unit: str,
    pixel_size: float,
    pixel_size_unit: str,
    area: float,
    concentration: float,
    mean_intensity: float,
    n_pixels: int,
) -> int:
    """Insert one calibration standard, or replace it if `roi_name`
    already has one (`UNIQUE(roi_name)`) — re-adding the same ROI (e.g.
    correcting its concentration) updates in place rather than
    duplicating. One row at a time, not bulk: standards accumulate one
    "Add Standard" action at a time (see the calibration workflow's own
    docs), unlike e.g. `save_target_list_matches`.

    Args:
        db_path: The analysis database.
        sample_id: The sample the ROI was drawn on.
        roi_name: The ROI's name (from the `rois` catalog).
        amount: Total quantity of the analyte in the spot, as entered.
        amount_unit: One of `core.calibration.units.AMOUNT_UNITS`.
        pixel_size: This ROI's pixel edge length.
        pixel_size_unit: `"um"` or `"mm"`.
        area: `core.calibration.units.pixel_area(n_pixels, pixel_size, pixel_size_unit)`.
        concentration: `core.calibration.units.concentration_from_amount(amount, area)`
            — the curve's actual x-value. Computed by the caller, not here.
        mean_intensity: The ROI's mean intensity (see
            `core.calibration.roi_intensity.roi_mean_intensity`).
        n_pixels: The ROI's pixel count.

    Returns:
        The new (or existing, on update) `calibration_points.id`.
    """
    with connect(db_path) as con:
        safe_execute(
            con,
            "INSERT INTO calibration_points "
            "(sample_id, roi_name, amount, amount_unit, pixel_size, pixel_size_unit, "
            "area, concentration, mean_intensity, n_pixels, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(roi_name) DO UPDATE SET "
            "sample_id = excluded.sample_id, amount = excluded.amount, "
            "amount_unit = excluded.amount_unit, pixel_size = excluded.pixel_size, "
            "pixel_size_unit = excluded.pixel_size_unit, area = excluded.area, "
            "concentration = excluded.concentration, "
            "mean_intensity = excluded.mean_intensity, n_pixels = excluded.n_pixels",
            (
                sample_id, roi_name, amount, amount_unit, pixel_size, pixel_size_unit,
                area, concentration, mean_intensity, n_pixels,
                datetime.now().astimezone().isoformat(),
            ),
            table="calibration_points",
            logger=logger,
            source=db_path,
        )
        con.commit()
        row = con.execute(
            "SELECT id FROM calibration_points WHERE roi_name = ?", (roi_name,)
        ).fetchone()
        return int(row[0])


@log_call(source="db_path")
def load_calibration_points(db_path: Path | str) -> pd.DataFrame:
    """Every calibration standard point for this analysis (v1: one
    analysis-wide set, not scoped per curve — see the schema's own
    comment).

    Returns:
        A DataFrame with `id`, `sample_id`, `roi_name`, `amount`,
        `amount_unit`, `pixel_size`, `pixel_size_unit`, `area`,
        `concentration`, `mean_intensity`, `n_pixels`, `created_at`,
        ordered by `concentration`. Empty when there are no standards yet.
    """
    sql = (
        "SELECT id, sample_id, roi_name, amount, amount_unit, pixel_size, "
        "pixel_size_unit, area, concentration, mean_intensity, n_pixels, "
        "created_at FROM calibration_points ORDER BY concentration"
    )
    with connect(db_path) as con:
        try:
            return pd.read_sql_query(sql, con)
        except (pd.errors.DatabaseError, sqlite3.OperationalError):
            return pd.DataFrame()


@log_call(source="db_path")
def delete_calibration_point(db_path: Path | str, roi_name: str) -> bool:
    """Remove one calibration standard.

    Returns:
        True if a row was actually deleted.
    """
    with connect(db_path) as con:
        cur = safe_execute(
            con, "DELETE FROM calibration_points WHERE roi_name = ?", (roi_name,),
            table="calibration_points", logger=logger, source=db_path,
        )
        con.commit()
        return cur.rowcount > 0


@log_call(source="db_path")
def delete_calibration_curve(db_path: Path | str, curve_id: int) -> bool:
    """Remove one calibration curve from the fit history.

    Does NOT touch `calibration_points` — those aren't scoped per curve
    (v1: one analysis-wide set, see the schema's own comment).
    `calibration_unknowns.curve_id` has a real `FOREIGN KEY` though, so
    any unknown still pointing at this curve has it cleared to `NULL`
    first (its last-computed `interpolated_conc`/`amount` are left
    untouched — only the now-dangling curve reference is detached) until
    the next re-fit updates it.

    Returns:
        True if the curve was actually deleted.
    """
    with connect(db_path) as con:
        safe_execute(
            con, "UPDATE calibration_unknowns SET curve_id = NULL WHERE curve_id = ?",
            (curve_id,), table="calibration_unknowns", logger=logger, source=db_path,
        )
        cur = safe_execute(
            con, "DELETE FROM calibration_curves WHERE id = ?", (curve_id,),
            table="calibration_curves", logger=logger, source=db_path,
        )
        con.commit()
        return cur.rowcount > 0


@log_call(source="db_path")
def upsert_calibration_unknown(
    db_path: Path | str,
    sample_id: int | None,
    roi_name: str,
    pixel_size: float,
    pixel_size_unit: str,
    area: float,
    mean_intensity: float,
    n_pixels: int,
    curve_id: int | None = None,
    interpolated_conc: float | None = None,
    amount: float | None = None,
) -> int:
    """Insert one quantified ROI's result, or replace it if `roi_name`
    already has one (`UNIQUE(roi_name)`).

    `curve_id`/`interpolated_conc`/`amount` may be `None` together — an
    unknown ROI can be drawn (and its mean intensity/area recorded)
    before any curve has been fit yet; the GUI shows "no curve yet" for
    it until `fitCalibrationCurve` runs and re-interpolates every
    existing unknown against the fresh fit
    (`update_calibration_unknown_result`).

    Args:
        db_path: The analysis database.
        sample_id: The sample the ROI was drawn on.
        roi_name: The ROI's name.
        pixel_size: This ROI's pixel edge length (same units as the
            standards it'll be interpolated against).
        pixel_size_unit: `"um"` or `"mm"`.
        area: This ROI's physical area (`core.calibration.units.pixel_area`).
        mean_intensity: The ROI's mean intensity.
        n_pixels: The ROI's pixel count.
        curve_id: The `calibration_curves.id` this result was interpolated
            against, or `None` if no curve exists yet.
        interpolated_conc: The interpolated concentration, or `None`.
        amount: `interpolated_conc * area`, or `None` alongside it.

    Returns:
        The new (or existing, on update) `calibration_unknowns.id`.
    """
    with connect(db_path) as con:
        safe_execute(
            con,
            "INSERT INTO calibration_unknowns "
            "(sample_id, roi_name, pixel_size, pixel_size_unit, area, "
            "mean_intensity, n_pixels, curve_id, interpolated_conc, amount, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(roi_name) DO UPDATE SET "
            "sample_id = excluded.sample_id, pixel_size = excluded.pixel_size, "
            "pixel_size_unit = excluded.pixel_size_unit, area = excluded.area, "
            "mean_intensity = excluded.mean_intensity, n_pixels = excluded.n_pixels, "
            "curve_id = excluded.curve_id, interpolated_conc = excluded.interpolated_conc, "
            "amount = excluded.amount",
            (
                sample_id, roi_name, pixel_size, pixel_size_unit, area,
                mean_intensity, n_pixels, curve_id, interpolated_conc, amount,
                datetime.now().astimezone().isoformat(),
            ),
            table="calibration_unknowns",
            logger=logger,
            source=db_path,
        )
        con.commit()
        row = con.execute(
            "SELECT id FROM calibration_unknowns WHERE roi_name = ?", (roi_name,)
        ).fetchone()
        return int(row[0])


@log_call(source="db_path")
def update_calibration_unknown_result(
    db_path: Path | str, roi_name: str, curve_id: int, interpolated_conc: float, amount: float
) -> None:
    """Fill in `curve_id`/`interpolated_conc`/`amount` for an existing
    unknown — called once per existing unknown every time
    `fitCalibrationCurve` (re-)fits, so a re-fit updates every unknown's
    result in place rather than requiring them to be re-drawn.
    """
    with connect(db_path) as con:
        safe_execute(
            con,
            "UPDATE calibration_unknowns "
            "SET curve_id = ?, interpolated_conc = ?, amount = ? WHERE roi_name = ?",
            (curve_id, interpolated_conc, amount, roi_name),
            table="calibration_unknowns",
            logger=logger,
            source=db_path,
        )
        con.commit()


@log_call(source="db_path")
def load_calibration_unknowns(db_path: Path | str) -> pd.DataFrame:
    """Every quantified ROI result for this analysis (v1: one
    analysis-wide set, not scoped per curve).

    Returns:
        A DataFrame with `id`, `sample_id`, `roi_name`, `pixel_size`,
        `pixel_size_unit`, `area`, `mean_intensity`, `n_pixels`,
        `curve_id`, `interpolated_conc`, `amount`, `created_at`, ordered
        by `created_at`. Empty when there are no results yet.
    """
    sql = (
        "SELECT id, sample_id, roi_name, pixel_size, pixel_size_unit, area, "
        "mean_intensity, n_pixels, curve_id, interpolated_conc, amount, created_at "
        "FROM calibration_unknowns ORDER BY created_at"
    )
    with connect(db_path) as con:
        try:
            return pd.read_sql_query(sql, con)
        except (pd.errors.DatabaseError, sqlite3.OperationalError):
            return pd.DataFrame()


@log_call(source="db_path")
def delete_calibration_unknown(db_path: Path | str, unknown_id: int) -> bool:
    """Remove one quantified ROI result.

    Returns:
        True if a row was actually deleted.
    """
    with connect(db_path) as con:
        cur = safe_execute(
            con, "DELETE FROM calibration_unknowns WHERE id = ?", (unknown_id,),
            table="calibration_unknowns", logger=logger, source=db_path,
        )
        con.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Imported analyzed .h5ad datasets (ADR 49)
# ---------------------------------------------------------------------------


@log_call(source="db_path")
def insert_imported_dataset(
    db_path: Path | str,
    *,
    name: str,
    rel_dir: str,
    source_path: str,
    source_kind: str,
    sample_column: str | None,
    layers: list[str],
    samples: list[tuple[str, str | None, int]],
) -> int:
    """Register one imported dataset and the samples it covers.

    Args:
        db_path: The analysis database.
        name: Display name — `UNIQUE`.
        rel_dir: Per-sample `.h5ad` folder, relative to `db_path`'s folder.
        source_path: The user's own source `.h5ad`; stored relative to
            the project when inside it, else absolute (ADR 59).
        source_kind: `"merged"` or `"single"`.
        sample_column: The source's obs column naming each row's sample
            (`None` for a single-sample file).
        layers: The imported files' `.layers` names (for a layer picker).
        samples: `(sample_name, source_label, n_pixels)` per imported
            sample; `sample_name` must already be in `samples`.

    Returns:
        The new `imported_datasets.id`.

    Raises:
        sqlite3.IntegrityError: `name`/`rel_dir` already exists.
        ValueError: a `sample_name` isn't one of this analysis' samples.
    """
    with connect(db_path) as con:
        ids = {}
        for sample_name, _label, _n in samples:
            row = con.execute(
                "SELECT sample_id FROM samples WHERE name = ?", (sample_name,)
            ).fetchone()
            if row is None:
                raise ValueError(f"no sample named {sample_name!r} in this analysis")
            ids[sample_name] = int(row[0])
        cur = safe_execute(
            con,
            "INSERT INTO imported_datasets (name, rel_dir, source_path, source_kind, "
            "sample_column, layers, imported_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                name, rel_dir, _path_for_storage(db_path, source_path),
                source_kind, sample_column,
                json.dumps(list(layers)), datetime.now().astimezone().isoformat(),
            ),
            table="imported_datasets", logger=logger, source=db_path,
        )
        dataset_id = int(cur.lastrowid)
        safe_executemany(
            con,
            "INSERT INTO imported_dataset_samples (dataset_id, sample_id, source_label, "
            "n_pixels) VALUES (?, ?, ?, ?)",
            [(dataset_id, ids[s], label, int(n)) for s, label, n in samples],
            table="imported_dataset_samples", logger=logger, source=db_path,
        )
        con.commit()
        return dataset_id


@log_call(source="db_path")
def load_imported_datasets(db_path: Path | str) -> pd.DataFrame:
    """Every imported dataset of this analysis, for the workspace's
    dataset switcher.

    Returns:
        A DataFrame with `id`, `name`, `rel_dir`, `source_path`,
        `source_kind`, `sample_column`, `imported_at`, ordered by
        `imported_at`. Empty when there are none (or the table doesn't
        exist yet on an analysis the schema refresh hasn't reached).
    """
    sql = (
        "SELECT id, name, rel_dir, source_path, source_kind, sample_column, "
        "imported_at FROM imported_datasets ORDER BY imported_at, id"
    )
    with connect(db_path) as con:
        try:
            return pd.read_sql_query(sql, con)
        except (pd.errors.DatabaseError, sqlite3.OperationalError):
            return pd.DataFrame()


@log_call(source="db_path")
def load_imported_dataset(db_path: Path | str, dataset_id: int) -> dict | None:
    """One imported dataset, with its samples.

    Returns:
        `{"id", "name", "rel_dir", "source_path", "source_kind",
        "sample_column", "layers": [...], "imported_at", "samples": [{"name",
        "sample_id", "source_label", "n_pixels"}, ...]}` (samples ordered by
        `sample_id`), or `None` if no such dataset.
    """
    with connect(db_path) as con:
        try:
            row = con.execute(
                "SELECT id, name, rel_dir, source_path, source_kind, sample_column, "
                "layers, imported_at FROM imported_datasets WHERE id = ?",
                (int(dataset_id),),
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        if row is None:
            return None
        sample_rows = con.execute(
            "SELECT s.name, s.sample_id, i.source_label, i.n_pixels "
            "FROM imported_dataset_samples i JOIN samples s ON s.sample_id = i.sample_id "
            "WHERE i.dataset_id = ? ORDER BY s.sample_id",
            (int(dataset_id),),
        ).fetchall()
    keys = ("id", "name", "rel_dir", "source_path", "source_kind", "sample_column",
            "layers", "imported_at")
    info = dict(zip(keys, row))
    info["source_path"] = _stored_path_here(db_path, info["source_path"])
    info["layers"] = json.loads(info["layers"] or "[]")
    info["samples"] = [
        {"name": n, "sample_id": int(i), "source_label": label, "n_pixels": int(px)}
        for n, i, label, px in sample_rows
    ]
    return info


@log_call(source="db_path")
def delete_imported_dataset_rows(db_path: Path | str, dataset_id: int) -> bool:
    """Remove one imported dataset's rows (its sample rows cascade).
    Files on disk are `core.imported_dataset.delete_imported_dataset`'s job.

    Returns:
        True if a dataset row was actually deleted.
    """
    with connect(db_path) as con:
        safe_execute(
            con, "DELETE FROM imported_dataset_samples WHERE dataset_id = ?",
            (int(dataset_id),), table="imported_dataset_samples",
            logger=logger, source=db_path,
        )
        cur = safe_execute(
            con, "DELETE FROM imported_datasets WHERE id = ?", (int(dataset_id),),
            table="imported_datasets", logger=logger, source=db_path,
        )
        con.commit()
        return cur.rowcount > 0
