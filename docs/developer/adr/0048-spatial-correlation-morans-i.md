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

A new `AnalysisBridge.getSpatialCorrelationModel` (originally a
`requestSpatialCorrelation`/`spatialCorrelationReady` fire-and-forget
pair — superseded by the model-based redesign below, same session) backs
a new `SpatialCorrelationSection.qml` tab, added to `AnalysisPage.qml`
alongside Summary/MS1/Annotations/Visual Inspection. Row fetching still
uses `TableQueryWorker` (same background-thread pattern as
`requestAnnotationTable`, since this does real per-sample `.h5ad` file
I/O); `_load_spatial_correlation_table` (`analysis_bridge.py`) reads
`load_samples` for the sample list, then each sample's own `.h5ad` for
`uns["moranI"]` — flat `(mz, sample, moran_i)` rows, pivoted
(`_pivot_spatial_correlation_rows`) into the model's own per-feature row
shape.

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
- **State (not just data) persists across tab switches** — `SpatialCorrelationModel`
  (new, `spatial_correlation_model.py`), a small `QObject` owned by
  `AnalysisBridge.getSpatialCorrelationModel(analysis_db_path)`
  (single-slot cache keyed by path — a different analysis discards the
  old model and builds a fresh one, matching "switching to a new
  analysis clears the memory of the table"). This directly answers a
  sharper version of the original complaint: an earlier version of this
  fix only cached the fetched *rows* in Python, but the section's own
  `sortColumn`/`sortAscending`/`nameQuery`/`mzQuery` still lived as plain
  QML properties on the section item itself — which is destroyed every
  time (`SpatialCorrelationSection.qml` is a lazy `Loader`, `active` only
  while its tab is current). Reported directly: "when I told caching,
  means cache the state. How can it be useful if when I go back I don't
  have the same table as before (I might have sorted for a column)?"
  `SpatialCorrelationModel` now owns the sort/filter state too
  (`sortBy()`, `nameQuery`/`mzQuery` properties, `visibleRows` —
  already sorted and filtered), so re-binding the section's `model`
  property to the same instance on a later visit picks up exactly where
  the user left off, with zero QML-side persistence logic.
  **A `TableView` bound to a real `QAbstractTableModel` was tried
  first** (the user's own suggestion — "is it maybe the time to
  introduce a table model?") and would also have solved virtualizing
  the (800-1200 row) table, but empirically **`TableView`/
  `HorizontalHeaderView` never instantiate any delegate items at all
  under this project's offscreen GUI test harness** — verified directly
  (`contentWidth`/`topRow`/`bottomRow` compute correctly, `rows`/
  `columns` counts are right, but zero delegate `QQuickItem`s are ever
  created, with no warnings) — a real environment limitation, not a
  usage mistake. Since this project's own testing culture puts real
  weight on GUI tests actually exercising rendering (see this codebase's
  existing Column/Repeater/Flickable-under-offscreen-platform notes for
  a similar precedent), shipping a page that can't be tested this way
  was judged worse than keeping the already-working
  `Repeater`-in-`Flickable` rendering. `SpatialCorrelationModel` is
  therefore a plain `QObject` exposing a `visibleRows` `QVariantList`
  property (each row a dict with a precomputed `label` field), not a
  `QAbstractTableModel` — sorting/filtering moved into Python (a real
  win, since it's what actually survives the Loader's destroy/rebuild
  cycle), but the QML rendering itself is close to unchanged from the
  first version of this table.

### Visual Inspection handoff no longer freezes the GUI thread

The right-click "Show spatial distribution" (and, it turned out, the plain
Visual Inspection nav button too, and Annotations' "Inspect visually" —
all three land on the same tab) could freeze the UI, worst on the very
first switch to Visual Inspection for an analysis. Root cause:
`ZoomableImage.qml`'s `Image` is deliberately `asynchronous: false` (its
own comment documents a real, reproduced hang with `asynchronous: true`
and 2+ concurrent tiles — suspected matplotlib global state under
concurrent access) — a reasonable call for the actual *rendering* step
(small rasters, genuinely fast), but it means `HeatmapImageProvider._load_adata`'s
`ad.read_h5ad()` for a **cold** sample runs synchronously on the GUI
thread the instant the tab activates and its tiles start requesting
`image://heatmap/...`.

Fix: `AnalysisBridge.prefetchHeatmapSamples` (new) reads the same
`.h5ad` files into `HeatmapImageProvider`'s existing cache on a
background thread (`HeatmapPrefetchWorker`, new — pure `ad.read_h5ad`,
no matplotlib, so it doesn't reintroduce the concurrency hazard
`asynchronous: false` guards against) — called from a new
`AnalysisPage.goToVisualInspection(pendingMz)` function that every
entry point onto that tab now funnels through (the plain nav button,
Annotations' handoff, this feature's handoff), *before*
`sectionStack.currentIndex` actually changes. A new `switchingToVisual`
property is folded into `currentSectionReady`, so the existing
`sectionLoadingOverlay` (already used for "the destination tab is still
loading") covers this waiting period too — no second, separate spinner
needed. Only the first `min(4, sample count)` samples are prefetched,
matching `VisualInspectionSection.qml`'s own grid cap (`gridCols:
Math.min(4, samples.length)`) and `HeatmapImageProvider`'s own
`_CACHE_SIZE = 4` — prefetching more would just evict what's about to
be shown before it's ever displayed.

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
- **Table virtualization** — `Repeater`-in-`Flickable` instantiates every
  row's delegate up front (800-1200+ features on a real project), not
  just the visible ones. `TableView` would solve this but isn't usable
  here (see above); a hand-rolled virtualized `Repeater` (only
  instantiate rows within the visible `contentY` range) was considered
  out of scope for this pass — no reported performance complaint yet,
  just a known latent risk on a larger project.

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
- Switching to Visual Inspection is now genuinely asynchronous (a
  background-thread round trip happens before `sectionStack.currentIndex`
  changes), even when the prefetch itself resolves near-instantly (e.g.
  no samples, or a warm cache). Several existing GUI tests that clicked
  the Visual Inspection nav button and asserted on `controlsFlickable`
  right after a flat `qtbot.wait(50)` needed to change to
  `qtbot.waitUntil(...)` instead (`test_analysis_page.py`,
  `test_visual_inspection_section.py`, `test_roi_design_window.py`,
  `test_coregistration_window.py`) — a real, if small, test-suite-wide
  ripple effect from what looked like a narrowly-scoped fix.

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
