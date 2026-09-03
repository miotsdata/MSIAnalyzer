# 2 — MS2→feature association design

**Status:** Accepted

## Context

The old `ms2_grouper` / `ms2_annotator` modules fused two concerns — tying MS2
scans to something, and scoring them against a library — and wrote back into the
raw MS2 database. Annotation needs rebuilding on top of the
[raw/analysis split](0001-raw-vs-analysis-db-split.md). The re-scoring loop (new
library, tweaked parameters, different `top_n`) is run often; the association is
not.

## Decision

Split annotation into two stages with separate outputs, both writing only to the
analysis DB:

- **Stage A — association (the "grouper"), `core/annotation/group_ms2.py`.**
  Deterministic, cheap, no library. Each MS2 scan → a `feature_id` from
  `align_mz_across_samples`.
- **Stage B — library annotation** (planned). Consumes Stage A + libraries +
  scoring parameters. Re-runnable without redoing Stage A.

Grouper rules:

- **Match key:** `precursor_mz` (same physical quantity as a feature). Fall back
  to `isolation_window_target` only when `precursor_mz` is null; record which in
  `match_key`.
- **Isolation window is a bracket, not a matcher.** Features in
  `[target − lower, target + upper]` are the candidate set and the chimera count
  (`n_features_in_window`); the primary pick is the nearest candidate within
  `assoc_ppm`.
- **The feature list *is* the grouping unit.** No separate "groups" table —
  `features` rows are the groups, `ms2_associations` is the membership table.
- **Chimeric scans** associate to their nearest feature only; every in-window
  feature and its ppm difference are still recorded
  ([ADR 4](0004-two-table-association-storage.md)). Conservative annotation
  transfer (duplicate into every co-isolated feature) is deferred to Stage B.
- **Nothing is filtered** ([ADR 3](0003-no-min-peaks-filter-flag-instead.md)).

Design of the code: a pure core (`associate_scan`, `group_ms2`,
`summarize_features`, `detect_precursor_only`) tested on plain arrays/dicts, and a
thin IO layer (`persist_grouping`, `run_grouper`) that reads raw MS2 DBs and
writes the analysis DB.

## Consequences

- Stage A's output (`ms2_associations`, `feature_ms2_summary`) is independently
  useful: "which features have MS2 coverage, in how many samples, how clean".
- `run.py` gained one run-wide `group_ms2` step after alignment.
- Config gained a `group_ms2` group; config schema `version` 3 → 4.
- Stage B's per-scan-vs-consensus scoring decision is still open; the
  `precursor_only` flag lets a consensus exclude non-fragmented scans.
