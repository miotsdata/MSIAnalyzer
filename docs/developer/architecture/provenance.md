# Provenance & caching

## Two identifiers

| name | value | scope | used for |
|---|---|---|---|
| `run_id` | the **project** UUID (`Project.uuid`) | many runs | raw-DB `commands` bookkeeping; cross-run caching of `parse` and `map_pixels_to_db` |
| `analysis_id` | this **run's** `Run.id` (a fresh UUID) | one run | analysis-DB identity (`analysis_<analysis_id>.db`), analysis-DB `commands` rows, and `aggregated_spectra.run_id` |

The parse and pixel-mapping outputs live in the raw DB and are keyed by
`run_id`, so a second analysis in the same project reuses them — provided both
analyses resolve the same raw-DB path, which is why raw databases live at the
project level (`<project_folder>/parsed/`) and not in a per-analysis `out_dir`
([ADR 1](../adr/0001-raw-vs-analysis-db-split.md)). Everything
parameter-dependent is keyed by `analysis_id` and lives in a database whose file
name already contains `analysis_id`.

## The `commands` tables

Every parameter-dependent step appends a row *before* it runs:

```python
command_id = analysis_db.log_command(
    adb_path,
    command_name="detect_ms1_centroids",
    arguments={**vars(config.centroid)},   # the FULL parameter set
    run_id=analysis_id,
    sample_id=sample_id,                   # None for run-wide steps
)
```

`arguments` is JSON and holds the complete config group for that stage (the
grouper also stores `align_ppm` alongside `group_ms2.*`). An analysis database is
therefore self-describing: `SELECT command_name, arguments FROM commands` tells
you exactly how each derived table was produced.

`aggregated_spectra` rows reference their producing `command_id`, so the averaged
/ centroided / filtered spectra are distinguishable even though they share a
table.

## Skip logic

Each stage checks whether it has already run and short-circuits:

- **File-based** (align, figures, CSV, `.h5ad`): "does the output file exist?"
- **Command-based** (parse, map pixels, average, centroids, filter, group MS2):
  `is_command_already_run(command_name, id, db_path[, sample_id])` — "is there a
  matching `commands` row?"
  - raw DB: `Run.is_command_already_run(name, run_id, raw_db_path)` (no
    `sample_id` column there).
  - analysis DB: `analysis_db.is_command_already_run(name, analysis_id,
    adb_path, sample_id)`.

To force recomputation, delete the output file, or the `commands` row (and any
dependent rows).

## Grouper idempotence

`persist_grouping` calls `create_analysis_schema` (all `IF NOT EXISTS`) and then
`DELETE`s the three grouper tables before inserting. Combined with the
`group_ms2` `commands` check in `run_core`, a re-run is a no-op unless the row is
removed; a forced re-run cleanly replaces the association without disturbing
`features` or `samples`.

## Annotator idempotence

`run_annotation` upserts the `annotation_libraries` row (keyed on `path`) and
`persist_annotations` `DELETE`s that library's `ms2_annotations` rows before
inserting. The `annotate_ms2` `commands` check in `run_core` skips the step
entirely once it has run; the whole step is also skipped whenever
`config.annotate.library_path` is empty.
