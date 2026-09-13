# 21 — Representative-compound selection: prefer more matched peaks within a score tolerance

**Status:** Accepted

## Context

User-reported, root-caused live against a real run (Arturo,
`output/2026-09-12_gui3`, feature 76, m/z 89.0253 — the lactic-acid
family): the Annotations table showed **(S)-LACTATE** as the feature's
representative compound (score 0.9330, matched **1/1** library peaks) over
**Lactic acid** (score 0.8572, matched **3/3** library peaks) — a
trivially-easy single-peak match outranking a richer, more thoroughly
confirmed three-peak match from a *different* library entry.

Tracing the exact numbers: `score = dot_product_score**w_dot *
lib_coverage**w_lib * emp_coverage**w_emp`
(`core/annotation/spectral_match.reverse_dot_product`). Both candidates had
`dot_product_score ≈ 1.0` and `lib_coverage = 1.0` (every library peak
matched, in both cases) — the gap came entirely from `emp_coverage`
(matched / *total filtered empirical peaks in that scan*), which is a
property of the empirical scan, not of how well-supported the identification
is: the 1-peak library entry happened to be matched at an unusually clean
scan (2 filtered peaks total), while the 3-peak library entry's best match
landed on a busier scan (14 filtered peaks) — depressing `emp_coverage`
even though the match itself covered every characteristic fragment the
richer reference spectrum has. The `w_emp` exponent is already low by
default in this run (`0.1`, per [ADR 10](0010-score-weights-and-flat-fragmentation.md)'s
mitigation for exactly this class of problem) — even so, the gap survives.

The compound label everywhere in the GUI/report (Annotations table's
per-feature row, Visual Inspection's feature-selector labels, MS1's
category coloring, the report's "Top features") ultimately reduces to
picking **one row** to represent a feature, currently just "whichever
scored highest" — with zero regard for how many peaks that identification
actually rests on. `score` itself is a comparison-quality metric, correctly
independent of any global "prefer more peaks" adjustment (baking that in
would make `score` non-comparable across runs/features with different peak
counts, undermining [ADR 10](0010-score-weights-and-flat-fragmentation.md)'s
"unweighted, directly-comparable-across-runs" guarantee for the diagnostic
columns).

## Decision

New `AnnotateConfig.representative_score_tolerance` (default `0.05`):
candidates within this many `score` points of a feature's (or a
feature+sample's) top score are pooled, and the one with the most
`n_matched_peaks` wins — not necessarily the top-scoring one. Everything
outside that pool, and every rank below the winner, stays plain `score`
descending, untouched. `score` itself, and every row's own stored fields,
are never modified — this only changes which single row lands at
`rank_feature == 1` / `rank_feature_sample == 1`.

Implemented in `annotate._rank_rows_by_score` (used by
`assign_feature_ranks` for both row-level ranks; the two scan-level ranks —
`rank_scan_feature`/`rank_scan_feature_sample`, "which scan", not "which
compound" — are a different question and untouched). `0` disables the
rule entirely (plain highest-score-wins, ties broken by `sorted()`'s stable
order — the old behavior).

Every reader that used to pick "the" compound for a feature via
`feature_compound_scores`'s `MAX(score)` (a view grouped by
`(feature_id, inchikey)`, with no notion of `rank_feature` at all) now
instead reads `ms2_annotations WHERE rank_feature = 1` directly — the same
mechanism `report/summary.py`'s "Top features" table already used, so this
change also fixes the report for free. New `analysis_db.
load_feature_representative_annotations` (feeds the GUI Annotations
table's `AnalysisBridge.getAnnotationTable`); `load_feature_list` and
`load_feature_categories` swap their `ROW_NUMBER() OVER (... ORDER BY
best_score DESC)` subquery over `feature_compound_scores` for a plain
`JOIN ms2_annotations ON rank_feature = 1` — simpler SQL, and one single
source of truth for "the representative row" instead of two independently
computed ones that could disagree. `feature_compound_scores` itself is
unchanged and still used for its own purpose: enumerating every distinct
plausible compound above a score cutoff (report's compound-diversity
counts), not picking a single winner.

No `Config.version` bump — a new field with a default, existing configs
unaffected (same reasoning as the already-parsed-samples change,
[ADR 20](0020-already-parsed-db-only-samples.md)).

## Consequences

- **Re-run needed** for existing analyses to see the corrected
  representative pick — `rank_feature` is stamped once at `annotate_ms2`
  time, not recomputed live.
- The default `0.05` tolerance does **not** flip the feature-76 case above
  (its gap is ~0.076) — deliberately conservative rather than tuned to one
  example; a user who wants that case to flip needs to raise
  `representative_score_tolerance` (e.g. to `0.1`) for their own run.
- A feature with 3+ real candidates within tolerance of each other now
  picks by peak count among just that pool — a 4th candidate further down
  is never reconsidered even if it has more peaks than the pool's winner
  but sits just outside the tolerance band. This is intentional (bounded,
  predictable effect) rather than "always pick the global max matched-peak
  count," which could let a very low-scoring but many-peak candidate win
  representative status purely on peak count with no real spectral
  similarity.
