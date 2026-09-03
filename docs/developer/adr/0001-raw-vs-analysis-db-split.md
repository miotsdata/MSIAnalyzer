# 1 — Raw vs analysis database split

**Status:** Accepted

## Context

Originally every downstream step wrote its output back into the per-sample
SQLite database produced by the parser: pixel mapping, the averaged /
centroided / filtered MS1 spectra, and the `commands` / `run_id` bookkeeping all
mutated `<sample>.db`. The cross-sample feature list was only ever written to a
CSV.

This is the wrong ownership model:

- The "raw" database is never actually immutable — every analysis mutates it,
  causing write-lock contention between concurrent analyses, unbounded growth,
  and making "which analyses touched this file / delete analysis X cleanly" a
  cross-table sweep.
- mzML is parsed once. If the raw DB is write-once it becomes a cacheable,
  shippable, parallel-safe artefact; re-running an analysis never touches it.
- Alignment, MS2 association and annotation are inherently cross-sample and have
  no single raw DB to live in — so derived data was already destined to be split
  across two homes with two mental models.

## Decision

Draw the line at **parameter dependence**:

- **Raw DB (`<sample>.db`)** holds only objective facts about the acquisition:
  `ms1_scans`, `ms2_scans`, instrument `metadata`, a parse `commands` row, and
  the pixel grid (`spatial_pixels`, `pixel_ms1_scans`). Written by `parse` and
  `map_pixels_to_db`; never modified afterwards.
- **Analysis DB (`analysis_<run-id>.db`)** — one file per run — holds everything
  that depends on a parameter choice: `samples`, `aggregated_spectra`,
  `features`, the grouper tables, and later annotation tables, plus its own
  `metadata` / `commands`.

Pixel geometry stays in the raw DB: an imaging acquisition has exactly one true
pixel grid, derived from the instrument timing file, not from an analysis
parameter.

## Consequences

- An analysis is one self-contained, reproducible file. Deleting it removes the
  analysis with no side effects.
- Several analyses (different bin widths, tolerances, filters) share one set of
  parsed raw databases.
- Cross-database reads use `ATTACH DATABASE` (`analysis_db.attach_raw`); the raw
  DB is opened read-only by convention.
- `save_aggregated_spectra` / `load_aggregated_spectra` take an
  `analysis_db_path` and a `sample_id`; the raw DB lost its `group_id` column on
  `ms2_scans` (grouping is an analysis concern).
