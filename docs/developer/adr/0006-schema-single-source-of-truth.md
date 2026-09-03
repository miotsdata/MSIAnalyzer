# 6 — One schema builder per database

**Status:** Accepted

## Context

Tests need database fixtures whose schema matches production. If a fixture
hand-writes `CREATE TABLE`, it drifts from the real schema the moment a column
changes, and the drift is invisible until an integration test fails obscurely.

Separately, the grouper first shipped with its own `create_grouper_schema` in
`annotation/group_ms2.py`, creating three tables lazily inside `persist_grouping`
— a second place the analysis schema was defined.

## Decision

Exactly one function builds each database's schema, and everything else calls it:

- **Raw DB** → `parser.mzml_parser.create_raw_schema(con)` (with
  `init_raw_db(path)` wrapping it with pragmas). `MzmlParser._init_ms1_db`
  delegates to it; the test factory `materialize_ms2_db` calls `init_raw_db`.
- **Analysis DB** → `analysis_db.create_analysis_schema(con)` (with
  `init_analysis_db(path)` wrapping it). This now **includes the grouper
  tables** (`ms2_associations`, `ms2_window_features`, `feature_ms2_summary`).
  `group_ms2.create_grouper_schema` was removed; `persist_grouping` calls
  `create_analysis_schema` (every statement is `IF NOT EXISTS`, so calling it on
  an existing DB is a no-op).

All statements use `CREATE TABLE/INDEX IF NOT EXISTS`, so the builders are
idempotent and safe to call from `init_*`, from `persist_grouping`, and from a
re-run.

## Consequences

- A schema change is made in one place and every fixture picks it up.
- `annotation` now imports `create_analysis_schema` from `analysis_db`
  (no cycle — `analysis_db` imports nothing from `annotation`).
- Tests that need only the grouper tables still get the whole analysis schema;
  harmless, and closer to production.
