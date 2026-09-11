# 10 — Configurable score weights + flat-fragmentation flag

**Status:** Accepted

## Context

Real runs kept showing the same shape: `dot_product_score` and `lib_coverage`
both high, `emp_coverage` low, and the combined `score` collapsing anyway.
`reverse_dot_product`'s formula —
`score = dot_product_score * sqrt(lib_coverage * emp_coverage)` — is a fixed,
unnormalised weighted-geometric-mean (exponents `1, 0.5, 0.5`). A real MSI
spectrum legitimately carries matrix/background fragment peaks the library
was never going to have, so `emp_coverage` is depressed for reasons that have
nothing to do with match quality, and the multiplicative formula let that one
term crush an otherwise strong match. There was no way to lean the score away
from it without editing code.

Separately: some MS2 scans show many fragment peaks at different m/z but
near-identical height — a "comb" — which is far more consistent with
chemical/electronic noise or an isobaric co-isolation smear than genuine
CID/HCD fragmentation, which decays (one or a few dominant fragments, several
minor ones). Nothing flagged this; such scans scored and annotated exactly
like clean fragmentation.

## Decision

**Configurable score weights.** `reverse_dot_product` (`spectral_match.py`)
takes three new exponent params — `weight_dot`, `weight_lib_coverage`,
`weight_emp_coverage` (defaults `1.0, 0.5, 0.5`, reproducing the original
formula exactly). Only the final `score` is weighted; `dot_product_score`,
`lib_coverage`, `emp_coverage` and `coverage_score` stay the unweighted,
directly-comparable-across-runs diagnostics they always were. Exposed as
`AnnotateConfig.score_weight_dot` / `score_weight_lib_coverage` /
`score_weight_emp_coverage`, threaded through `score_scan_against_candidates`
→ `annotate_feature` → `_annotate_feature_batch` → `run_annotation`. Setting a
weight to `0` drops that term from the score entirely (`x**0 == 1`).

**A `flat_fragmentation` flag.** New `detect_flat_fragmentation` in
`group_ms2.py`, mirroring `detect_precursor_only`: peaks are floor-filtered
(`flat_fragmentation_min_rel_intensity`, default `0.01`), and — only once at
least `flat_fragmentation_min_peaks` (default `3`) survive — the scan is
flagged when the coefficient of variation of their intensities
(`std/mean`) is `<= flat_fragmentation_cv_threshold` (default `0.2`). A soft
QC signal, not a filter: flagged scans are still scored and stored, same
philosophy as `precursor_only`. Computed once per scan in `associate_scan`
(Stage A), not per library candidate — it's a property of the empirical
spectrum alone. Stored on `ms2_associations.flat_fragmentation`, rolled up as
`feature_ms2_summary.n_flat_fragmentation`, and left-joined onto every
`ms2_annotations` row exactly like `precursor_only`.

**Report.** `annotation_summary` gains `n_features_total` and an explicit
funnel sentence (`n_features_total` → `n_ms2_bearing_features` →
`n_features_annotated`, with percentages) — the drop is usually library
coverage (no candidate compound within `candidate_ppm` in any configured
library), not a scoring failure, with a smaller further loss from candidates
whose fragments didn't survive noise filtering on both sides. The "Top
compounds by feature count" table is replaced by "Top features by score" —
the individual best-annotated features (`feature_id`, `feature_mz`,
`inchikey`, `compound_name`, `score`), highest first — since that was the
actually-useful question (which features got a confident call), not which
compound recurred most.

Schema changes (`ms2_associations.flat_fragmentation`,
`feature_ms2_summary.n_flat_fragmentation`,
`ms2_annotations.flat_fragmentation`) are additive, folded into
`create_analysis_schema` ([ADR 6](0006-schema-single-source-of-truth.md)).
Config `version` 11 → 12.

## Alternatives considered

- **Normalise the score weights to sum to 1.** Rejected: the resulting
  formula isn't numerically the default formula for any normalisation (it's
  a different function shape), so it can't default to today's behaviour.
  Unnormalised exponents can reproduce the exact original formula and let a
  weight of `0` cleanly disable a term.
- **A weighted arithmetic mean instead of geometric.** Rejected: coverage
  terms exist specifically so a spectrum with almost no shared fragments
  can't out-argue its way to a high score via `dot_product_score` alone
  (which is exactly what the reverse-dot-product design guards against); an
  arithmetic mean lets one strong term compensate for a near-zero one, which
  defeats that guard.
- **Hard-filter `flat_fragmentation` scans (drop them like a min-peaks
  filter).** Rejected for the same reason [ADR 3](0003-no-min-peaks-filter-flag-instead.md)
  rejected a min-peaks filter: CV is a soft, occasionally-wrong signal (a
  genuinely symmetric molecule can fragment into near-equal pieces) — flag,
  don't drop.
- **Fold `flat_fragmentation` into the consensus `peak_term` discount now.**
  Deferred — worth doing once the flag's behaviour has been checked against
  real data; out of scope here.

## Consequences

- `ms2_associations` and `ms2_annotations` are one column wider each,
  `feature_ms2_summary` too; `persist_grouping`/`persist_annotations` and
  their column tuples updated. No data migration — breaking for old analysis
  DBs (`annotation_summary`'s query now selects
  `ms2_annotations.flat_fragmentation`), re-run the grouper + annotation +
  report, consistent with [ADR 4](0004-two-table-association-storage.md) and
  [ADR 7](0007-library-annotation-design.md)'s same policy.
- Re-running annotation with different `score_weight_*` values changes
  `score` (and every rank derived from it) without touching the unweighted
  diagnostic columns — a weighting experiment is always inspectable against
  the same `dot_product_score` / `lib_coverage` / `emp_coverage` it started
  from.
