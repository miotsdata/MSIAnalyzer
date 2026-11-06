# 69 — Shared drive: DELETE journal after a run + "in use elsewhere" marker

**Status:** Accepted

## Context

Analyses live on the lab SMB share, mounted with `nobrl` (SQLite needs it
there, see the SMB notes in the memory/history). With `nobrl`, file locks
never leave the computer that takes them, so neither SQLite nor HDF5 can see
that another computer has the same analysis open. Two problems followed when
the same analysis was opened on two computers:

- The analysis database stayed in **WAL** mode forever. WAL's `-shm` file is
  shared memory and only works between processes of one computer; another
  computer could read an outdated or inconsistent view ("No data", empty
  sections) or, if both wrote, damage the database.
- **Opening an analysis writes** (schema refresh, `.var` backfill), and
  the user can write more (ROIs, Moran's I, normalization, ...). A reader on
  the other computer could catch a half-written `.h5ad` (blank tile).

## Decision

- **WAL only during a run.** `analysis_db.connect` no longer sets the
  journal mode. `Run` switches the analysis DB to WAL after creating it,
  and in a `finally` puts the analysis DB and every raw DB it touched back
  to DELETE (`Run._end_run_dbs`, `utils.db.set_journal_mode`; best effort,
  logged). Opening an analysis in the GUI also switches it to DELETE, for
  analyses run before this change.
- **In-use marker** (`core/utils/in_use.py`): `<analysis db>.inuse`, JSON
  with host, user, pid, a token, `since`, `heartbeat`. The holder refreshes
  it every 60 s from a daemon thread; older than 5 min = stale (crash). Only
  another *host* counts as "elsewhere".
  - **Run** claims it when it creates the analysis DB and refuses
    (`InUseError`) if another computer holds it.
  - **GUI**: `AnalysisPage` calls `AnalysisBridge.claimAnalysis` on open and
    `releaseAnalysis(path)` on close; `main.py` releases on quit.
- **Read-only when held elsewhere** (user's choice: full read-only):
  - A banner at the top of the analysis page names the other computer.
  - No writes on open (schema update, backfills, journal mode).
  - Every database-writing bridge slot is wrapped in `@_writes(fallback)`:
    it returns its own error shape with "read-only" (async ones emit their
    `...Failed` signal).
  - `h5ad_io.open_for_write` raises `ReadOnlyError` (an `OSError`) for files
    under a read-only analysis folder, covering `.h5ad` writes outside the
    bridge slots.
  - Entry points are disabled: Analyses menu (import, target list,
    normalize, re-annotate), Draw ROI, Coregister, Scale bar, Predict
    formula, Add Standard/Sample, Compute Moran's I / feature correlation,
    reset category colors, remove imported dataset.

## Consequences

- Read-only stays until the analysis is reopened, even if the other
  computer closes it meanwhile.
- The marker is per analysis, not per raw DB: a raw DB shared by two
  analyses open on two computers is not protected (H&E attachment writes to
  it).
- Clocks: "stale" compares the other computer's timestamp with ours; 5 min
  tolerates normal clock skew.
- A GUI quit mid-run leaves the run's marker until it goes stale.
