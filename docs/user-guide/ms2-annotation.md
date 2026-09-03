# MS2 annotation

Annotation has two stages. **Stage A — association** (built) ties every MS2 scan
to a feature. **Stage B — library annotation** (planned) scores the fragment
spectra against reference libraries. This page covers Stage A: what the grouper
does and how to read its output.

## What the grouper does

For every MS2 scan in every sample:

1. **Pick a match value.** Use `precursor_mz`. If it is missing, fall back to
   `isolation_window_target`. The column `match_key` records which was used.
2. **Enumerate the isolation window.** Every feature whose m/z lies in
   `[target − lower, target + upper]` is a candidate that could have
   co-fragmented. `n_features_in_window` is that count — the **chimera flag**.
   Missing isolation offsets fall back to `group_ms2.default_isolation_half_width`.
3. **Choose the primary feature.** Among the in-window features within
   `group_ms2.assoc_ppm` of the match value, pick the closest in ppm. If none
   qualifies, the scan is **unassigned** (`feature_id` is NULL).
4. **Record a ppm difference for every in-window feature**, not just the chosen
   one (`ms2_window_features`).
5. **Flag fragmentation failure** (`precursor_only`, see below). Nothing is ever
   dropped.

Why match on `precursor_mz` and not the isolation target: the target is an
*instruction* to the instrument and is often rounded; `precursor_mz` is a
*measurement* of the same physical quantity as a feature, so a tight ppm
comparison is meaningful. See
[ADR 5](../developer/adr/0005-three-ppm-tolerances.md).

## Tables

### `ms2_associations` — one row per MS2 scan

| column | meaning |
|---|---|
| `sample_id`, `scan_id` | back-pointer into the sample's raw `ms2_scans` |
| `feature_id` | the chosen feature, or NULL if unassigned |
| `match_key` | `precursor_mz` or `isolation_window_target` |
| `ppm_offset` | signed ppm, match value vs the chosen feature |
| `n_features_in_window` | features inside the isolation window (chimera count) |
| `nearest_other_feature_ppm` | ppm to the closest feature that is *not* the chosen one |
| `precursor_target_delta_ppm` | ppm between `precursor_mz` and `isolation_window_target` |
| `precursor_only` | `1` when fragmentation appears not to have occurred |
| `rt`, `collision_energy`, `n_peaks`, `polarity` | copied from the scan |

### `ms2_window_features` — one row per (scan, in-window feature)

`ppm_diff` (signed, vs the match value), `within_tol` (inside `assoc_ppm`?),
`is_primary` (the chosen one). A clean single match is exactly one row with
`is_primary = 1`; a chimeric scan has several, one primary.

### `feature_ms2_summary` — one row per feature

| column | meaning |
|---|---|
| `n_ms2` | MS2 scans associated to this feature |
| `n_samples` | distinct samples those scans came from |
| `n_precursor_only` | how many showed no real fragmentation |
| `n_single_peak` | how many had ≤ 1 fragment peak |
| `n_chimeric` | how many had > 1 feature in their isolation window |
| `median_n_peaks` | median fragment-peak count |

This answers questions like *"feature 743.52 was fragmented 8× across 3 samples;
5 of those show no real fragmentation."*

## The `precursor_only` flag

A failed MS2 is usually **the surviving precursor and little else** — a poor
signal to identify from, but genuine information about the analyte. A scan is
flagged `precursor_only` when its base peak is within
`group_ms2.precursor_only_mz_tol_da` of the precursor **and** at least
`group_ms2.precursor_only_tic_frac` of the fragment TIC sits in that band. Raw
peak count is a weak proxy (noise inflates it), so it is kept only as a secondary
`n_single_peak` view. See
[ADR 3](../developer/adr/0003-no-min-peaks-filter-flag-instead.md).

## `include_unmatched`

With `group_ms2.include_unmatched = true` (default) an MS2 scan that matches no
feature is still stored, with `feature_id` NULL — useful for spotting features
that peak-picking or alignment missed. Set it to `false` to keep only MS2 tied to
an imageable feature.

## Chimeric scans — current policy

A chimeric scan (`n_features_in_window > 1`) is associated **only to its nearest
feature**; the alternatives are still recorded in `ms2_window_features` with
their ppm differences. Duplicating a chimeric scan into every co-isolated feature
(conservative annotation transfer) is left for Stage B to decide.
