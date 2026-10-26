# 49 — Imported analyzed `.h5ad` datasets, linked to an analysis

**Status:** Accepted

## Context

After a run, the user analyzes the output `.h5ad` in notebooks. The
analysis covers QC, dimensionality reduction and clustering, among other
steps. The user then wants those results back in the app, for:

- Visual Inspection, including overlaying clusters and other notebook
  `obs` columns;
- drawing ROIs, including on a registered H&E image;
- Moran's I.

The file brought back can be the analysis' `merged.h5ad` or one sample's
`.h5ad`. In either case it may have lost pixels (QC) and features, and
gained `obs`, `obsm` and layers. Its `X` may no longer be raw
intensities.

Everything in Visual Inspection, ROI, H&E overlay and Moran's I reads
**one `.h5ad` per sample**, resolved as
`Path(analysis_db).parent / f"{sample}.h5ad"`. That path was duplicated in
the bridge, both image providers and `core/export.py`.

## Decision

These choices were agreed with the user:

- **A separate, linked dataset, not a write-back into the pipeline
  files.** Import splits the source once into
  `<analysis folder>/imported/<slug>/<sample>.h5ad`. Two new
  analysis-DB tables (`imported_datasets`, `imported_dataset_samples`)
  record it. Every existing per-sample reader then works unchanged,
  because it is simply pointed at another folder. The pipeline's own
  `.h5ad` files are never touched. Several imports of the same analysis
  can coexist, for example two clustering runs.
- **One resolver.** `core.imported_dataset.sample_h5ad_path(db, sample,
  rel_dir=None)` replaces the four duplicated derivations.
  `AnalysisBridge` holds the one *active dataset*
  (`setActiveDataset(db, id)` / `activeDatasetId`). It routes the ROI
  slots, the feature-list and spatial-correlation loaders, both
  heatmap-reading providers, and Image export through it. Its caches are
  keyed by `(db, rel_dir)`. A workspace always opens on the pipeline
  output.
- **Pixel identity.** Each source row is matched to a pipeline pixel id.
  The first attempt strips `ad.concat(index_unique="-")`'s `-<label>`
  suffix from the obs name. If that fails, the row is matched on exact
  x/y coordinates. The import is refused, and nothing is left on disk
  or in the DB, if any row doesn't belong to its mapped sample. The
  source obs name is kept in `obs["source_obs_name"]`. `obsm["spatial"]`
  is restored from the pipeline sample.
- **Full grid axes (`uns["msianalyzer_grid"]`).** If QC drops a whole
  edge row or column, the grid that `heatmap.pixel_grid_indices` rebuilds
  from the remaining coordinates shrinks. That silently shifts every ROI
  polygon and every H&E affine, since both are stored in grid-index
  space. Imported files therefore store the pipeline sample's full axes,
  and `pixel_grid_indices` uses them when present. This is the single
  injection point, and pipeline files are unaffected.
- **Missing features render black.** Plain nearest-m/z matching would
  otherwise show a *neighbouring* feature when the notebook dropped one.
  The 10 ppm guard applies to imported files only. The pipeline keeps its
  documented nearest-match behaviour, which an existing test pins.
- **ROIs on an imported dataset** go into its per-sample file. They are
  then written back into the user's **source** file, **obs column only**
  (`roi_<name>`). This is the user's own choice, so the notebook sees the
  ROI. The write reads and rewrites only the `obs` group through h5py
  (`anndata.io.read_elem`/`write_elem`), and matches rows by
  `source_obs_name`. `X` is never loaded, which matters for multi-GB
  files. The write-back is best effort: a moved file, or one locked by a
  notebook, still saves the ROI locally and reports a warning.
  `merged.h5ad` sync applies only to the pipeline output.
- **Moran's I on an imported dataset is a button** in Spatial
  Correlation. The user chose this over computing it at import time, and
  it deliberately overrides the usual "front-load it into the Run" rule,
  because an import has no Run. It reuses `compute_spatial_autocorr` and
  `compute_local_moran_layer` sample by sample; both already parallelize
  internally.
- **Import runs in the background** (`DatasetTaskWorker`), with
  per-sample progress. The split is parallel across samples
  (`ProcessPoolExecutor`). Each worker opens the source in backed mode
  and materializes only its own rows, so peak memory is about one sample
  per worker. `inspect_h5ad` also reads in backed mode.
- **Tile ids carry a trailing `|ds<id>` field.** Switching datasets would
  otherwise leave every QML `Image.source` unchanged, so nothing would be
  re-requested. The Python parsers read fields by position and ignore it.
  `HeatmapPrefetchWorker` appends the same token so its cache keys still
  match.
- **Detecting the source analysis needs no pipeline change.** The
  pipeline already stores the analysis id as `uns["project_id"]`. The
  name is historical: `create_spatial_adata` is passed the analysis id.
  The value survives `ad.concat(uns_merge="unique")`. The import dialog
  warns on a mismatch but doesn't block the import.

## Consequences

- No config or schema-version bump, and no re-run. The new tables are
  `CREATE TABLE IF NOT EXISTS`, so opening an existing analysis adds them
  through `ensureSchemaCurrent`.
- An imported dataset keeps a copy of its samples, costing disk space
  about equal to the source file. Removing the dataset deletes that copy
  and never the user's file.
- "Delete ROI everywhere" with a dataset active still drops the
  analysis-wide name/color catalog row, even if pipeline files carry that
  ROI. The catalog is name/color only, so re-saving re-registers it.
- Integration export stays pipeline-only.
- `ad.concat` can leave a `None` key in `.layers`, and the pipeline's own
  `merged.h5ad` has one. It is filtered out of every layer list.

## Addendum: Moran's I after the run, on the pipeline output too

The Spatial Correlation tab's "Compute Moran's I" bar now shows for
whichever dataset is selected, including the pipeline output. It covers an
analysis run without `spatial_correlation`, and recomputing on another
layer. `compute_dataset_spatial_correlation(dataset_id=None)` scores the
pipeline's own per-sample `.h5ad` files. Each sample is scored on its own
grid, never pooled, and `merged.h5ad` is not touched. The layer choices
for the pipeline output are the normalized layer (the default) and `X`
(raw). If the tables already show scores for a selected pass, a "Recompute
and overwrite?" confirmation comes first.
