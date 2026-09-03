# Database schemas

Two SQLite databases. Both use WAL journalling and `PRAGMA foreign_keys = ON`.
Schema is created by exactly one function per database
([ADR 6](../adr/0006-schema-single-source-of-truth.md)).

---

## Raw database — `<sample>.db`

Created by `parser.mzml_parser.create_raw_schema`. Written by `parse` and
`map_pixels_to_db` only; **never modified by an analysis**.

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

Primary key `(pixel_id, scan_id)`. Also creates `idx_ms1_scans_rt`.

---

## Analysis database — `analysis_<run-id>.db`

Created by `analysis_db.create_analysis_schema`. One file per run; because the
file is unique to the run, `run_id` in `commands` is informational rather than a
scoping key.

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

---

## Re-run semantics

| action | effect |
|---|---|
| re-run any stage | never rewrites a raw DB |
| re-run the grouper | `DELETE` + repopulate `ms2_associations`, `ms2_window_features`, `feature_ms2_summary`; `features` / `samples` untouched |
| re-run alignment | `DELETE FROM features` then repopulate |
