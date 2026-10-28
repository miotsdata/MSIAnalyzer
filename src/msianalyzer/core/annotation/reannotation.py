"""MS2 annotation stages, and re-running them on a finished analysis (ADR 63).

The MS2 stages (`group_ms2` -> `precursor_purity` -> `annotate_ms2` ->
`ms2_consensus`) read only `features` and the raw databases' MS2 scans, and
write only the analysis-database tables in `MS2_TABLES`. So they can be
re-run on a finished analysis without touching its `.h5ad` data.

Every annotation state is kept: `annotation_history` has one row per state
(the pipeline's own, each re-annotation, each restore) with its settings
and a snapshot of `MS2_TABLES` in `<analysis folder>/annotation_history/`.
Restoring copies a snapshot back and records a new state, so nothing is
ever overwritten.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from msianalyzer.core import analysis_db
from msianalyzer.core.annotation.annotate import run_annotation
from msianalyzer.core.annotation.consensus import run_consensus
from msianalyzer.core.annotation.group_ms2 import run_grouper
from msianalyzer.core.annotation.precursor_purity import run_precursor_purity
from msianalyzer.core.utils.logging_utils import log_call

logger = logging.getLogger(__name__)

#: Tables owned by the MS2 stages, parents before children (restore
#: inserts in this order and deletes in reverse).
MS2_TABLES = (
    "ms2_cleaning",
    "ms2_background_fragments",
    "ms2_associations",
    "feature_ms2_summary",
    "precursor_purity",
    "feature_ms2_consensus",
    "annotation_libraries",
    "ms2_annotations",
    "feature_annotation_outcome",
)

#: The stages, in order (also the pipeline's step names).
MS2_STAGES = ("group_ms2", "precursor_purity", "annotate_ms2", "ms2_consensus")

#: Config groups a re-annotation can change.
MS2_GROUPS = ("ms2_cleaning", "group_ms2", "purity", "annotate", "consensus")

HISTORY_DIR = "annotation_history"


@dataclass
class Ms2Settings:
    """Everything the MS2 stages read. `align_ppm` is the run's own and is
    not editable afterwards (the features it aligned stay as they are)."""

    ms2_cleaning: object
    group_ms2: object
    purity: object
    annotate: object
    consensus: object
    align_ppm: float

    @classmethod
    def from_sections(cls, sections: dict, align_ppm: float | None = None) -> "Ms2Settings":
        """Each group's defaults, overlaid with the keys of `sections` that
        the group still has — tolerant of a config saved by an older version
        (unlike `Config.from_dict`, which rejects another config version)."""
        from msianalyzer.core.config.config import GROUPS, AlignMzSamples

        def build(group: str):
            cls_ = GROUPS[group]
            names = {f.name for f in dataclasses.fields(cls_)}
            given = sections.get(group) or {}
            return cls_(**{k: v for k, v in given.items() if k in names})

        if align_ppm is None:
            align_ppm = (sections.get("align") or {}).get(
                "align_ppm", AlignMzSamples().align_ppm
            )
        return cls(*(build(g) for g in MS2_GROUPS), align_ppm=float(align_ppm))

    def to_dict(self) -> dict:
        """`{group: {field: value}}` for the `MS2_GROUPS`, JSON-friendly."""

        def plain(v):
            if isinstance(v, Path):
                return str(v)
            if isinstance(v, (list, tuple)):
                return [plain(x) for x in v]
            return v

        return {g: {k: plain(v) for k, v in vars(getattr(self, g)).items()} for g in MS2_GROUPS}


def run_ms2_stages(
    adb_path: Path | str,
    settings: Ms2Settings,
    run_id: str,
    *,
    skip_done: bool = False,
    on_step: Callable[[str, str], None] = lambda step, status: None,
) -> None:
    """Run the four MS2 stages in order, each logged as a `commands` row.

    Args:
        skip_done: Skip a stage whose command already ran for `run_id` —
            the pipeline's resume behaviour. A re-annotation passes False.
        on_step: `(step, status)` callback, status one of
            `"started"`/`"completed"`/`"skipped"`.
    """

    def should_run(step: str) -> bool:
        if skip_done and analysis_db.is_command_already_run(step, run_id, adb_path):
            logger.debug("Already run %s", step)
            return False
        return True

    gm, purity, annotate, consensus = (
        settings.group_ms2, settings.purity, settings.annotate, settings.consensus,
    )

    on_step("group_ms2", "started")
    if should_run("group_ms2"):
        command_id = analysis_db.log_command(
            adb_path,
            command_name="group_ms2",
            arguments={
                **vars(gm),
                "align_ppm": settings.align_ppm,
                "fragmentation_factor_mz_tol_da": purity.fragmentation_factor_mz_tol_da,
                "ms2_cleaning": vars(settings.ms2_cleaning),
            },
            run_id=run_id,
        )
        grouping = run_grouper(
            adb_path,
            assoc_ppm=gm.assoc_ppm,
            align_ppm=settings.align_ppm,
            include_unmatched=gm.include_unmatched,
            command_id=command_id,
            cleaning=settings.ms2_cleaning,
            default_isolation_half_width=gm.default_isolation_half_width,
            fragmentation_factor_mz_tol_da=purity.fragmentation_factor_mz_tol_da,
            flat_fragmentation_min_peaks=gm.flat_fragmentation_min_peaks,
            flat_fragmentation_cv_threshold=gm.flat_fragmentation_cv_threshold,
            flat_fragmentation_min_rel_intensity=gm.flat_fragmentation_min_rel_intensity,
        )
        logger.info(
            "Associated %d MS2 scans across %d features with MS2 coverage",
            len(grouping.associations),
            len(grouping.feature_summary),
        )
    on_step("group_ms2", "completed")

    if getattr(purity, "enabled", True):
        on_step("precursor_purity", "started")
        if should_run("precursor_purity"):
            command_id = analysis_db.log_command(
                adb_path, command_name="precursor_purity",
                arguments={**vars(purity)}, run_id=run_id,
            )
            result = run_precursor_purity(adb_path, purity, command_id=command_id)
            logger.info(
                "Precursor purity: scored %d MS2 scans (%d precursor-confirmed, %d snapped)",
                result.n_scans, result.n_confirmed, result.n_snapped,
            )
        on_step("precursor_purity", "completed")
    else:
        logger.info("Precursor purity disabled; skipping")
        on_step("precursor_purity", "skipped")

    if annotate.library_path:
        on_step("annotate_ms2", "started")
        if should_run("annotate_ms2"):
            command_id = analysis_db.log_command(
                adb_path, command_name="annotate_ms2",
                arguments={**vars(annotate)}, run_id=run_id,
            )
            annotation = run_annotation(adb_path, annotate, command_id=command_id)
            logger.info(
                "Annotated %d MS2 scans over %d features (%d candidate rows)",
                annotation.n_scans_annotated,
                annotation.n_features_annotated,
                len(annotation.rows),
            )
        on_step("annotate_ms2", "completed")
    else:
        logger.info("No annotation library configured; skipping MS2 annotation")
        on_step("annotate_ms2", "skipped")

    if getattr(consensus, "enabled", True):
        on_step("ms2_consensus", "started")
        if should_run("ms2_consensus"):
            command_id = analysis_db.log_command(
                adb_path, command_name="ms2_consensus",
                arguments={**vars(consensus)}, run_id=run_id,
            )
            result = run_consensus(adb_path, consensus, command_id=command_id)
            logger.info(
                "MS2 consensus: picked a scan for %d feature(s) (%d backed by a library hit)",
                result.n_features, result.n_features_scored,
            )
        on_step("ms2_consensus", "completed")
    else:
        logger.info("MS2 consensus disabled; skipping")
        on_step("ms2_consensus", "skipped")


# ---------------------------------------------------------------------------
# snapshots
# ---------------------------------------------------------------------------


def _columns(con: sqlite3.Connection, schema: str, table: str) -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA {schema}.table_info({table})")]


def _snapshot(adb_path: Path, dest: Path) -> None:
    """Copy every `MS2_TABLES` table into a new SQLite file `dest`."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(adb_path)
    try:
        analysis_db.create_analysis_schema(con)  # tables added since the run
        con.execute("ATTACH DATABASE ? AS snap", (str(dest),))
        for table in MS2_TABLES:
            con.execute(f"CREATE TABLE snap.{table} AS SELECT * FROM main.{table}")
        con.commit()
        con.execute("DETACH DATABASE snap")
    finally:
        con.close()


def _clear(con: sqlite3.Connection) -> None:
    for table in reversed(MS2_TABLES):
        con.execute(f"DELETE FROM main.{table}")


def _restore_snapshot(adb_path: Path, src: Path) -> None:
    """Replace every `MS2_TABLES` table's rows with `src`'s, by the columns
    both have (a snapshot may predate a column added since)."""
    con = sqlite3.connect(adb_path)
    try:
        con.execute("ATTACH DATABASE ? AS snap", (str(src),))
        _clear(con)
        for table in MS2_TABLES:
            snap_cols = set(_columns(con, "snap", table))
            if not snap_cols:
                continue  # a snapshot from before the table existed
            cols = ", ".join(c for c in _columns(con, "main", table) if c in snap_cols)
            con.execute(f"INSERT INTO main.{table} ({cols}) SELECT {cols} FROM snap.{table}")
        con.commit()
        con.execute("DETACH DATABASE snap")
    finally:
        con.close()


def _rebuild_report(adb_path: Path, report: dict | None) -> None:
    """Rewrite `summary_report.html` for the new annotation, when the run's
    `report` settings have it enabled. Best effort, like in the pipeline."""
    from msianalyzer.core.config.config import ReportConfig
    from msianalyzer.core.report.summary import build_summary_report

    names = {f.name for f in dataclasses.fields(ReportConfig)}
    cfg = ReportConfig(**{k: v for k, v in (report or {}).items() if k in names})
    if not getattr(cfg, "enabled", True):
        return
    samples = analysis_db.load_samples(adb_path)
    raw = {
        int(sid): analysis_db.get_sample_raw_db_path(adb_path, name)
        for sid, name in zip(samples.get("sample_id", []), samples.get("name", []))
    }
    try:
        build_summary_report(adb_path, raw_db_paths=raw, out_dir=adb_path.parent, config=cfg)
    except Exception:
        logger.exception("summary report rebuild failed (the annotation is unaffected)")


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _n_annotated(adb_path: Path) -> int:
    with sqlite3.connect(adb_path) as con:
        return con.execute(
            "SELECT COUNT(DISTINCT feature_id) FROM ms2_annotations WHERE rank_feature = 1"
        ).fetchone()[0]


def _record(
    adb_path: Path, kind: str, settings: dict, *,
    created_at: str | None = None, restored_from: int | None = None,
) -> int:
    """Snapshot the current MS2 tables and add a history row for them."""
    with sqlite3.connect(adb_path) as con:
        entry_id = con.execute(
            "INSERT INTO annotation_history (created_at, kind, settings_json, "
            "restored_from, n_annotated_features) VALUES (?, ?, ?, ?, ?)",
            (created_at or _now(), kind, json.dumps(settings), restored_from,
             _n_annotated(adb_path)),
        ).lastrowid
        con.commit()
    name = f"{entry_id:03d}_{kind}.db"
    _snapshot(adb_path, adb_path.parent / HISTORY_DIR / name)
    with sqlite3.connect(adb_path) as con:
        con.execute("UPDATE annotation_history SET snapshot = ? WHERE id = ?", (name, entry_id))
        con.commit()
    return entry_id


@log_call(source="adb_path")
def load_history(adb_path: Path | str) -> list[dict]:
    """Every annotation state, oldest first: `id`, `created_at`, `kind`,
    `settings` (dict), `snapshot`, `restored_from`, `n_annotated_features`,
    `current` (the newest one). Empty before the first re-annotation."""
    with analysis_db.connect(adb_path) as con:
        try:
            rows = con.execute(
                "SELECT id, created_at, kind, settings_json, snapshot, restored_from, "
                "n_annotated_features FROM annotation_history ORDER BY id"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    last = rows[-1][0] if rows else None
    return [
        {
            "id": r[0], "created_at": r[1], "kind": r[2], "settings": json.loads(r[3]),
            "snapshot": r[4], "restored_from": r[5], "n_annotated_features": r[6],
            "current": r[0] == last,
        }
        for r in rows
    ]


def _ensure_pipeline_entry(adb_path: Path, pipeline_settings: dict, pipeline_date: str | None) -> None:
    """Record the pipeline's own annotation as the first state, once."""
    if not load_history(adb_path):
        _record(adb_path, "pipeline", pipeline_settings, created_at=pipeline_date)


@log_call(source="adb_path")
def reannotate(
    adb_path: Path | str,
    settings: Ms2Settings,
    run_id: str,
    *,
    pipeline_settings: dict,
    pipeline_date: str | None = None,
    report: dict | None = None,
    on_step: Callable[[str, str], None] = lambda step, status: None,
) -> int:
    """Re-run the MS2 stages with `settings`, replacing their tables; return
    the new history entry's id.

    The current state is snapshotted first (the pipeline's, on the first
    call, with `pipeline_settings`). If a stage fails, that snapshot is put
    back before the error propagates, so the analysis is never left half
    re-annotated.
    """
    adb_path = Path(adb_path)
    _ensure_pipeline_entry(adb_path, pipeline_settings, pipeline_date)
    current = load_history(adb_path)[-1]
    with sqlite3.connect(adb_path) as con:
        _clear(con)
        con.commit()
    try:
        run_ms2_stages(adb_path, settings, run_id, on_step=on_step)
    except Exception:
        logger.exception("re-annotation failed; restoring the previous annotation")
        _restore_snapshot(adb_path, adb_path.parent / HISTORY_DIR / current["snapshot"])
        raise
    entry_id = _record(adb_path, "reannotate", settings.to_dict())
    _rebuild_report(adb_path, report)
    return entry_id


@log_call(source="adb_path")
def restore_annotation(
    adb_path: Path | str,
    entry_id: int,
    *,
    pipeline_settings: dict,
    pipeline_date: str | None = None,
    report: dict | None = None,
) -> int:
    """Make history entry `entry_id`'s annotation the current one again, as
    a new `"restore"` entry; return its id."""
    adb_path = Path(adb_path)
    _ensure_pipeline_entry(adb_path, pipeline_settings, pipeline_date)
    entry = next((e for e in load_history(adb_path) if e["id"] == entry_id), None)
    if entry is None or not entry["snapshot"]:
        raise ValueError(f"No saved annotation #{entry_id}.")
    _restore_snapshot(adb_path, adb_path.parent / HISTORY_DIR / entry["snapshot"])
    new_id = _record(adb_path, "restore", entry["settings"], restored_from=entry_id)
    _rebuild_report(adb_path, report)
    return new_id


@log_call(source="adb_path")
def delete_history_entry(adb_path: Path | str, entry_id: int) -> None:
    """Forget one past annotation state and delete its snapshot file. The
    current state can't be deleted."""
    adb_path = Path(adb_path)
    entry = next((e for e in load_history(adb_path) if e["id"] == entry_id), None)
    if entry is None:
        raise ValueError(f"No saved annotation #{entry_id}.")
    if entry["current"]:
        raise ValueError("The current annotation can't be deleted.")
    if entry["snapshot"]:
        (adb_path.parent / HISTORY_DIR / entry["snapshot"]).unlink(missing_ok=True)
    with sqlite3.connect(adb_path) as con:
        con.execute("DELETE FROM annotation_history WHERE id = ?", (entry_id,))
        con.commit()
