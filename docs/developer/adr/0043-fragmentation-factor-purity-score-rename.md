# 43 — `fragmentation_factor`/`purity_score` rename; flat-fragmentation scans excluded from library matching

**Status:** Accepted

## Context

Two related reports from the same conversation:

1. A `flat_fragmentation`-flagged scan (see [ADR 10](0010-score-weights-and-flat-fragmentation.md))
   was still being scored against the spectral library — confirmed by
   reading `annotate_feature`, which only ever filtered on
   `min_precursor_frac`. Wanted: keep computing and flagging it (still
   useful for QC/reporting), but never actually library-match it — a
   flat/noisy spectrum isn't a real fragmentation pattern to begin with.
2. `GroupMs2Config.precursor_only_tic_frac`/`precursor_only_mz_tol_da`
   (Stage A, `group_ms2.py`) and `PurityConfig`'s `precursor_frac`
   (Stage A′, `precursor_purity.py`) were assumed to be duplicate
   settings for one metric, split confusingly across two config tabs.
   They're not literally duplicates — `detect_precursor_only` reads only
   the MS2 scan's *own* fragment spectrum (is the surviving signal still
   sitting on the precursor mass); `precursor_frac` reads the *parent
   MS1*'s isolation window (was the isolation window itself
   co-isolation-free) — but the split was still genuinely confusing
   (same "precursor is dominant" question asked twice, in two stages,
   under near-identical names). Renamed for clarity: the first is now
   **`fragmentation_factor`** (a continuous `[0, 1]` score, not a bool —
   confirmed with the user before implementing), the second **`purity_score`**.

## Decision

### Flat fragmentation → hard filter

`annotate_feature` (and its early-exit pre-filter in
`_annotate_feature_batch`, for the same reason `min_purity_score`
already short-circuits before reading fragment arrays) now skips any
scan with `flat_fragmentation` set, unconditionally — no new config
field; the existing `GroupMs2Config.flat_fragmentation_cv_threshold`
already controls whether a scan gets flagged in the first place. The
column itself, and `group_ms2`'s own flagging, are unchanged — only
`annotate`'s (Stage B) behavior toward an already-flagged scan changed.

### `precursor_only` → `fragmentation_factor` (continuous)

- `group_ms2.detect_precursor_only(...) -> bool` (threshold baked in,
  `tic_frac` param) → `compute_fragmentation_factor(...) -> float | None`
  (`1.0 -` the fraction of a scan's total intensity within
  `mz_tol_da` of its precursor; `None` when nothing to compute from —
  same "unscored, not zero" convention `purity_score` already used).
  The old "base peak must also sit on the precursor" gate is dropped —
  redundant once the value is continuous rather than a single yes/no
  call.
- `ms2_associations.precursor_only INTEGER` → `fragmentation_factor REAL`;
  `feature_ms2_summary.n_precursor_only` (a count, dead code — nothing
  ever read it, confirmed by grep) → `mean_fragmentation_factor REAL`
  (a real per-feature aggregate, actually useful); `ms2_annotations`
  carries the same rename through.
  `ReportConfig` gains `fragmentation_factor_cutoff: float = 0.2` (the
  old default `tic_frac=0.8`'s exact complement) purely for the
  report's "how many low-fragmentation scans" count — mirrors
  `purity_score_cutoff`'s existing report-only-threshold role, not a
  new filter.
- `precursor_only_mz_tol_da` **moved** from `GroupMs2Config` to
  `PurityConfig` as `fragmentation_factor_mz_tol_da` — the *computation*
  stays in `group_ms2.py` (it only needs the scan's own already-loaded
  arrays, same as `flat_fragmentation`, unlike `purity_score` which
  needs the parent MS1), but the *setting* now lives alongside every
  other "something about the precursor is off" knob, which is the
  actual complaint being fixed. `precursor_only_tic_frac` is dropped
  entirely (no longer a compute-time threshold with nothing left to
  parametrize).
- `PurityConfig`'s docstring now opens with an explicit two-metric
  breakdown (`purity_score` vs `fragmentation_factor` — which scan each
  reads, which stage computes it) instead of only describing purity.
- `fragmentation_factor_mz_tol_da`'s docstring includes a worked example
  (precursor `500.25`, tolerance `2.0` → band `[498.25, 502.25]`) per
  the user's explicit ask for one.

### `precursor_frac` → `purity_score`

Straight rename, no semantic change: the `PurityRow`/`AnnotationRow`/
`ScanStat`/`ConsensusRow` fields, the `precursor_purity.purity_score`
and `feature_ms2_consensus.purity_score`/`ms2_annotations.purity_score`
columns, `AnnotateConfig.min_precursor_frac` →
`min_purity_score`, `ConsensusConfig.neutral_precursor_frac`/
`min_precursor_frac` → `neutral_purity_score`/`min_purity_score`,
`consensus.precursor_frac_term` → `purity_score_term`,
`ReportConfig.purity_cutoff` → `purity_score_cutoff`, and every report
figure/prose string that named the value directly.

### Schema

`Config.version` bumped 16 → 17 (field renames/removals break
`Config.from_dict`'s strict validation on an old saved config — same
convention as every prior version bump). Every renamed/moved DB column
gets a matching `_ensure_column` retrofit call so an old analysis's
database can still be *opened* without error (`ensureSchemaCurrent`,
ADR 37) — the old columns (`precursor_only`, `precursor_frac`,
`n_precursor_only`) are left in place, unread by any new code, not
dropped (no destructive migration exists in this codebase). Real *data*
for the new columns needs a re-run, same accepted limitation as every
prior additive schema change. One ordering bug found and fixed while
adding this: the new retrofit calls originally sat together near the
end of `create_analysis_schema`, after `CREATE INDEX
idx_purity_value ON precursor_purity(purity_score)` — which fails
outright against an old table that doesn't have that column yet. Each
retrofit now sits directly after its own table's `CREATE TABLE`, before
that table's own indexes (caught by a new test seeding a full old-style
schema across all five affected tables, not just `ms2_annotations`).

## Alternatives considered

- **Keeping `detect_precursor_only` a boolean, just renaming it.**
  Rejected — explicitly asked for the richer continuous score once the
  rename was already touching every call site.
- **Moving `compute_fragmentation_factor` itself into
  `precursor_purity.py`**, alongside its now-co-located config field.
  Rejected — it only needs the MS2 scan's own arrays (same as
  `detect_flat_fragmentation`, which isn't moving), not the parent MS1;
  moving it would separate it from the sibling check it's computed
  alongside for no functional gain, just to match where its config
  field happens to live now.

## Consequences

- Existing analyses' `summary_report.html`/`summary.json` will show
  `n_low_fragmentation`/`n_confident_not_low_fragmentation` as `0`
  (or based on stale, unpopulated `NULL` `fragmentation_factor` values)
  until re-run — same caveat as every other additive-schema change.
- `AnnotateConfig`/`ConsensusConfig`/`GroupMs2Config`/`PurityConfig`/
  `ReportConfig` field names changed; any external YAML/TOML config
  file written against version 16 needs updating (or simply re-saving
  from the GUI, which uses the current schema automatically).
