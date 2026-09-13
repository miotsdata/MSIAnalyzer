# 20 — Already-parsed, db-only samples

**Status:** Accepted

## Context

The user asked to start an analysis directly from a sample's already-parsed,
pixel-mapped raw database instead of always re-parsing mzML/XML — useful
when the same raw data feeds multiple analyses, or a raw database was
produced by a previous run. Checking `IOConfig` and `Run.run_core` showed
this was **not** actually supported: the per-sample loop in `run_core` is
keyed on `enumerate(config.io.mzml_paths)`, so a sample with no mzML path
had no way to exist at all — contrary to the initial assumption that core
already handled it.

The existing `IOConfig` shape is three parallel, index-aligned lists
(`mzml_paths`, `xml_paths`, `db_paths`) plus `project_folder`/`out_dir` (see
[ADR 1](0001-raw-vs-analysis-db-split.md) for why raw databases are a
separate, shared artifact in the first place). Any new design needed to fit
that shape rather than replace it, and the user confirmed mzML-sourced and
already-parsed samples should be freely mixable within one run rather than
forcing an analysis to be all-one-kind.

## Decision

A `None` entry in `mzml_paths`/`xml_paths` at index *i* marks that sample as
already parsed: it's sourced entirely from `db_paths[i]`, which must be set.
`IOConfig.__post_init__` validates only this new invariant (every index
where `mzml_paths[i] is None` must have a non-`None` `db_paths[i]`) —
deliberately narrow, since a first attempt at requiring
`len(xml_paths) == len(mzml_paths)` broke pre-existing tests that relied on
`xml_paths`/`db_paths` being shorter than `mzml_paths` (never a real
invariant before this change).

`Run._process_one_sample` branches on `mzml_path is None`: when it is, both
`MzmlParser().parse()` and the pixel-mapping step
(`map_pixels_to_db`/`parse_raster_xml`) are skipped entirely, and a new
`Run._raw_db_is_ready(db_path)` staticmethod checks the database actually
has `ms1_scans` and `spatial_pixels` rows before trusting it — a
content-based check rather than the existing `is_command_already_run`
mechanism, because a user-supplied "already parsed" database may come from
a different `run_id`/project than the current run.

`Config.to_dict()`'s list-to-`str` conversion is fixed alongside this: it
used to check only `isinstance(value[0], Path)` to decide whether a whole
list needed stringifying, which silently failed to convert a mixed list
with `None` at index 0.

`Config.version` is **not** bumped (stays 15) — unlike every prior
config-shape change in this project's history, this one is purely additive
and backward compatible: no field was renamed or removed, and every
existing config file (where `mzml_paths`/`xml_paths` entries are always
non-`None`) behaves identically.

The GUI's New Analysis wizard exposes this as a separate "Add db files..."
button on the input/output tab (`NewAnalysisPage.qml`), populating a
`dbOnlyPaths` list distinct from the mzML/XML `sampleRows` table.
`collectConfig()` merges it in: one `null`/`null`/real-path triple per
`dbOnlyPaths` entry, appended after the `sampleRows`-derived entries. The
Run button's `enabled` condition changes from requiring `sampleRows.length >
0` to requiring `sampleRows.length > 0 || dbOnlyPaths.length > 0`, so an
analysis made entirely of already-parsed samples can still run.

## Consequences

- Mixing is the default and only mode — there is no "all-mzML or
  all-already-parsed" restriction anywhere in core or the GUI.
- `_raw_db_is_ready` is a cheap two-query check, not a full schema
  validation — a database with the right tables but garbage/incompatible
  content is not caught here; it will fail later, same as any other
  malformed input.
- Every place that previously assumed `mzml_paths[i]` was always a `Path`
  (log labels, output filenames, `sample_names` used for
  `align_mz_across_samples`) now falls back to `raw_db_paths[i].stem` when
  it's `None` — anywhere a future contributor adds a new
  `mzml_path`-keyed code path in `run.py` needs the same `is None` check,
  or it will crash with `Path(None).stem` on a mixed run.
