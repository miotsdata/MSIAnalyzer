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

Per this round's discussion, v1 shipped with a per-sample top-N ranked
list plus a plain sortable full table. After using it against real data,
the user judged the page "very very ugly" and the top-N panel redundant
with the full table — revised (same conversation, v1.1) to:

- **The per-sample top-N panel was removed entirely.** Everything it did
  is achievable by sorting the full table by a sample's own column.
- **The full table is the whole page now**: one row per feature, one
  fixed-width column per sample (`featureColumnWidth`/`sampleColumnWidth`
  constants in `SpatialCorrelationSection.qml`) — no more
  Layout-negotiated variable widths, which was the source of the
  misalignment complaint. A frozen header row (its own
  non-interactive `Flickable`, horizontal position slaved to the
  scrollable body's `contentX`) wraps long sample names onto up to 3
  lines and always leaves room for the sort-direction arrow underneath,
  rather than truncating the name and hiding the arrow. The body scrolls
  both directions with visible scrollbars — fixed column widths mean
  "roughly 6 samples visible before scrolling" falls out naturally from
  typical window width, rather than being hard-capped at exactly 6.
  Numeric cells are right-aligned in a monospace font for column
  alignment; the feature column is left-aligned.
- **The feature column shows the representative compound name when
  annotated, else `m/z <value>`** — the exact same convention Visual
  Inspection's own feature picker uses (`HeatmapControlsPanel.qml`'s
  `featureLabels`). Backed by a new `_nearest_compound_name` join in
  `analysis_bridge.py` against `analysis_db.load_feature_list` (nearest
  m/z within a 10 ppm tolerance — capped, unlike `heatmap.py`'s
  equivalent lookup, since here a genuinely unmatched m/z is possible and
  must fall back to "unannotated" rather than silently borrowing an
  unrelated feature's name).
- **Name/m/z search fields**, same two-field convention as Annotate/Visual
  Inspection (`Utils/SearchQuery.js`, ADR 34/35) — filters the full table
  live.
- **Right-click "Show spatial distribution"** unchanged in mechanism —
  the exact cross-tab handoff Annotations' "Inspect visually" already
  established (ADR 32), just now only on the full table's rows (the
  top-N panel it also lived on is gone).
- **Caching across tab switches**: `AnalysisBridge.requestSpatialCorrelation`
  gained a single-slot cache keyed by `analysis_db_path`. The section is
  a lazy `Loader` (`active` only while its tab is current,
  `AnalysisPage.qml`'s `spatialCorrLoader`) — its whole QML item,
  including the fetched `rows`, is destroyed on every tab switch away, so
  a per-QML-instance cache can't survive that; caching on the Python side
  instead means only the *first* visit to the tab (per analysis) actually
  re-reads every sample's `.h5ad` — switching tabs and back is instant.
  A different analysis (different `analysis_db_path`) misses the cache
  and re-fetches, matching "switching to a new analysis clears the
  memory of the table".

## Out of scope for this pass

- **Local Moran's I** (per-pixel LISA maps) — a different output shape (a
  spatial overlay, not a per-feature score). Noted by the user to revisit
  once global Moran's I proves useful; not built here.
- **Per-cell color-coded table** — flat/sortable only for v1, as above.
- **Cross-sample comparison views** (e.g. "is feature X organized in A but
  not B") — the full table's columns already let this be eyeballed
  directly; no dedicated visualization built.
- **Per-cell color-coding** for the full table — still deferred (see v1.1
  redesign above); the fixed-width/wrapped-header/frozen-header rework
  addressed alignment and readability, but adding a gradient/heatmap-style
  background per cell is separate follow-on work.
- **The right-click → Visual Inspection handoff can freeze the UI
  momentarily** — the user reported this, explicitly flagged it as lower
  priority ("optional now"), and it was not investigated or fixed this
  round. Likely the same underlying cost as opening Visual Inspection
  cold for any other reason (its own feature-list fetch + first heatmap
  image render are not obviously backgrounded end-to-end) rather than
  something this feature's handoff introduced — worth a dedicated look if
  it keeps coming up.

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

## Follow-ups (same conversation)

- **Parallelism**: `spatial_correlation.n_jobs` (new `SpatialCorrelationConfig`
  field, default `None`) is passed straight through to
  `squidpy.gr.spatial_autocorr`'s own `n_jobs` — parallelizes scoring
  *within* one sample, across its features (squidpy's own joblib-backed
  parallelism). Samples themselves are still processed one at a time in
  `Run.run_core`'s loop; per-sample parallelism (a process pool over
  samples, like `precursor_purity.n_workers`) was considered and
  deliberately not built — the user chose to rely on squidpy's own
  parallelism first.
- **Real bug found and fixed**: after running the pipeline with this
  feature enabled, the new "Spatial Correlation" tab didn't appear in the
  actual GUI app at all. Root cause: `src/msianalyzer/gui/resources_rc.py`
  is a *generated* file (`pyside6-rcc qml/resources.qrc -o resources_rc.py`,
  see `docs/developer/architecture/gui.md`) that the real app imports
  directly and must be committed up to date — but `tests/gui`'s own
  session fixture (`compile_qml_resources`) transparently recompiles it
  fresh before every test run, which silently masked that the *committed*
  copy had gone stale (last touched by an unrelated H&E commit, well
  before this feature's QML changes) while every test still passed.
  Fixed by regenerating and committing it. **Takeaway for future GUI
  work**: adding/changing a `.qml` file always needs `resources_rc.py`
  regenerated and committed alongside `resources.qrc` — the test suite's
  automatic recompilation is real, but it only proves the *tests* see the
  new file, not that a real, already-built `resources_rc.py` does.
- **A second real bug found and fixed, same session**: the first attempt
  at `requestSpatialCorrelation`'s cache passed `analysis_db_path` into
  `_onSpatialCorrelationSucceeded` via a `lambda df: ...` closure instead
  of a plain bound-method connection. PySide only marshals a cross-thread
  signal onto the main thread's event loop when it can detect the
  receiver's thread affinity from a bound `QObject` method; a lambda has
  no such receiver, so it ran directly on the worker thread — which then
  wrote straight to a QML-exposed property from off the GUI thread and
  silently hung the test process (no traceback, just a `waitUntil`
  timeout). Fixed by keeping a plain `worker.succeeded.connect(self._onSpatialCorrelationSucceeded)`
  connection and reading `analysis_db_path` back off
  `self.sender()._analysis_db_path` (an attribute `TableQueryWorker`
  already stores) instead of closing over it. **Takeaway**: never wrap a
  `TableQueryWorker`/similar cross-thread signal connection in a lambda
  to smuggle extra arguments through — read them off the worker/sender
  instead.
