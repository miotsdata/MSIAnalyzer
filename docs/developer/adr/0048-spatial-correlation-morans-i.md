# 48 — Spatial correlation (Moran's I), computed via squidpy on the `.h5ad`

**Status:** Accepted

## Context

The user wants a per-feature, per-sample score for how spatially organized
a metabolite is in the tissue — a compound present throughout one clear
region (real biology) looks very different from one scattered at random
(likely noise), but nothing in the pipeline measured that distinction.
Moran's I (a standard spatial-autocorrelation statistic, already familiar
from squidpy/scanpy spatial-omics workflows) was the requested starting
metric — explicitly per-sample, never pooled across samples, since a
feature's spatial organization on one slide says nothing about another.

The user specified computing this with squidpy directly against the
existing per-sample `.h5ad` `AnnData` objects (squidpy's native
input/output shape), with results persisted **in the `.h5ad` itself**
(squidpy's own convention, `adata.uns["moranI"]`) rather than in a new
SQL table — deliberately different from most other pipeline outputs in
this codebase, which live in the analysis database.

squidpy (and its `scanpy` dependency, which pulls in `numba`/
`scikit-learn`/`networkx`/`statsmodels`) is a materially heavier dependency
than anything else in `pyproject.toml`. Both squidpy 1.8.3 and scanpy
1.12.4 (and numba/llvmlite) ship `cp313` wheels, so installing on this
project's pinned `>=3.13,<3.14` isn't blocked, but the PyInstaller/AppImage
build (currently ~742MB unpacked / ~298MB AppImage per
`docs/developer/distribution.md`) should be re-validated once this ships —
not done as part of this change.

## Decision

### New opt-in pipeline stage, after TIC normalization

`spatial_correlation.enabled` (new `SpatialCorrelationConfig`, default
`False`) gates a new `"spatial_correlation"` stage in `Run.run_core`
(`src/msianalyzer/core/run/run.py`), inserted right after `normalize_tic`
and before the summary report, mirroring `normalize_tic`'s own
enabled-check / `is_command_already_run` / `log_command` shape exactly.
For each sample, it calls the new
`core.spatial.moran.compute_spatial_autocorr(h5ad_path, layer)`:

- `squidpy.gr.spatial_neighbors_grid(adata, n_neighs=4)` — the grid-specific
  neighbor builder, since `create_spatial_adata`'s pixel raster is an exact
  regular lattice (not squidpy's usual hexagonal Visium assumption,
  `n_neighs=6`'s default); `n_neighs=4` is rook adjacency, matching a
  square pixel grid. (`spatial_neighbors_grid` supersedes the older,
  now-deprecated generic `spatial_neighbors` — used directly rather than
  the deprecated entry point.)
- `squidpy.gr.spatial_autocorr(adata, mode="moran", genes=list(adata.var_names), layer=...)`
  — scored against `layer="TIC"` by default (comparable across samples),
  falling back to raw `.X` when that layer isn't present (e.g.
  `normalization.enabled=False`).
- `adata.write_h5ad(h5ad_path)` — overwrites the sample's `.h5ad` in place,
  the same read-mutate-write-back pattern `run_tic_normalization` already
  uses.

No new analysis-database schema at all — `adata.uns["moranI"]` (a
DataFrame indexed by `var_names`, an `I` column plus p-values) is the only
place this result lives. Config `version` bumped 17→18 (no migration
shim, consistent with every prior bump).

### GUI: new "Spatial Correlation" workspace tab

A new `AnalysisBridge.requestSpatialCorrelation`/`spatialCorrelationReady`
fire-and-forget pair (same `TableQueryWorker` background-thread pattern as
`requestAnnotationTable`, since this does real per-sample `.h5ad` file I/O)
backs a new `SpatialCorrelationSection.qml` tab, added to
`AnalysisPage.qml` alongside Summary/MS1/Annotations/Visual Inspection.
`_load_spatial_correlation_table` (new, `analysis_bridge.py`) reads
`load_samples` for the sample list, then each sample's own `.h5ad` for
`uns["moranI"]` — flat `(mz, sample, moran_i)` rows, pivoted client-side in
QML (same "flat rows in, QML pivots" shape `AnnotationsSection.qml`
already uses).

Per this round's discussion, v1 ships:
- **Per-sample top-N** ranked lists (one block per sample, not one global
  ranking — "for each slide/sample, not overall"), reusing the existing
  top-N `SpinBox` pattern (`AnnotationsSection.qml`'s `topNSpinBox`).
- **A plain sortable full table** (features × samples, click-to-sort
  header, reusing `AnnotationsSection.qml`'s `sortColumn`/`sortAscending`/
  `sortBy()` pattern verbatim) — flat styling; per-cell color-coding is
  explicitly deferred (no such QML control exists yet anywhere in this
  codebase, and would need real design work to avoid, in the user's words,
  "stuck[ing] the page").
- **Right-click "Show spatial distribution"** on any row (top-N or full
  table), reusing the exact cross-tab handoff Annotations' "Inspect
  visually" already established (ADR 32) — `AnalysisPage.qml`'s existing
  `pendingInspectMz` property and `visualLoader` wiring, unchanged; the new
  tab just emits into the same mechanism via a new
  `showSpatialDistributionRequested(real mz)` signal.

## Out of scope for this pass

- **Local Moran's I** (per-pixel LISA maps) — a different output shape (a
  spatial overlay, not a per-feature score). Noted by the user to revisit
  once global Moran's I proves useful; not built here.
- **Per-cell color-coded table** — flat/sortable only for v1, as above.
- **Cross-sample comparison views** (e.g. "is feature X organized in A but
  not B") — the full table's columns already let this be eyeballed
  directly; no dedicated visualization built.

## Consequences

- Off by default — an existing config/analysis is completely unaffected
  until `spatial_correlation.enabled: true` is set.
- Verified end-to-end on a real project (HCCA, 4 samples, 802 features):
  the full pipeline ran cleanly with the stage enabled (~5s added for
  Moran's I across all 4 samples), and the GUI's own table-loading function
  read the results back correctly, with consistent high scores for the
  same handful of features across every sample — a good sanity signal that
  the statistic is capturing genuine spatial structure, not per-sample
  noise.
- squidpy/scanpy is now a base dependency (not GUI-only, since the
  computation runs in core pipeline code) — `pyproject.toml`'s
  `dependencies` list grew accordingly. The PyInstaller/AppImage build's
  size and hidden-imports handling has not been re-validated against this
  much larger dependency tree; flagged for a follow-up build-and-verify
  pass before this ships in a packaged release.
