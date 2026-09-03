# 4 — Two-table storage for MS2 associations

**Status:** Accepted

## Context

A chimeric MS2 scan has several features inside its isolation window, and the
grouper must record *"a ppm difference and a flag for each"*. A flat
one-row-per-scan table cannot express that; a flat one-row-per-(scan, candidate)
table makes the common case (one clean match) awkward to join against for
annotation.

## Decision

Three tables in the analysis DB, defined in
`analysis_db.create_analysis_schema`:

- **`ms2_associations`** — one row per MS2 scan. 1:1 with scans, so Stage B
  annotation joins cleanly on `feature_id`. Holds the primary pick, the
  isolation-window values, `ppm_offset`, `n_features_in_window`,
  `nearest_other_feature_ppm`, `precursor_target_delta_ppm`, `precursor_only`,
  and scan metadata.
- **`ms2_window_features`** — one row per (association, feature inside the
  isolation window). `ppm_diff` (signed, vs the match value), `within_tol`,
  `is_primary`. **Always populated** — a clean single match is one row with
  `is_primary = 1`; a chimeric scan is several rows, exactly one primary; an
  unassigned scan with an empty window is zero rows.
- **`feature_ms2_summary`** — one row per feature, the roll-up
  ([ADR 3](0003-no-min-peaks-filter-flag-instead.md)).

`ms2_window_features.association_id` is `ON DELETE CASCADE`, so clearing
`ms2_associations` on a re-run drops the child rows with it.

## Alternatives considered

- **Single flat table** (sample + scan + library + compound + scores per row) —
  fine as an export view, wrong as the primary store: repeats metadata and
  conflates the empirical scan, the candidate, and the match.
- **JSON column of window features on `ms2_associations`** — cannot answer
  "which scans co-isolate feature X" without scanning every row.

## Consequences

- Two write paths in `persist_grouping`, one `executemany` each.
- "Conservative annotation transfer" is available later for free: Stage B can
  join annotations to `ms2_window_features` instead of `ms2_associations`.
- `feature_ms2_summary` is a derived table — always rebuilt, never migrated.
