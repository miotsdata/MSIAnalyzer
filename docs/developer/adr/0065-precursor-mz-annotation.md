# 65 — Annotation by precursor m/z alone

**Status:** Accepted

## Context

Many features have no MS2, or MS2 too poor to match. A mass match against
the libraries' compounds still narrows down what a feature can be, and is
the only identity a feature without MS2 can get from a library.

## Decision

- **Settings in `annotate`**: `ms2_enabled` (default on) and
  `precursor_enabled` (default off) with `precursor_ppm`. MS2 only, MS2 +
  precursor, or precursor only. Both use `library_path`. No config version
  bump beyond ADR 64's v23 (same release).
- **A new stage, `annotate_precursor`**, after `annotate_ms2`, part of the
  MS2 stages, so Re-annotate can switch it on for older analyses. It runs on
  **every** feature.
- **Candidates**: every library spectrum within `precursor_ppm`, filtered to
  the samples' polarity, the closest one kept per (library, compound,
  adduct) — a library has several spectra per compound and adduct (one per
  collision energy). Ranked per feature by |ppm error|, all kept in
  `precursor_annotations`.
- **Reference m/z**: the stored precursor m/z when it has at least 4
  decimals; otherwise formula + adduct (`target_list.Adduct` by label, else
  the library's own adduct delta mass); otherwise the stored value anyway.
  A float drops trailing zeros (`760.5850` reads as `760.585`), so such a
  value takes the formula route, which is the accurate one anyway.
- **Library read with plain SQL**, read-only, without peak arrays, and
  matched with `searchsorted`: half a million library spectra and 20,000
  features take about a second, so no process pool.
- **Feature name**: tier order target list → MS2 → precursor → predicted
  formula. Isomers tie on ppm, so the name is
  `formula + adduct (n candidates)`, never an arbitrary first compound.
- **Exposed**: `precursor_name`, `precursor_ppm` and
  `n_precursor_candidates` in the Annotation export and `.var` (whatever
  tier names the feature); every candidate in
  `.uns["precursor_candidates"]` as one long table keyed by `.var_names`
  (one HDF5 group per feature would make the file slow to write and open);
  an MS2 / Precursor switch in the Annotations top hits; a
  "Precursor-matched features" tile on the Summary page.

- **Name search over candidates** (follow-up): the Annotations name box
  also matches a feature's top-N MS2 candidates (one per compound, by best
  `rank_feature`) and top-N precursor candidates, N being the top-hits
  panel's Top-N, unless "Search in candidates" is unticked. The names come
  with the table rows (`load_feature_candidate_names`, at most 20 per kind),
  cached per database version since grouping every MS2 row takes seconds on
  millions; a covering index `idx_ann_feature_compound` makes it ~3x faster.

- **Feature m/z and ties** (follow-up): samples disagree on a feature's
  m/z by ~1 ppm, as much as the ppm differences between candidates.
  `features.mz` stays the rounded plain mean (the identity every join,
  `aligned_mzs.csv` and `.var` use); precursor matching computes
  `features.mz_weighted`, the intensity-weighted mean of the member
  centroids (from each sample's stored `filter_spectra` peaks, via
  `members_json`), unrounded, and `features.mz_spread_ppm`, their standard
  deviation in ppm (NULL with one sample). ppm errors are against
  `mz_weighted`. Candidates whose |ppm| is within `mz_spread_ppm` of their
  tie group's first share its `precursor_annotations.tie_rank` (`rank`
  stays unique: the representative pick still takes `rank = 1`). Computed
  at precursor annotation, so the pipeline and Re-annotate both refresh
  it. Limit: centroids are rounded to the 0.0001 Da bin grid at detection
  and stored as float32, which dominates below ~m/z 200; keeping full
  precision there is a separate change. Recalibration against known peaks
  (the fix for a systematic per-sample offset) is deferred.

## Consequences

- Only compounds that have spectra in the libraries, with the adducts those
  spectra were recorded with, can match. A broad compound database belongs
  in a target list (ADR 26/60).
- Precursor candidates don't count as "annotated" in the MS2 outcome funnel
  or the re-annotation history count, which stay about MS2 matching.
