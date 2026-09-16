# 33 — Partial index for `ms2_annotations.rank_feature`

**Status:** Accepted

## Context

Opening the Annotations tab on a real analysis was reported as "now very
slow." Root-caused directly, not guessed: `load_feature_representative_annotations`
(`analysis_db.py`, backs the GUI's Annotations table) filters
`ms2_annotations` with a bare `WHERE rank_feature = 1` inside its `ms2_rep`
CTE — a scan of the *entire* `ms2_annotations` table, which holds one row
per scored candidate per scan per feature per library, not one row per
feature. `rank_feature` had no index at all (`create_analysis_schema`
already indexes `feature_id`, `score`, `inchikey`, and `(sample_id,
scan_id)`, but never `rank_feature`). `load_feature_list` (backs Visual
Inspection's feature picker) joins on `best.feature_id = f.feature_id AND
best.rank_feature = 1` — better-shaped (a per-feature lookup, not a bare
table-wide filter) but benefits from the same missing index too.

Confirmed with `EXPLAIN QUERY PLAN` against the real query text from both
functions (not a synthetic approximation): before this change, the
`ms2_rep` CTE's query plans as a full `SCAN` of `ms2_annotations`.

## Decision

One partial index, tailored to exactly the predicate both queries filter
on:

```sql
CREATE INDEX IF NOT EXISTS idx_ann_rank_feature_1
ON ms2_annotations(feature_id) WHERE rank_feature = 1
```

A **partial** index (SQLite-native), not a plain index on `rank_feature` —
only the ~1-row-per-feature "representative" rows are ever indexed, not
every candidate row, which is both smaller and exactly matches what these
two queries actually select. Re-verified with `EXPLAIN QUERY PLAN` against
both functions' real query text after adding it:
`load_feature_representative_annotations`'s CTE now plans as `SEARCH ...
USING INDEX idx_ann_rank_feature_1`; `load_feature_list`'s join now uses it
as a **covering** index (`SEARCH best USING COVERING INDEX
idx_ann_rank_feature_1 (feature_id=?)`) — no row lookup into the base table
needed at all for that join.

## Alternatives considered

- **A plain (non-partial) index on `rank_feature` alone.** Rejected —
  every query that cares about `rank_feature` here also wants `feature_id`
  it's paired with (or is satisfied by an index that already implies it as
  the indexed rowset), and a full index would carry every candidate row
  (multiple per feature) instead of just the representative ones.
- **A composite `(feature_id, rank_feature)` index** (not partial).
  Rejected — functionally similar for `load_feature_list`'s join, but the
  partial form is strictly smaller (skips every non-rank-1 row entirely,
  where a composite index still has one entry per candidate row) for the
  same query-plan benefit.

## Consequences

- New analyses get this automatically — `create_analysis_schema` runs once
  at `Run.run_core` start (`init_analysis_db`), and every index statement
  here is already idempotent (`IF NOT EXISTS`).
- **An already-run analysis does not get this retroactively from a
  re-run alone** — but does get it automatically the next time its GUI
  workspace is opened, without a re-run: see
  [ADR 37](0037-retroactive-schema-refresh-on-analysis-open.md).
