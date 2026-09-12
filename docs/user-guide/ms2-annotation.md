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
2. **Choose the primary feature.** Among the features physically inside the
   isolation window `[target − lower, target + upper]` (missing offsets fall
   back to `group_ms2.default_isolation_half_width`) and within
   `group_ms2.assoc_ppm` of the match value, pick the closest in ppm. If none
   qualifies, the scan is **unassigned** (`feature_id` is NULL).
3. **Flag fragmentation failure** (`precursor_only`, see below). Nothing is ever
   dropped.

How many *other* features also happened to fall inside that isolation window
is **not** tracked or stored — it measures feature-list density, not what
actually co-fragmented into the scan's own spectrum, and it used to be
recorded (and used to gate scoring) as a "chimeric" flag. See
[ADR 19](../developer/adr/0019-retire-feature-density-chimeric-flag-and-peak-based-purity.md)
for why that was removed, and use **Stage A′ / precursor purity** below for
the real, per-scan signal instead.

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
| `nearest_other_feature_ppm` | ppm to the closest feature that is *not* the chosen one |
| `precursor_target_delta_ppm` | ppm between `precursor_mz` and `isolation_window_target` |
| `precursor_only` | `1` when fragmentation appears not to have occurred |
| `rt`, `collision_energy`, `n_peaks`, `polarity` | copied from the scan |

### `feature_ms2_summary` — one row per feature

| column | meaning |
|---|---|
| `n_ms2` | MS2 scans associated to this feature |
| `n_samples` | distinct samples those scans came from |
| `n_precursor_only` | how many showed no real fragmentation |
| `n_single_peak` | how many had ≤ 1 fragment peak |
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

---

# Stage A′ — precursor ion purity

`core.annotation.precursor_purity` asks: *"how much of the ion current in this
scan's isolation window actually belonged to the precursor?"* It measures that
where it physically happened — in the MS1 scan the MS2 was triggered from
(`ms2_scans.parent_scan_id`, or the nearest earlier MS1) — and never touches the
feature list. Set `purity.enabled = false` to skip the stage.

## What it does

For every MS2 scan:

1. **Resolve the parent MS1** and slice it to the isolation window
   `[target − lower, target + upper]` (missing offsets fall back to
   `purity.default_half_window_da`).
2. **Confirm the precursor without peak detection.** A local peak-picker fails
   in dense, matrix-heavy, low-m/z windows even when the precursor is plainly
   there, so the stage integrates the raw profile directly: `precursor_frac =
   I(precursor_mz ± purity.precursor_confirm_ppm) / I(isolation window)`
   (above-baseline area). This is *the* metric — always computable, never
   dependent on resolving a discrete peak.
3. **Set `precursor_confirmed`.** `precursor_confirmed = precursor_frac >=
   purity.precursor_confirm_min_frac` — the recorded precursor really carries
   signal in its own parent MS1.
4. **Snap `precursor_mz`.** Move it to the nearest parent-MS1 local maximum
   within `purity.precursor_snap_ppm` (a tight radius, so it can never jump to
   a neighbour; a no-op when the recorded value is already on a peak). Stored
   as `precursor_mz_snapped` / `snap_shift_ppm`. **Association is not re-run** —
   this is a refined value for downstream QC.

Samples are scored **one process per sample** (`purity.n_workers`, default one
per CPU, capped at the sample count; set `1` to force the serial path). The
`precursor_purity` table is written once, after every sample is in.

!!! note "History"
    This stage originally also computed a peak-picking-based `purity` value
    (msPurity-style: detect a peak in the window, compare intensities,
    interpolate across the parent + next MS1 scan on the same raster line).
    That measurement failed to resolve the precursor as a discrete peak in
    ~56% of scans on real MALDI-imaging data, so it — and the raster
    interpolation that supported it — were retired in favor of
    `precursor_frac`, which needs no peak at all. See
    [ADR 8](../developer/adr/0008-precursor-ion-purity.md) and
    [ADR 19](../developer/adr/0019-retire-feature-density-chimeric-flag-and-peak-based-purity.md).

## `precursor_purity` — one row per MS2 scan

| column | meaning |
|---|---|
| `sample_id`, `ms2_scan_id` | back-pointer into the sample's raw `ms2_scans` |
| `parent_ms1_scan_id` | the MS1 the scan was triggered from |
| `window_lo_mz`, `window_hi_mz` | resolved isolation window bounds |
| `precursor_frac` | **peak-detection-free** purity proxy (see step 2). Always populated (`0` if there's no signal at all). |
| `precursor_confirmed` | `1` when `precursor_frac` clears the threshold — the precursor is really there in its own MS1 |
| `precursor_mz_snapped` / `snap_shift_ppm` | `precursor_mz` snapped to a parent-MS1 peak, and the ppm it moved |

Filter low-purity scans with `precursor_frac < 0.5` (a query-time choice —
the stage stores a measurement, not a verdict), not on how many features
happen to share a scan's isolation window.

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
2. **Score every associated scan, unconditionally.** There is no gate based on
   how many features share a scan's isolation window (see
   [ADR 19](../developer/adr/0019-retire-feature-density-chimeric-flag-and-peak-based-purity.md)).
   Optionally set `annotate.min_precursor_frac` to skip scans whose precursor
   purity (Stage A′) is known and below that — scans the purity stage could
   not score are always kept, the filter never guesses.
3. **Score each scan against each candidate.** Both spectra are max-normalised to
   1; peaks below `annotate.noise_threshold` are dropped from *both*; fragments
   are aligned within `annotate.fragment_ppm`; a weighted reverse dot product
   (`annotate.mz_power` / `annotate.int_power`) plus a coverage term gives
   `score = dot_product_score × coverage_score` in `[0, 1]`.
4. **Keep every candidate** that shared at least `annotate.min_matched_peaks`
   fragment peaks, each stored with a `rank_ms2` within its scan.
5. **Rank the feature's hits.** `rank_feature` / `rank_feature_sample` rank
   *rows* outright — `rank_feature = 1` **is** the feature's single best hit
   (add `AND sample_id = ?` via `rank_feature_sample = 1` for one sample).
   `rank_scan_feature` / `rank_scan_feature_sample` instead rank the feature's
   *scans* by their best hit and broadcast that onto the scan's rows — pair
   with `rank_ms2` to walk one spectrum's candidates.

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
| `rank_ms2` | 1 = best candidate for this scan |
| `rank_feature` | row rank over the whole feature — **1 = the feature's single best hit** |
| `rank_feature_sample` | row rank over one (feature, sample) — 1 = the feature's best hit in this sample |
| `rank_scan_feature` | the feature's *scans* ranked by best hit (all samples), value repeated on every row of the scan |
| `rank_scan_feature_sample` | same, within one sample |
| `precursor_confirmed`, `precursor_frac` | carried through from the purity stage (NULL when the scan was not purity-scored) |
| `precursor_only` | carried through — fragmentation looked to have failed |
| `emp_raw_mz` / `emp_raw_intensity` | the untouched (pre-filtering) **empirical** spectrum |
| `lib_raw_mz` / `lib_raw_intensity` | the untouched (pre-filtering) **library** spectrum |

The four `*_raw_*` columns are zlib-compressed float32 blobs (decode with
`msianalyzer.core.parser.mzml_parser.blob_to_array`). They exist so a mirror plot
can be drawn straight from a result row without re-opening the raw per-sample
database or the library file. The noise-filtered, normalised view actually
scored is not stored — it's reconstructed on demand from these raw arrays plus
this run's own `noise_threshold`
(`msianalyzer.core.annotation.spectral_match.normalize_and_filter_spectrum`).
Set `annotate.store_raw_spectra = false` to write the raw columns NULL and keep
the table small.

### `feature_compound_scores` — view: best score per (feature, compound)

A `CREATE VIEW` over `ms2_annotations` (no stored data — always current). One
row per `(feature_id, inchikey)` with `best_score = MAX(score)`,
`best_scan_id` / `best_sample_id` / `best_library_id` of that row, plus
`compound_name`, `n_candidate_rows` and `n_scans`. Rows with a NULL InChIKey
are dropped.

```sql
SELECT inchikey, compound_name, best_score, best_scan_id
FROM feature_compound_scores
WHERE feature_id = 1223
ORDER BY best_score DESC;
```

or `analysis_db.load_feature_compound_scores(db, feature_id=1223)` for a
DataFrame.

## Low-purity and precursor-only scans

Every associated scan is scored — there is no gate based on isolation-window
feature density (see
[ADR 19](../developer/adr/0019-retire-feature-density-chimeric-flag-and-peak-based-purity.md)).
`precursor_only` scans are annotated too (the flag rides along) — usually you
will filter them out when reviewing hits.

Set `annotate.min_precursor_frac` (e.g. `0.5`) to **not** score scans whose
precursor purity (Stage A′) is known and below that. Scans the purity stage
could not score (no parent MS1 resolved) are always kept — the filter never
guesses. Every stored row carries the scan's `precursor_frac` regardless, so
you can also filter at query time.

## In the summary report

When a library ran, `summary_report.html` gains an **MS2 annotation** section:
MS2-bearing features by best-hit confidence (`rank_feature = 1` score ≥ 0.5 /
≥ 0.75), the best-score distribution, how many distinct plausible compounds each
feature has (best-per-compound ≥ 0.5, from the `feature_compound_scores` view),
whether a feature's repeat scans agree on the top compound, plus top-compound
and per-library tables. It also reports how often the Stage A″ consensus scan is
the annotated best scan.

---

# Stage A″ — MS2 consensus

`core.annotation.consensus` answers *"which single MS2 scan represents this
feature?"*. For each MS2-bearing feature it folds three per-scan signals into

```
consensus_score = best_score × precursor_frac_term × peak_term
```

* `best_score` — the scan's `ms2_annotations.score` at `rank_ms2 = 1`, or `1.0` when
  no library ran;
* `precursor_frac_term` — `clamp(precursor_frac)`, or
  `consensus.neutral_precursor_frac` (default `0.5`) when the scan was not
  purity-scored;
* `peak_term` — `min(1, n_peaks / consensus.target_peaks)` on the scan's
  fragment-peak count.

The highest-scoring scan wins. `consensus.min_precursor_frac` (its own knob)
drops scans with a *known* low precursor purity from the pick but not from the
`n_ms2` count. The stage works library-free (score term collapses to
precursor_frac × peaks) and purity-free (`neutral_precursor_frac` stands in);
set `consensus.enabled = false` to skip it.

## `feature_ms2_consensus` — one row per MS2-bearing feature

| column | meaning |
|---|---|
| `feature_id`, `feature_mz` | the feature |
| `best_sample_id`, `best_scan_id` | the chosen MS2 scan |
| `n_ms2` | scans associated to the feature |
| `n_ms2_considered` | scans that passed `min_precursor_frac` |
| `n_ms2_scored` | of those, how many had a library hit |
| `consensus_score` | the winning scan's score |
| `precursor_frac`, `n_peaks` | the winning scan's precursor purity and fragment count |
| `best_annotation_score` | its `rank_ms2 = 1` library score, or NULL |
| `best_compound_name`, `best_inchikey` | that hit's identity, when present |
