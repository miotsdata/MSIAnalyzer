# 37 — Retroactively refresh an analysis DB's schema when its workspace opens

**Status:** Accepted

## Context

[ADR 33](0033-annotations-table-partial-index.md) added an index that
speeds up the Annotations table, but explicitly noted the limitation: this
project has no migration system, so an analysis run before that change
keeps its old (unindexed) schema until re-run — the index is only ever
created by `create_analysis_schema`, called once at `Run.run_core` start.
Asked directly afterward: can an already-run analysis get the benefit
without a full re-run?

## Decision

`AnalysisBridge.ensureSchemaCurrent(analysis_db_path)`: a thin wrapper
around the existing `analysis_db.init_analysis_db` (`connect` +
`create_analysis_schema` + `commit`), called once by `AnalysisPage.qml`'s
`onAnalysisChanged` the moment an analysis workspace opens. Every
statement in `create_analysis_schema` is `CREATE TABLE`/`CREATE INDEX IF
NOT EXISTS` — no data-mutating statements — so running it against an
already-populated, already-current database is a cheap, safe no-op, and
against an older one it adds exactly the missing structure (this index,
and any future one) without touching a row of data.

`analysis_db.connect()`'s own docstring warns callers to "issue no DDL" —
that's scoped to concurrent per-sample *worker processes* racing a schema
lock during an active pipeline run (`ProcessPoolExecutor` workers hitting
the same file at once); it doesn't apply here, since the GUI only ever
opens an already-*finished* analysis through a single connection, with no
concurrent writers to race.

This is deliberately general, not a one-off patch for `idx_ann_rank_feature_1`
specifically — any future addition to `create_analysis_schema` (another
index, a new table) now reaches already-run analyses the same way, the
first time their workspace is opened after upgrading, with no separate
migration step to remember.

## Alternatives considered

- **A real migration system** (versioned schema migrations, tracked in a
  table). Rejected as overkill for what `create_analysis_schema` already
  gives for free — every statement being independently `IF NOT EXISTS`
  means "just re-run the whole schema function" already *is* a trivial,
  correct migration mechanism for additive changes (new tables/indexes).
  Would only stop being sufficient for a change that needs to *alter* or
  *drop* existing structure, which none of this project's schema changes
  to date have needed.
- **Run it from `analysis_db.connect()` itself**, so every read gets it
  automatically. Rejected — `connect()`'s own "no DDL" contract exists
  specifically so the hot, frequently-called, potentially-concurrent read
  path never has to think about schema locks; keeping the ensure-call at
  the one GUI entry point (`AnalysisPage.qml` opening) instead of on every
  connection is both cheaper (once per session, not once per query) and
  keeps that contract intact for the pipeline's own concurrent writers.

## Consequences

- Closes the gap [ADR 33](0033-annotations-table-partial-index.md) left
  open: an existing analysis now gets the index the next time its
  workspace is opened in the GUI, not only on re-run.
- The CLI (`msianalyzer run`) is unaffected — this is a GUI-only hook;
  `Run.run_core`'s own `init_analysis_db` call already keeps a *freshly
  run* analysis current regardless.
