# 63 — Re-annotate a finished analysis, with history

**Status:** Accepted

## Context

Trying another library, tolerance or flat-spectrum threshold meant a full
re-run: a new analysis that redoes peak picking, alignment and `.h5ad`
building, although only the MS2 part changes. The raw-parse cache only
skips parsing and pixel mapping.

## Decision

- **Only the MS2 stages re-run**: `group_ms2 → precursor_purity →
  annotate_ms2 → ms2_consensus`. They read `features` and the raw
  databases' MS2 scans, and write only the analysis-DB tables in
  `reannotation.MS2_TABLES`. The `.h5ad` data, normalization and Moran's I
  don't depend on them.
- **One implementation for both paths**: `reannotation.run_ms2_stages`. The
  pipeline calls it with `skip_done=True` (its resume behaviour) and its
  step events; re-annotation with `skip_done=False`.
- **In place, not a copy of the analysis**: a copy would mostly duplicate
  the `.h5ad` files. They can't be shared or hard-linked either, since each
  analysis' `.var` holds its own annotation columns.
- **History, every state kept**: a new `annotation_history` table, one row
  per state (`pipeline` — recorded at the first re-annotation —,
  `reannotate`, `restore`) with its settings and a snapshot of
  `MS2_TABLES` in `<analysis folder>/annotation_history/<id>_<kind>.db`.
  The newest row is current. **Restore** copies a snapshot back and adds a
  new `restore` row, so nothing is overwritten. **Delete** removes an old
  row and its file; the current one can't be deleted.
- **Only the MS2 tables are snapshotted**, never the whole analysis DB: it
  also holds ROIs, calibration, post-run target-list names and imported
  datasets, which a restore must not undo. A restore copies by the columns
  both sides have, so a snapshot older than a new column still restores.
- **Re-annotation clears every MS2 table first**: `persist_annotations`
  only replaces the rows of the libraries it was given, so a removed
  library's hits would otherwise survive. If a stage fails, the snapshot of
  the previous state is put back before the error is reported.
- **Settings**: all four groups (`group_ms2`, `purity`, `annotate`,
  `consensus`) are editable. `align_ppm` stays the run's. Settings are
  built per group from today's defaults overlaid with the stored values
  (`Ms2Settings.from_sections`), not with `Config.from_dict`, which rejects
  a config saved by another version.
- **Afterwards**: the summary report is rebuilt (when enabled in the run's
  `report` settings). The bridge then does the same refresh as a post-run
  target list (caches, `.var`, `targetListMatchesChanged`). The run's
  saved config gets the new MS2 groups through
  `ProjectModel.updateRunConfigSections`, on the GUI thread, since the GUI
  holds the project in memory and a background write would be overwritten.
- **GUI**: **Analyses → Re-annotate…** (`ReannotateDialog.qml`), one tab per
  group built with `ConfigGroupForm.qml` from `ConfigSchema`, plus a
  History tab (Load settings / Restore / Delete). The per-kind field
  parsing moved to `Utils/ConfigFields.js`, shared with New Analysis.

## Consequences

- A different library or threshold is a few minutes, and reversible.
- Snapshots cost disk space (`ms2_annotations` stores raw spectra), so old
  entries can be deleted.
- No config change, so no config version bump; the table is added by the
  schema refresh on opening an analysis.
