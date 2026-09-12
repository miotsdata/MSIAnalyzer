# Database schemas

Two SQLite databases. Both use WAL journalling and `PRAGMA foreign_keys = ON`.
Schema is created by exactly one function per database
([ADR 6](../adr/0006-schema-single-source-of-truth.md)).

---

## Raw database — `<project_folder>/parsed/<sample>.db`

Created by `parser.mzml_parser.create_raw_schema`. Written by `parse` and
`map_pixels_to_db` only; **never modified by an analysis**. One per sample per
project (path from `IOConfig.raw_db_paths()`; `io.db_paths` overrides), shared
by every analysis in that project.

### `metadata`

| column | type | notes |
|---|---|---|
| `key` | TEXT | primary key |
| `value` | TEXT | |

### `commands`

| column | type | notes |
|---|---|---|
| `id` | INTEGER | PK, autoincrement |
| `run_id` | UUID | project UUID; `"parse"` for the parse row |
| `command_name` | TEXT | |
| `datetime` | TEXT | ISO 8601 |
| `arguments` | TEXT | JSON |

Index: `idx_command_run_id (run_id)`.

### `ms1_scans`

| column | type | notes |
|---|---|---|
| `scan_id` | INTEGER | PK (the mzML scan index) |
| `rt` | REAL | retention time, not null |
| `n_peaks` | INTEGER | |
| `mz_min`, `mz_max` | REAL | observed m/z range |
| `tic` | REAL | total ion current |
| `polarity` | TEXT | `"+"` / `"-"` |
| `mz_array`, `intensity_array` | BLOB | zlib `float32`, not null |

Indexes: `rt`, `mz_min`, `mz_max`, `tic`, `polarity`.

### `ms2_scans`

| column | type | notes |
|---|---|---|
| `scan_id` | INTEGER | PK |
| `parent_scan_id` | INTEGER | FK → `ms1_scans(scan_id)` |
| `polarity` | TEXT | |
| `rt` | REAL | not null |
| `filter_string` | TEXT | instrument scan filter, not null |
| `precursor_mz` | REAL | measured precursor m/z (MS:1000744) |
| `precursor_charge` | INTEGER | |
| `precursor_intensity` | REAL | |
| `isolation_window_target` | REAL | quadrupole set-point (MS:1000827) |
| `isolation_window_lower` / `_upper` | REAL | Da offsets (MS:1000828/829) |
| `collision_energy` | REAL | |
| `n_peaks` | INTEGER | |
| `tic` | REAL | |
| `mz_array`, `intensity_array` | BLOB | zlib `float32`, not null |

Indexes: `rt`, `precursor_mz`, `isolation_window_target`, `tic`,
`parent_scan_id`, `filter_string`.

### `spatial_pixels`  (written by `map_pixels_to_db`)

| column | type | notes |
|---|---|---|
| `pixel_id` | INTEGER | PK, autoincrement |
| `x`, `y` | INTEGER | grid coordinates, not null |
| `t_start`, `t_end` | REAL | pixel time window, not null |

### `pixel_ms1_scans`

| column | type | notes |
|---|---|---|
| `pixel_id` | INTEGER | FK → `spatial_pixels(pixel_id)` |
| `scan_id` | INTEGER | FK → `ms1_scans(scan_id)` |

Primary key `(pixel_id, scan_id)`. `map_pixels_to_db` also creates
`idx_ms1_scans_rt` and `idx_pms1_scan (scan_id)` — the composite PK cannot
serve a lookup by `scan_id`, which downstream stages need. Databases parsed
before this index existed pick it up on the next `map_pixels_to_db` run.

---

## Analysis database — `analysis_<run-id>.db`

Created by `analysis_db.create_analysis_schema`. One file per run; because the
file is unique to the run, `run_id` in `commands` is informational rather than a
scoping key.

The per-sample workers all append to this one file in parallel. Two rules keep
that safe: every connection is opened through `analysis_db.connect` (WAL + a
60 s busy timeout, so writers **queue** on the single-writer lock instead of
failing or racing), and **only `create_analysis_schema` issues DDL** —
`init_analysis_db` runs it once, up front; no other function (in particular
`save_aggregated_spectra`) runs `CREATE TABLE`/`CREATE INDEX`, because
concurrent schema statements on a shared WAL file have corrupted it in the
field.

### `metadata`

Analysis-level provenance (`analysis_id`, `project_id`, `created`, `n_samples`).

### `commands`

As the raw `commands` plus a `sample_id` column (NULL for run-wide steps such as
alignment and grouping). Indexes: `idx_cmd_run_id (run_id)`,
`idx_cmd_lookup (command_name, run_id, sample_id)`.

### `samples`

| column | type | notes |
|---|---|---|
| `sample_id` | INTEGER | PK, autoincrement |
| `name` | TEXT | mzML stem |
| `raw_db_path` | TEXT | path to `<sample>.db`, unique |
| `polarity` | TEXT | |

### `aggregated_spectra`

| column | type | notes |
|---|---|---|
| `id` | INTEGER | PK, autoincrement |
| `run_id` | TEXT | analysis id |
| `sample_id` | INTEGER | FK → `samples` |
| `command_id` | INTEGER | FK → `commands` — which step produced this spectrum |
| `mz_array`, `intensity_array` | BLOB | zlib `float32` |

Holds the averaged MS1, the centroided peaks and the filtered peaks, one row
each, distinguished by their `command_id`. Indexes: `run_id`, `sample_id`,
`command_id`.

### `features`

| column | type | notes |
|---|---|---|
| `feature_id` | INTEGER | PK, autoincrement |
| `mz` | REAL | consensus m/z, not null |
| `members_json` | TEXT | `{sample_name: original_peak_index or null}`, not null |
| `command_id` | INTEGER | FK → `commands` (the `align_mz_across_samples` row) |

Index: `idx_features_mz (mz)`. Persisted form of `align_mz_across_samples`.

### `ms2_associations`  (grouper)

One row per MS2 scan considered.

| column | type | notes |
|---|---|---|
| `id` | INTEGER | PK, autoincrement |
| `sample_id`, `scan_id` | INTEGER | back-pointer into the raw `ms2_scans`; `UNIQUE(sample_id, scan_id)` |
| `feature_id` | INTEGER | FK → `features`; NULL = unassigned |
| `match_key` | TEXT | `precursor_mz` / `isolation_window_target` / `none` |
| `precursor_mz` | REAL | |
| `isolation_window_target` / `_lower` / `_upper` | REAL | copied from the scan |
| `ppm_offset` | REAL | signed, match value vs chosen feature |
| `n_features_in_window` | INTEGER | chimera count, not null |
| `nearest_other_feature_ppm` | REAL | ppm to closest non-chosen feature |
| `precursor_target_delta_ppm` | REAL | ppm `precursor_mz` vs `isolation_window_target` |
| `rt`, `collision_energy`, `n_peaks`, `polarity` | | copied from the scan |
| `precursor_only` | INTEGER | 0/1, not null, default 0 |
| `command_id` | INTEGER | FK → `commands` (the `group_ms2` row) |

Index: `idx_assoc_feature (feature_id)`.

### `ms2_window_features`  (grouper)

One row per (association, feature inside the scan's isolation window). Always
populated — a clean single match is one row.

| column | type | notes |
|---|---|---|
| `association_id` | INTEGER | FK → `ms2_associations(id)` `ON DELETE CASCADE` |
| `feature_id` | INTEGER | FK → `features` |
| `feature_mz` | REAL | not null |
| `ppm_diff` | REAL | signed, match value vs this feature |
| `within_tol` | INTEGER | 0/1 — inside `assoc_ppm`? |
| `is_primary` | INTEGER | 0/1 — the chosen feature |

Primary key `(association_id, feature_id)`.

### `feature_ms2_summary`  (grouper)

One row per feature with MS2 coverage; rebuilt on every grouper run.

| column | type |
|---|---|
| `feature_id` | INTEGER, PK, FK → `features` |
| `feature_mz` | REAL |
| `n_ms2` | INTEGER |
| `n_samples` | INTEGER |
| `n_precursor_only` | INTEGER |
| `n_single_peak` | INTEGER |
| `n_chimeric` | INTEGER |
| `median_n_peaks` | REAL |

### `precursor_purity`  (purity stage)

One row per MS2 scan; rebuilt on every run of the purity stage. Chimericity
measured against the scan's own **parent MS1** (`ms2_scans.parent_scan_id`, or
the nearest earlier MS1) and, when the laser had moved to the same pixel or an
adjacent pixel on the same raster line, the next MS1 — never the analysis-wide
feature list. See [ADR 8](../adr/0008-precursor-ion-purity.md).

| column | type | notes |
|---|---|---|
| `id` | INTEGER | PK |
| `sample_id` | INTEGER | FK → `samples` |
| `ms2_scan_id` | INTEGER | not null — the scan in the sample's raw `ms2_scans` |
| `parent_ms1_scan_id` | INTEGER | the MS1 the scan was triggered from |
| `next_ms1_scan_id` | INTEGER | second MS1 used for interpolation, else NULL |
| `bracket_kind` | TEXT | not null — `parent_only` / `same_pixel` / `same_line` |
| `rt_weight` | REAL | 0 = parent only … 1 = next MS1; the interpolation weight |
| `window_lo_mz` / `window_hi_mz` | REAL | resolved isolation window bounds |
| `precursor_found` | INTEGER | 0/1 — an in-window MS1 peak matched the precursor |
| `precursor_mz_ms1` / `precursor_intensity_ms1` | REAL | that peak, in the parent MS1 |
| `n_peaks_in_window` | INTEGER | not null — real peaks in the parent MS1 window (`> 1` ⇒ co-isolation) |
| `runner_up_rel_int` | REAL | strongest non-precursor in-window peak / precursor peak |
| `purity` | REAL | precursor / total in-window intensity, RT-interpolated when `next_ms1_scan_id` is set. **Peak-based → NULL when `precursor_found = 0`.** |
| `purity_parent` | REAL | the same, parent MS1 only |
| `precursor_frac` | REAL | **peak-detection-free** purity proxy: above-baseline profile area within `purity.precursor_confirm_ppm` of the recorded `precursor_mz`, over the whole isolation window. Always computed (`0` if no signal). |
| `precursor_confirmed` | INTEGER | 0/1 — `precursor_frac >= purity.precursor_confirm_min_frac`: the recorded precursor really carries signal in its own parent MS1 |
| `precursor_mz_snapped` | REAL | `precursor_mz` moved to the nearest parent-MS1 local max within `purity.precursor_snap_ppm` (no-op when already on a peak; association is *not* re-run) |
| `snap_shift_ppm` | REAL | signed ppm shift the snap applied (`0` = no move) |
| `command_id` | INTEGER | FK → `commands` |

`UNIQUE (sample_id, ms2_scan_id)`. Indexes: `idx_purity_scan (sample_id,
ms2_scan_id)`, `idx_purity_value (purity)`.

### `feature_ms2_consensus`  (consensus stage)

One row per MS2-bearing feature; rebuilt on every run of the consensus stage.
`consensus_score = best library score x purity term x peak term`; see
[ADR 9](../adr/0009-consume-purity-and-consensus.md).

| column | type | notes |
|---|---|---|
| `feature_id` | INTEGER | PK, FK → `features` |
| `feature_mz` | REAL | not null |
| `best_sample_id` / `best_scan_id` | INTEGER | the chosen MS2 scan |
| `n_ms2` | INTEGER | scans associated to the feature |
| `n_ms2_considered` | INTEGER | scans that passed `consensus.min_purity` |
| `n_ms2_scored` | INTEGER | of those, how many had a library hit |
| `consensus_score` | REAL | not null — the winning scan's score |
| `purity` / `n_peaks` | — | the winning scan's purity and fragment count |
| `best_annotation_score` | REAL | its `ms2_annotations.score` at `rank_ms2 = 1`, or NULL |
| `best_compound_name` / `best_inchikey` | TEXT | that hit's identity, when present |
| `command_id` | INTEGER | FK → `commands` |

Index: `idx_consensus_score (consensus_score)`.

### `annotation_libraries`  (annotator)

One row per spectral library used to annotate.

| column | type | notes |
|---|---|---|
| `id` | INTEGER | PK |
| `path` | TEXT | not null, unique |
| `name` | TEXT | from the library metadata |
| `n_spectra` / `n_compounds` | INTEGER | counts at annotation time |
| `command_id` | INTEGER | FK → `commands` |

### `ms2_annotations`  (annotator)

One row per (MS2 scan, library candidate) comparison that shared at least
`min_matched_peaks` fragments. Written only when `annotate.library_path` is set;
with several libraries configured the rows are interleaved and distinguished by
`library_id`, and `rank_ms2` is the best hit across all of them.

| column | type | notes |
|---|---|---|
| `id` | INTEGER | PK |
| `sample_id`, `scan_id` | INTEGER | the empirical scan |
| `feature_id` | INTEGER | FK → `features` — the feature it was scored against |
| `library_id` | INTEGER | FK → `annotation_libraries`, not null |
| `library_spectrum_id` | INTEGER | not null |
| `compound_id` / `compound_name` / `compound_formula` / `inchikey` | — | the candidate |
| `score` | REAL | `dot_product_score × coverage_score`, not null |
| `dot_product_score` | REAL | weighted reverse dot product, not null |
| `lib_coverage` / `emp_coverage` / `coverage_score` | REAL | not null |
| `n_matched_peaks` / `n_lib_peaks` / `n_emp_peaks_raw` / `n_emp_peaks_filtered` | INTEGER | not null |
| `rank_ms2` | INTEGER | not null — 1 = best candidate for this scan |
| `rank_feature` | INTEGER | row rank over the whole feature — **1 = the feature's single best (scan, candidate) hit** |
| `rank_feature_sample` | INTEGER | row rank over one (feature, sample) — 1 = the feature's best hit in this sample |
| `rank_scan_feature` | INTEGER | the feature's *scans* ranked by their best hit (all samples), broadcast onto that scan's rows |
| `rank_scan_feature_sample` | INTEGER | same, within one sample |
| `is_chimeric` | INTEGER | 0/1, from the grouper |
| `n_features_in_window` | INTEGER | from the grouper |
| `purity` / `runner_up_rel_int` / `precursor_confirmed` / `precursor_frac` | — | left-joined from `precursor_purity`; NULL when the scan was not purity-scored |
| `precursor_only` | INTEGER | 0/1, from the grouper |
| `emp_raw_mz` / `emp_raw_intensity` | BLOB | untouched (pre-filtering) empirical spectrum (zlib float32); NULL if `store_raw_spectra` off |
| `lib_raw_mz` / `lib_raw_intensity` | BLOB | same for the library spectrum |
| `command_id` | INTEGER | FK → `commands` |

Indexes: `idx_ann_scan (sample_id, scan_id)`, `idx_ann_feature (feature_id)`,
`idx_ann_score (score)`, `idx_ann_inchikey (inchikey)`.

### `feature_compound_scores`  (view)

A view, not a table — pure aggregation over `ms2_annotations`, so it always
reflects the current rows and costs no storage. One row per
`(feature_id, inchikey)`: the best library score for that compound on that
feature, and the row it came from.

| column | notes |
|---|---|
| `feature_id`, `inchikey` | the group |
| `compound_name`, `compound_formula` | from the top-scoring row |
| `best_score` | `MAX(ms2_annotations.score)` for the group |
| `best_dot_product_score` | that row's `dot_product_score` |
| `best_sample_id` / `best_scan_id` / `best_library_id` | which row the max came from |
| `n_candidate_rows` | rows for this (feature, compound) |
| `n_scans` | distinct MS2 scans among them |

Rows with `inchikey IS NULL` are excluded. `msianalyzer report` and
`analysis_db.load_feature_compound_scores(db, feature_id=None)` read it.

---

## Re-run semantics

| action | effect |
|---|---|
| re-run any stage | never rewrites a raw DB |
| re-run the grouper | `DELETE` + repopulate `ms2_associations`, `ms2_window_features`, `feature_ms2_summary`; `features` / `samples` untouched |
| re-run the purity stage | `DELETE FROM precursor_purity` then repopulate; nothing else touched |
| re-run the annotator | `DELETE FROM ms2_annotations WHERE library_id = ?` then repopulate; `annotation_libraries` row is upserted |
| re-run the consensus stage | `DELETE FROM feature_ms2_consensus` then repopulate; nothing else touched |
| re-run alignment | `DELETE FROM features` then repopulate |
