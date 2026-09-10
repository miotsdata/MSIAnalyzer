# 9 — Consuming precursor purity + per-feature MS2 consensus

**Status:** Accepted

## Context

[ADR 8](0008-precursor-ion-purity.md) added the `precursor_purity` stage but
deliberately left the rest of the pipeline untouched: `ms2_annotations` still
knew only the grouper's feature-list `n_features_in_window`, and nothing folded
purity into a per-feature decision. Two gaps followed:

1. Reviewing a library hit meant a manual join to `precursor_purity` to learn how
   clean the scan was, and there was no way to *not* score obviously
   contaminated scans.
2. A feature fragmented many times (across samples, pixels, isolation quality)
   had no single "use this spectrum" answer. `ms2_annotations.rank_feature`
   ordered scans by library score alone — blind to purity and peak richness, and
   undefined when no library ran.

## Decision

**Annotation consumes purity.**

- `ms2_annotations` gains `purity` and `runner_up_rel_int`, left-joined from
  `precursor_purity` on `(sample_id, scan_id)` in the annotation worker and
  stamped on every row.
- `AnnotateConfig.min_purity` (default `None`): when set, a scan whose purity is
  *known* and below it is skipped — parallel to `annotate_chimeric = false`. An
  unscored scan (`precursor_found = 0`, or the purity stage disabled) is always
  kept; `min_purity` never guesses.
- `is_chimeric` / `n_features_in_window` are **unchanged** — the feature-list
  count stays as a coarse flag; purity is the measurement to filter on.

**A per-feature consensus stage.**

- New module `annotation/consensus.py`, new run-wide step `ms2_consensus` after
  annotation, new table `feature_ms2_consensus` (one row per MS2-bearing
  feature), new config group `consensus`.
- `consensus_score = best_score x purity_term x peak_term` where
  `best_score` is the scan's `ms2_annotations.score` at `rank_ms2 = 1` (or `1.0`
  when annotation did not run), `purity_term = clamp(purity or neutral_purity)`,
  `peak_term = min(1, n_peaks / target_peaks)` on the scan's fragment count.
- `min_purity` (its own knob) drops scans from the *pick* but not from `n_ms2`.
- The stage is optional and independent: it runs library-free (score term
  collapses to purity x peaks) and purity-free (`neutral_purity` stands in).

Both changes are additive to the schema and folded into
`create_analysis_schema` ([ADR 6](0006-schema-single-source-of-truth.md)).
Config `version` 6 → 7 (the same bump also introduces the `report` group, see
below).

## Alternatives considered

- **Redefine `is_chimeric` as `purity < cutoff`.** Freezes a threshold into the
  table and loses the cheap feature-list signal. Rejected — keep both, filter at
  query time.
- **Push the consensus into `ms2_annotations.rank_feature`.** That column is a
  library-score ranking by definition and undefined without a library; consensus
  needs to work library-free and fold in non-library signals. A dedicated
  one-row-per-feature table is clearer and cheaper to query.
- **A learned/weighted score.** Premature — a transparent product of three
  bounded terms is inspectable and tunable (`target_peaks`, `neutral_purity`),
  and every input is already on the row.

## Consequences

- `ms2_annotations` is two columns wider; `persist_annotations` and `_ANN_COLS`
  updated. Old rows from a pre-v7 database read back with `purity` NULL.
- `run.py` gains one run-wide step (`ms2_consensus`); `feature_ms2_consensus` is
  small (≤ one row per feature).
- Re-running the consensus stage replaces every `feature_ms2_consensus` row and
  touches nothing else.
- The consensus reads `ms2_annotations` at `rank_ms2 = 1`; it must run *after*
  annotation, which `run_core` guarantees.
