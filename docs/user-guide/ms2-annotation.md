# MS2 annotation

Annotation has three steps. **Stage A — association** ties every MS2 scan to a
feature. **Stage A′ — precursor purity** measures how clean each scan's
isolation window was, judged against its own parent MS1 scan. **Stage B —
library annotation** scores the fragment spectra against reference libraries.
This page covers all three: what each step does and how to read its output.

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

## Chimeric scans — policy

A chimeric scan (`n_features_in_window > 1`) is associated **only to its nearest
feature**; the alternatives are still recorded in `ms2_window_features` with
their ppm differences. Stage B scores it against that primary feature and marks
every result row `is_chimeric = 1` (see below).

`n_features_in_window` counts the *analysis-wide* feature list, which is the
union over every sample and pixel. On a large run a wide isolation window almost
always straddles several features even when the scan's own parent MS1 held a
single clean peak there, so this count over-flags. Use **Stage A′ / precursor
purity** for a per-acquisition chimericity signal that does not depend on the
feature list.

---

# Stage A′ — precursor ion purity

`core.annotation.precursor_purity` asks a different question: not *"how many
features could this window contain?"* but *"how much of the ion current actually
in this window belonged to the precursor?"* It measures that where it physically
happened — in the MS1 scan the MS2 was triggered from
(`ms2_scans.parent_scan_id`, or the nearest earlier MS1) — and never touches the
feature list. Set `purity.enabled = false` to skip the stage.

## What it does

For every MS2 scan:

1. **Resolve the parent MS1** and slice it to the isolation window
   `[target − lower, target + upper]` (missing offsets fall back to
   `purity.default_half_window_da`).
2. **Detect real peaks** in that slice — local maxima above
   `purity.min_rel_intensity × (window base peak)`, refined by parabolic
   interpolation and merged within `purity.merge_ppm`.
3. **Identify the precursor peak** — the in-window peak nearest `precursor_mz`
   (or `isolation_window_target`) within `purity.ppm_precursor_match`.
4. **Score:** `purity = precursor intensity / total in-window intensity`;
   `runner_up_rel_int = strongest other in-window peak / precursor peak`;
   `n_peaks_in_window` = the peak count.
5. **Interpolate** across the parent MS1 and the *next* MS1 scan when the laser
   had only moved to the **same pixel** or an **adjacent pixel on the same
   raster line** (`purity.use_next_ms1`, default on). The MS2's retention time
   sets the blend weight (`rt_weight`). Otherwise only the parent MS1 is used
   and `purity == purity_parent`.

The "same raster line" test compares pixel identity, then `(x, y)` indices plus
the acquisition-time gap (`purity.max_interpixel_gap_sec`, auto-derived when
null) — never raw coordinates, so serpentine vs. flyback rastering is
irrelevant. If pixel mapping never ran, the stage is parent-MS1-only.

## `precursor_purity` — one row per MS2 scan

| column | meaning |
|---|---|
| `sample_id`, `ms2_scan_id` | back-pointer into the sample's raw `ms2_scans` |
| `parent_ms1_scan_id` | the MS1 the scan was triggered from |
| `next_ms1_scan_id` | second MS1 used for interpolation, or NULL |
| `bracket_kind` | `parent_only` / `same_pixel` / `same_line` |
| `rt_weight` | 0 (parent only) … 1 (next MS1) — the interpolation weight |
| `window_lo_mz`, `window_hi_mz` | resolved isolation window bounds |
| `precursor_found` | `1` when an in-window MS1 peak matched the precursor |
| `precursor_mz_ms1`, `precursor_intensity_ms1` | that peak in the parent MS1 |
| `n_peaks_in_window` | real peaks in the parent MS1 window — `> 1` ⇒ co-isolation |
| `runner_up_rel_int` | strongest non-precursor in-window peak ÷ precursor peak (`> 1` ⇒ the precursor was a minor ion) |
| `purity` | precursor ÷ total in-window intensity, RT-interpolated when `next_ms1_scan_id` is set |
| `purity_parent` | the same, parent MS1 only — always populated |

Filter chimeras with `purity < 0.8` (or your own cutoff) rather than
`n_features_in_window > 1`; `precursor_found = 0` means the precursor was too
faint to see in MS1 and `purity` is NULL. The cutoff stays a query-time choice —
the stage stores the measurement, not a verdict.

---

# Stage B — library annotation

Once every scan is snapped to a feature, `core.annotation.annotate` compares the
fragment spectra to one or more reference libraries. `annotate.library_path` is
either a single path or a **list** of paths; candidates from every library are
pooled per scan before ranking, and each stored row records which `library_id`
it came from.

## What the annotator does

The unit of work is **one feature**:

1. **Gather candidates once per feature.** Pull every library spectrum (from
   every configured library) whose precursor m/z is within
   `annotate.candidate_ppm` of the feature m/z. The same candidate set is reused
   for all of that feature's scans (features are processed in parallel,
   `annotate.batch_size` per worker).
2. **Score each scan against each candidate.** Both spectra are max-normalised to
   1; peaks below `annotate.noise_threshold` are dropped from *both*; fragments
   are aligned within `annotate.fragment_ppm`; a weighted reverse dot product
   (`annotate.mz_power` / `annotate.int_power`) plus a coverage term gives
   `score = dot_product_score × coverage_score` in `[0, 1]`.
3. **Keep every candidate** that shared at least `annotate.min_matched_peaks`
   fragment peaks, each stored with a `rank` within its scan.
4. **Rank scans within a feature** (`rank_feature`) by their best hit, so you can
   pick the single most convincing MS2 per feature.

Leaving `annotate.library_path` empty (`null` or `[]`) disables the whole stage.

## Tables

### `annotation_libraries` — one row per library used

`path` (unique), `name`, `n_spectra`, `n_compounds`, `command_id`. With several
libraries configured there is one row each, and every `ms2_annotations` row
points back via `library_id`.

### `ms2_annotations` — one row per (scan, library candidate)

| column | meaning |
|---|---|
| `sample_id`, `scan_id`, `feature_id` | which scan, and the feature it was scored against |
| `library_id`, `library_spectrum_id` | the matched library entry |
| `compound_name`, `compound_formula`, `inchikey` | the candidate compound |
| `score` | `dot_product_score × coverage_score`, the ranking value |
| `dot_product_score` | weighted reverse dot product alone |
| `lib_coverage`, `emp_coverage`, `coverage_score` | fraction of each side matched, and their geometric mean |
| `n_matched_peaks`, `n_lib_peaks`, `n_emp_peaks_raw`, `n_emp_peaks_filtered` | peak counts |
| `rank` | 1 = best candidate for this scan |
| `rank_feature` | 1 = this scan is the best-scoring MS2 on its feature |
| `is_chimeric`, `n_features_in_window` | carried through from the grouper |
| `purity`, `runner_up_rel_int` | carried through from the purity stage (NULL when the scan was not purity-scored) |
| `precursor_only` | carried through — fragmentation looked to have failed |
| `emp_filtered_mz` / `emp_filtered_intensity` | the noise-filtered, normalised **empirical** spectrum that was scored |
| `lib_filtered_mz` / `lib_filtered_intensity` | the same for the **library** spectrum |

The four `*_filtered_*` columns are zlib-compressed float32 blobs (decode with
`msianalyzer.core.parser.mzml_parser.blob_to_array`). They exist so a mirror plot
can be drawn straight from a result row without re-running the matcher. Set
`annotate.store_filtered_spectra = false` to write them NULL and keep the table
small.

## Chimeric, low-purity and precursor-only scans

Chimeric scans are scored against their primary feature and flagged
`is_chimeric = 1`; the coverage term already penalises mixed spectra, so
downstream can down-weight or exclude them. Set `annotate.annotate_chimeric =
false` to skip them entirely. `precursor_only` scans are annotated too (the flag
rides along) — usually you will filter them out when reviewing hits.

Set `annotate.min_purity` (e.g. `0.5`) to **not** score scans whose precursor
purity (Stage A′) is known and below that. Scans the purity stage could not
score (`precursor_found = 0`, or the stage disabled) are always kept — the
filter never guesses. Every stored row carries the scan's `purity` and
`runner_up_rel_int` regardless, so you can also filter at query time.

---

# Stage A″ — MS2 consensus

`core.annotation.consensus` answers *"which single MS2 scan represents this
feature?"*. For each MS2-bearing feature it folds three per-scan signals into

```
consensus_score = best_score × purity_term × peak_term
```

* `best_score` — the scan's `ms2_annotations.score` at `rank = 1`, or `1.0` when
  no library ran;
* `purity_term` — `clamp(purity)`, or `consensus.neutral_purity` (default `0.5`)
  when the scan was not purity-scored;
* `peak_term` — `min(1, n_peaks / consensus.target_peaks)` on the scan's
  fragment-peak count.

The highest-scoring scan wins. `consensus.min_purity` (its own knob) drops
scans with a *known* low purity from the pick but not from the `n_ms2` count.
The stage works library-free (score term collapses to purity × peaks) and
purity-free (`neutral_purity` stands in); set `consensus.enabled = false` to
skip it.

## `feature_ms2_consensus` — one row per MS2-bearing feature

| column | meaning |
|---|---|
| `feature_id`, `feature_mz` | the feature |
| `best_sample_id`, `best_scan_id` | the chosen MS2 scan |
| `n_ms2` | scans associated to the feature |
| `n_ms2_considered` | scans that passed `min_purity` |
| `n_ms2_scored` | of those, how many had a library hit |
| `consensus_score` | the winning scan's score |
| `purity`, `n_peaks` | the winning scan's purity and fragment count |
| `best_annotation_score` | its `rank = 1` library score, or NULL |
| `best_compound_name`, `best_inchikey` | that hit's identity, when present |
