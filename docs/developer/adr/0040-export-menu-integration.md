# 40 — Export menu: Integration export

**Status:** Accepted

## Context

Second of three Export-menu actions ([ADR 39](0039-export-menu-annotation.md)
covers Annotation, Image follows in its own ADR). Corrected during this
feature's own clarification: not a single sample-level summary table, but
one CSV/TXT file **per sample**, each the full pixel x feature intensity
matrix — no spatial aggregation at all. Raw or TIC-normalized, user's
choice.

## Decision

- `core/export.py`: `export_integration_tables(db_path, dest_folder,
  layer, file_format="csv")` — for every registered sample with an
  `.h5ad` on disk (same `Path(db_path).parent / f"{name}.h5ad"`
  convention as `AnalysisBridge._sample_h5ad_path` /
  `HeatmapImageProvider._load_adata` / `run.py`'s own writer — now a
  fourth, `core`-side copy of the same rule, factored into a small
  private `_sample_h5ad_path` here rather than importing a GUI module
  from `core/`), writes `<dest_folder>/<sample_name>_integration.<ext>`:
  header `x, y, <mz_1>, <mz_2>, ...` (every analysis feature, `mz` to 4
  decimals), one row per pixel from `adata.obsm["spatial"]`, values via
  `core.plotting.heatmap`'s own `_feature_column_index`
  (nearest-`mz`-match column lookup — a sample's own `adata.var["mz"]`
  isn't guaranteed bit-identical to `features.mz` after a SQLite
  round-trip) and `_layer_column` (`"raw"` -> `adata.X`, `"TIC"` ->
  `adata.layers["TIC"]`) — the exact same pair `render_feature_heatmap`/
  `feature_value_range` already use, not a new convention. A sample
  missing its `.h5ad` is skipped, not an error, same as those two
  functions' own GUI callers. Same `csv.QUOTE_NONNUMERIC` quoting/
  delimiter convention as `export_annotation_table`, except the
  delimiter is keyed off an explicit `file_format` argument here, not a
  destination filename's suffix — there's no per-file name to type an
  extension into when the output is "pick a folder, get N files."
- `AnalysisBridge.exportIntegrationTables(analysis_db_path, dest_folder,
  layer, file_format)` — reuses `exportFinished`/`exportFailed` and
  `ExportWorker` from ADR 39 unchanged, exactly as that ADR predicted.
- `Main.qml`: `Export > Integration…` opens a small custom `Dialog`
  (Raw/TIC and CSV/TXT, each a pair of plain `Button`s — not
  `RadioButton` + `ButtonGroup`, see `HeatmapControlsPanel.qml`'s own
  Raw/TIC toggle comment for why that combination has hung the QML
  engine before) whose "Choose Folder…" button hands off to a second,
  plain `FolderDialog` once both choices are made — a bare `FolderDialog`
  alone can't express "and also pick two settings first."

## Alternatives considered

- **One sample-level summary row per sample** (the original ask, before
  correction) — would have needed a spatial aggregation method
  (sum/mean) with no natural default. Dropped once the actual want (a
  full per-pixel matrix, no aggregation) was clarified.
- **Keying the delimiter off a typed filename**, matching Annotation
  export exactly — not possible here: `FolderDialog` returns a folder,
  not a filename, so the format has to be its own explicit choice in the
  dialog rather than inferred from what the user typed.
- **A single combined `FolderDialog` with format encoded in a
  `nameFilters`-style control** — QML's `FolderDialog` has no analogous
  filter mechanism; a small custom `Dialog` in front of it was the
  simplest way to collect two extra choices before it opens.

## Consequences

- Third Export action (Image) can reuse the same "custom `Dialog` with a
  couple of plain-Button choices, then hand off to a `FolderDialog`"
  shape this one establishes, plus the same `ExportWorker`/
  `exportFinished`/`exportFailed` pair.
- `_sample_h5ad_path` now exists in three places (`AnalysisBridge`,
  `HeatmapImageProvider`, and now `core/export.py`) expressing the same
  rule independently — acceptable for now (each is a one-line, easily
  greppable convention, and `core/` deliberately doesn't import GUI
  modules), but a fourth caller would be worth factoring into one shared
  home.
