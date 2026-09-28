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
  (new, `spatial_correlation_model.py`), owned by
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
  (`sortBy()`, `nameQuery`/`mzQuery` properties), so re-binding the
  section's `model` property to the same instance on a later visit picks
  up exactly where the user left off, with zero QML-side persistence
  logic.
- **v1.1 → v1.2, rendering revised again, this time for speed, not just
  state**: v1.1 shipped `SpatialCorrelationModel` as a plain `QObject`
  exposing a whole `visibleRows` list, rendered via the established
  `Repeater`-in-`Flickable` pattern (`AnnotationsSection.qml`) — chosen
  specifically *because* a `TableView`-based version was found not to
  render any delegates at all under this project's offscreen GUI test
  harness (verified directly: `contentWidth`/`topRow`/`bottomRow`
  compute correctly, `rows`/`columns` counts are right, but zero
  delegate `QQuickItem`s are ever created, with no warnings — a real
  environment limitation, not a usage mistake). That rendering choice
  turned out to be a real performance problem in the actual running app,
  not just a test-only concern: **every sort/filter destroyed and
  rebuilt every row's delegate** (a nested `Repeater` per row for the
  sample columns too — hundreds to low thousands of `QQuickItem`s on a
  real project), reported directly as "ordering the table is still super
  slow." Re-litigated and reversed: `SpatialCorrelationModel` is now a
  real `QAbstractTableModel` (`rowCount`/`columnCount`/`data`/
  `headerData`/`roleNames`, `beginResetModel`/`endResetModel` around
  every sort/filter), rendered via `TableView` + `HorizontalHeaderView`
  (hand-rolled click-to-sort in the header delegate — no native
  sort-indicator support in this Qt/PySide version, confirmed directly).
  `TableView` only ever asks `data()` for whatever cells are currently
  visible, so a sort/filter only redraws the viewport, not the whole
  table.
  **Traded away, explicitly and knowingly**: automated GUI-level
  interaction tests for this page (click-to-sort, right-click) can no
  longer run — no delegate renders in the offscreen harness to click on.
  `tests/gui/views/test_spatial_correlation_section.py` now drives the
  underlying model/signals directly (`model.sortBy(...)`,
  `section.showSpatialDistributionRequested.emit(...)`) instead of
  simulating clicks, which still exercises the QML-level wiring
  (property bindings, the cross-tab handoff) but not the actual
  clickable rendering. The model's own sort/filter/data logic is fully
  covered by plain Python unit tests
  (`tests/gui/utils/test_spatial_correlation_model.py`), independent of
  QML entirely. Real-device verification in the actual app (this
  project's established GUI workflow, given no display access in this
  environment) is what actually confirms the rendering/interaction side
  works — expected before trusting this page's UI, not a nice-to-have.

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
- ~~**Table virtualization**~~ — resolved in v1.2 (see Decision above):
  the table now renders via `TableView`, which only instantiates
  visible-viewport delegates regardless of row count.

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

- **A fourth real bug — a "loophole" the user found**: after the
  backed-mode fix, the table still showed "no correlation run" on the
  *first* visit to the tab, but worked correctly after switching to
  Visual Inspection and back. Root cause: `SpatialCorrelationModel.samples`
  (a `Property`) declared `notify=sortStateChanged`, a signal only ever
  emitted by `sortBy()` — never by `setRows()`. QML reads `model.samples`
  once, when the section first binds to the model (before the
  background fetch finishes, so it reads `[]`), and with no notify
  firing when the real data actually lands, that binding never
  re-evaluates — it's stuck showing the empty state forever, for that
  one section instance. Leaving the tab and coming back rebuilds the
  whole section from scratch, so it re-reads `model.samples` fresh (by
  then populated) — which is why that "fixed" it, and why it looked like
  a timing loophole rather than a broken binding. Fixed with a dedicated
  `samplesChanged` signal, emitted by `setRows()` when the sample list
  actually changes.
- **Eager warm-up at analysis-open time**: the user separately reported
  the Visual Inspection freeze was *still* happening (1s spinner, then
  3-5s blank, then everything appears) even after the render-cache
  prefetch fix, and proposed prefetching the feature list and the
  spatial-correlation table as soon as an analysis opens rather than
  waiting for the relevant tab's first visit — reasoning that opening an
  analysis is already an expected "loading" moment, so a little more
  work there is a better trade than a freeze mid-navigation later.
  Agreed as a good, pragmatic direction (see the exploratory discussion
  in-session) and implemented: `AnalysisPage.qml`'s `onAnalysisChanged`
  now also calls `AnalysisBridge.requestFeatureList` and
  `getSpatialCorrelationModel` eagerly, fire-and-forget. Both already
  had single-slot caches keyed by `analysis_db_path` (added
  `requestFeatureList`'s cache as part of this same change, mirroring
  `getSpatialCorrelationModel`'s), so the later, real call from
  `HeatmapControlsPanel.qml`/`SpatialCorrelationSection.qml` is a cache
  hit rather than a duplicate fetch. **Known limitation, not yet
  addressed**: `requestFeatureList`'s cache has no invalidation hook —
  a compound name set by "Predict formula" (ADR 27) after this cache is
  already warm won't show up in Visual Inspection's feature list until
  a different analysis is opened. **Also not yet addressed**: this
  doesn't touch the actual per-tile matplotlib render cost for anything
  beyond the one default tile already prefetched — the user separately
  asked (for a later session) whether the whole heatmap render pipeline
  can be made properly asynchronous (their suspicion: the loading
  spinner itself still visibly freezes, suggesting something is still
  blocking the main thread despite the render-cache prefetch) — flagged
  for follow-up, not investigated yet.
- **Local Moran's I revisited**: the user pointed out a real modeling
  concern with global Moran's I as currently computed — their samples'
  `.h5ad` each contain *both* a matrix-only region and the actual tissue
  in one image, so a feature's global spatial autocorrelation is heavily
  influenced by the gross matrix-vs-tissue split rather than genuine
  structure *within* the tissue. Local Moran's I (per-pixel LISA,
  already noted as out-of-scope for v1 above) would likely help here —
  flagged for follow-up, not investigated yet.
- **A third real bug, found on a real production-scale dataset**: after
  all the above shipped, the user tested against a real project (6
  samples, several `.h5ad` files 1-1.6GB each) and the new tab reported
  "no correlation run" for an analysis that had genuinely run
  `spatial_correlation.enabled: true` — every `.h5ad` had `uns["moranI"]`
  present and correct. Root cause: `_load_spatial_correlation_table`
  called plain `ad.read_h5ad(h5ad_path)` per sample — which loads the
  **entire** `X` matrix into memory just to read the small `uns`/`var`
  metadata this function actually needs. Trivial on the tiny synthetic
  fixtures every test here used; on the real multi-GB files, reading all
  6 samples' full `X` (on top of whatever `HeatmapImageProvider`'s own
  cache already held) failed — silently, since the fetch runs on a
  background thread and a failure there only logs a warning, never
  reaches the user. Fixed with `ad.read_h5ad(h5ad_path, backed="r")`
  (loads `obs`/`var`/`uns` normally, keeps `X` as an on-disk-backed
  proxy, never materialized) — confirmed on the real project's own files:
  well under a second per sample instead of loading gigabytes.
  **A second bug was found and fixed while writing this one's fix**: the
  loop that turns `moranI`'s per-feature scores into rows had its
  `rows.append(...)` sitting one indentation level too shallow, so a
  sample with N features would only append the *last* one — invisible
  with every existing test's single-feature fixture (where "last" and
  "only" are indistinguishable), only caught by writing a genuinely
  multi-feature test alongside this fix
  (`test_load_spatial_correlation_table_reads_every_feature_not_just_the_last`).
  **Takeaway**: a function that reads a whole `.h5ad` for a handful of
  small metadata fields should default to `backed="r"` unless it
  actually needs pixel-level data (`HeatmapImageProvider._load_adata`
  legitimately does, and stays as a full read) — and any fixture/test
  built around a single-row/single-feature case can hide an
  off-by-one/scope bug in a loop that a real dataset's multiplicity
  would immediately expose.
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

## Round 3: per-sample score label, async controls, local Moran's I

The three items flagged above ("Follow-ups") were picked up in a later
session, in order (smallest/lowest-risk first):

**1. Per-sample Moran's I next to each sample name in Visual Inspection.**
`SpatialCorrelationModel.moranIForMz(mz)` — a plain in-memory nearest-mz
scan over the model's already-loaded `_all_rows` (same 0.01 absolute
tolerance as `_matches_mz`), returning `{sample: score}` with unscored
samples omitted. `AnalysisBridge.getMoranIForFeature(analysis_db_path,
mz)` exposes it as a genuinely synchronous `@Slot` — deliberately, unlike
everything in item 2 below: it never touches disk, just the cached
model's `getSpatialCorrelationModel` already eagerly warmed at
analysis-open time, and it does **not** trigger a load on a cache miss
(returns `{}` instead), so a "cheap" call can't silently turn into a
surprise background fetch. `VisualInspectionSection.qml`'s
`heatmapTileLabel_<name>` now appends `" (I=0.42)"` when a score exists
for the currently-selected feature.

**2. `HeatmapControlsPanel.qml`'s remaining synchronous fetches, made
async.** The render-cache prefetch (above) fixed the *tile* render cost,
but three more call sites were still blocking the GUI thread with real
`.h5ad` reads on every relevant property change: the `obsColumns`
property (a plain JS binding calling `AnalysisBridge.getObsColumns`
directly), `refreshAutoRange()` (`getObsValueRange`/
`getFeatureValueRange`), and `refreshObsCategoryLegend()`
(`getObsCategories`) — this is what was still causing the freeze the user
kept reporting after the render-cache fix. **This explicitly supersedes
ADR 34's decision to leave `getObsColumns` synchronous** (reasoned at the
time that it wasn't `ms2_annotations`-backed and wasn't implicated in the
reported slowness — it turned out to matter once Visual Inspection's own
freeze was investigated directly). Confirmed first that `autoRange` is
purely a UI-display value (`vminToken()`/`vmaxToken()` return the literal
string `"auto"` when `autoScale` is on; the real render computes vmin/vmax
server-side) — so async-ifying these four calls has zero effect on tile
rendering correctness/timing, only removes GUI-thread blocking time.

New `HeatmapQueryWorker` (`gui/utils/heatmap_query_worker.py`) — one
generic `QThread` reused for all four calls rather than four near-
identical typed workers, following `MirrorPlotWorker`'s multi-arg
constructor pattern (`fn, *args`; `run()` calls `fn(*args)`, emits the
result). Four new `AnalysisBridge` fire-and-forget pairs
(`requestObsColumns`/`obsColumnsReady`,
`requestFeatureValueRange`/`featureValueRangeReady`,
`requestObsValueRange`/`obsValueRangeReady`,
`requestObsCategories`/`obsCategoriesReady`), each with its own in-flight-
worker slot and the standard `self.sender() is not self._x_worker: return`
staleness guard (plain bound-method connections, never a lambda — see the
cross-thread signal bug above). The four old synchronous `@Slot`s were
removed outright (confirmed via grep: only this one QML file called
them). `HeatmapControlsPanel.qml` is shared by Visual Inspection, ROI
Design, and Coregistration, so this fix benefits all three without
touching any of their own code.

**A real correctness bug found while wiring this up, not just a
performance one**: `obsColumns` arriving asynchronously means
`isSelectedObsNumeric` (which defaults to `true` when `obsColumns` hasn't
loaded yet) could still be showing its wrong default at the exact moment
`onInspectionModeChanged`/`refreshAutoRange()`/`refreshObsCategoryLegend()`
first ran — sending a *categorical* obs column straight to
`getObsValueRange`, which doesn't handle non-numeric columns and raised
(caught and logged, not a crash, but silently wrong: `obsCategoryLegend`
stayed `[]` forever, since `refreshObsCategoryLegend()`'s own short-circuit
for "numeric" also fired on the wrong default and nothing ever re-ran it
once the correct data arrived). Fixed by having `onObsColumnsChanged`
(which already resets `selectedObsIndex` on a fresh load) also re-run
`refreshAutoRange()`/`refreshObsCategoryLegend()` — harmless no-ops in
feature mode, and the correction obs-mode needed. **Takeaway**: converting
a property from a synchronous binding to an async fetch doesn't just add
latency — anything that read a *derived* property depending on it needs
to be checked for a "wrong default before the real data arrives" window,
not just "eventually correct."

**3. Local Moran's I (LISA), as an on-demand heatmap layer.** The user
confirmed (asked to choose between a per-pixel local map now vs.
ROI-restricted global scoring) they wanted **both** — the local map built
now, ROI-restricted scoring explicitly deferred to a later session.
squidpy has no local/LISA Moran's I function and `esda`/`libpysal` (the
PySAL package that does) weren't added as a dependency — instead,
`core/spatial/local_moran.py`'s `local_moran_values(adata, mz, layer)`
hand-rolls the standard Anselin (1995) formula (`I_i = (z_i / m2) *
sum_j w_ij * z_j`, row-standardized from squidpy's own binary grid
adjacency) directly against an **already-open** `AnnData` — recomputing
`spatial_neighbors_grid` on every call rather than depending on a prior
`spatial_correlation` pipeline run having populated `adata.obsp`, since
building that graph is pure pixel-coordinate geometry (cheap, no
intensity data involved) and this decouples the new feature from
requiring that separate, opt-in pipeline stage to have ever been enabled.
Pixels with no neighbors are `NaN` (no defined LISA value), not 0.

Rendering (`render_local_moran_heatmap`, same module) reuses
`heatmap.py`'s existing `_reconstruct_grid`/`_colorize_grid` — matching
`core/export.py`'s already-established precedent of importing those
private-by-convention helpers cross-module rather than exporting them.
`render_heatmap_by_target`'s dispatch (`core/plotting/heatmap.py`) grew
one new branch: `layer == "localMoran"` (a reserved sentinel, documented
next to the function, that can never collide with a real `.h5ad` layer
name) is checked before falling into the normal `render_feature_heatmap`
path — reusing the entire existing `sampleName|mz|layer|colormap|vmin|vmax`
target-string shape and `HeatmapImageProvider`'s render-cache/sample-cache
completely unchanged. `squidpy`'s import inside `local_moran_values` is
**lazy** (function-local, not module-level) — `heatmap.py` is on the GUI's
startup path via `heatmap_provider.py`, and squidpy (scanpy/numba) is
already this project's heaviest dependency; the lazy import means that
cost is only paid the first time a user actually selects the new layer.

`HeatmapControlsPanel.qml` gained a third `dataLayer` button ("Local
Moran's I", alongside the existing Raw/TIC) rather than a third
`inspectionMode` — it's still scored against whichever feature is already
selected, same as raw/TIC, so it fits the existing per-layer choice rather
than needing its own selection UI. Two small UX decisions made directly
(not asked, since they're mechanical/low-stakes): the colormap picker
gained `"coolwarm"` (the one diverging option, since local Moran's I
values are signed — positive means local clustering, negative means a
local spatial outlier — and every other option is sequential), auto-
selected via `onDataLayerChanged` when switching into this layer; and the
vmin/vmax numeric display shows literal `"auto"` text rather than a real
number in this mode, since there's no cheap way to get local Moran's I's
own min/max without computing the full per-pixel array — exactly what
rendering the tile already does — so a second full computation just to
populate a label wasn't worth it for v1.

**A real bug found on real usage, fixed same round**: the user reported
seeing the Local Moran's I layer render as if every sample were entirely
at the minimum of the color scale. Root cause: `render_local_moran_heatmap`
originally delegated straight to `_colorize_grid`'s own default autoscale
(`vmin = data.min()`, `vmax = data.max()`) — correct for a one-sided
intensity value, wrong for a *signed* metric. Real local Moran's I data is
typically dominated by a few strongly-clustered hot spots against a much
smaller (or larger) opposite-sign tail; normalizing to that raw, asymmetric
min/max puts "0" (no local structure — most of any real image) well off
the colormap's visual center, so nearly every ordinary pixel renders as if
it sat near one extreme even though its actual value is close to neutral.
Fixed by forcing the autoscale case (`vmin`/`vmax` both `None`) to a
symmetric range (`±max(abs(finite values))`) so 0 always lands exactly at
`coolwarm`'s white center, regardless of any skew in the data's real
min/max. Regression test:
`test_render_local_moran_heatmap_autoscale_is_symmetric_around_zero`
(asserts a pixel with value 0.0 renders as the colormap's exact center
color even when the surrounding data is wildly asymmetric, e.g. `+5.0` /
`-0.1`). **Takeaway**: any autoscale default for a signed/diverging metric
must be zero-centered, not a plain data min/max — this applies to any
future diverging-colormap layer added to this pipeline, not just this one.

**Explicitly still deferred, not designed further**: ROI-restricted
global Moran's I (the existing global statistic, restricted to pixels
inside a user-drawn ROI via `core/plotting/roi.py`'s
`polygon_pixel_mask`/`roi_obs_column`, then `adata[mask]` before the
existing `spatial_autocorr` call) — a future session should start from
`core/plotting/roi.py` and `RoiDesignWindow.qml`'s existing save/read
plumbing.

No new dependency added for any of the three items. Tests: unit tests for
`moranIForMz`/`getMoranIForFeature` (item 1); staleness-guard/success/
failure tests for the four new async request methods, plus a QML-level
regression test for the `onObsColumnsChanged` correctness fix (item 2);
`test_local_moran.py` (clustered-vs-random separation at the per-pixel
level, constant-feature-is-all-NaN, layer-fallback), a
`render_heatmap_by_target` dispatch test, a `HeatmapImageProvider`
request-id round-trip test, and a QML button-click test for the new layer
button plus its colormap auto-switch and "auto" label text (item 3).
`resources_rc.py` regenerated and committed alongside these `.qml`
changes (see the earlier real bug about this file going stale).

## Round 4: local Moran's I summary, as a pipeline pass + second table

After seeing the per-pixel local Moran's I layer on a real analysis, the
user asked a natural follow-on question: given one feature can show a
small cluster of high-local-I pixels, which feature has the *most* of
them? Discussed in-session (no code, design-only) before building:
agreed to compute this as a third, independent `spatial_correlation`
sub-pass (same config page, own toggle) rather than live in the GUI —
scoring *every* feature's *every pixel* on demand would be real, repeated
per-tab-open work, unlike the single-feature on-demand render Visual
Inspection's layer already does.

**Config** (`SpatialCorrelationConfig`, version 18 -> **19**): two new
fields, `local_enabled: bool = False` and `local_threshold: float = 1.0`.
Deliberately a **separate** toggle from `enabled` (global), not a
sub-option of it — scoring every pixel for every feature is heavier than
the global per-feature score, and neither pass depends on the other
having run (confirmed by `local_moran_values` already being decoupled
from the global stage's own neighbor graph, round 3). `local_threshold`
got the most attention of any field written for this project: local
Moran's I is **not** bounded to **-1**–**1** the way the global score is
— it scales with how far a pixel *and* its neighbors sit from the
sample's mean relative to that sample's own variance — so the field help
(General/Formula/Range/Interaction, ADR 44's template) spells out the
actual formula, gives a concrete anchor (`±1` ≈ "a pixel and its
neighborhood about one standard deviation from the mean, together") and
explicitly tells the user to look at a sample's own Local Moran's I layer
in Visual Inspection before trusting a threshold, rather than presenting
the default as a validated cutoff.

**Computation** (`core/spatial/local_moran.py`): `compute_local_moran_summary(h5ad_path, layer, threshold)`
loops over every feature, reusing `local_moran_values`, and writes
`adata.uns["localMoranSummary"]` (`pct_above_threshold`, one row per
feature) — the local analogue of `compute_spatial_autocorr`'s
`adata.uns["moranI"]`. One real efficiency fix made here: `local_moran_values`
used to rebuild the neighbor graph (`spatial_neighbors_grid`) on *every*
call — fine for a single on-demand GUI render, wasteful looped over
potentially thousands of features. Changed to build it only if
`adata.obsp` doesn't already have one, which also means: if the global
pass ran first and wrote its own graph back to this `.h5ad`, the local
pass's later `ad.read_h5ad` picks it up for free and never touches
squidpy at all. `Run.run_core`'s `spatial_correlation` step now gates on
`enabled OR local_enabled` and runs whichever (or both) are on, sharing
one `is_command_already_run`/`log_command` check — safe because
`analysis_id` is a fresh UUID per `Run` instance, so this check only ever
matters for resuming a crashed run, never a stale "already ran with
different config" check across separate invocations (confirmed by
reading `Run.__init__`, not assumed).

**GUI**: a second table, `LocalMoranSummaryModel`
(`gui/utils/local_moran_summary_model.py`) — a deliberate near-duplicate
of `SpatialCorrelationModel` (same sort/filter/`mzForRow`/`samples`
shape, cell values formatted as `"42.3%"` instead of `"0.750"`) rather
than a shared base class: only two of these tables exist, and each is
small enough on its own that a shared abstraction wouldn't meaningfully
reduce the code. `AnalysisBridge.getLocalMoranSummaryModel` mirrors
`getSpatialCorrelationModel` exactly (same single-slot cache, same
background-fetch, same state-survives-tab-switch reasoning) — the one
shared piece of logic, `_pivot_spatial_correlation_rows`, gained a
`value_key` parameter (default `"moran_i"`, unchanged for the existing
caller) rather than being duplicated, since it's a small, pure function
with no risk in extending it. `SpatialCorrelationSection.qml` gained a
second, visually identical block below the first (its own empty state,
search fields, header, `TableView`) — also deliberately not factored
into a shared QML component, same "only two, each small" reasoning, kept
separate to avoid touching the first (already-tested) table's existing
`objectName`s at all.

The right-click "Show spatial distribution" handoff from this second
table also switches Visual Inspection's heatmap layer to Local Moran's
I, not just the feature — a new `showLocalSpatialDistributionRequested`
signal, a new `pendingInspectLayer` one-shot property threaded through
`AnalysisPage.qml` alongside the existing `pendingInspectMz`, applied in
`VisualInspectionSection.qml`'s `applyPendingInspect()`. **A real bug
found wiring this up**: `AnalysisPage.qml` assigned `pendingInspectMz`'s
`Qt.binding` before `pendingInspectLayer`'s — but assigning
`pendingInspectMz` fires `applyPendingInspect()` *synchronously* (already
true before this round, see the existing comment on that code), so it ran
before `pendingInspectLayer`'s own binding was even in place, always
reading its stale `""` default and silently never switching the layer.
Fixed by assigning `pendingInspectLayer` first. **Takeaway**: when adding
a second piece of one-shot handoff state to an existing pattern like this,
check which assignment triggers the consuming side-effect and order the
others before it, not after.

No new dependency. Tests: `compute_local_moran_summary`'s per-feature
percentages and the neighbor-graph reuse/sharing behavior (three
dedicated tests, including one that seeds a prior global run's graph and
asserts squidpy is never called again); config version-bump tests;
`Run.run_core` tests confirming `enabled`/`local_enabled` are fully
independent (neither, either, or both); `LocalMoranSummaryModel`'s own
unit tests (mirroring `SpatialCorrelationModel`'s); `AnalysisBridge`
model-caching tests (mirroring `getSpatialCorrelationModel`'s); and QML
tests for the second table's empty state, search, sort/search-survives-
tab-switch, and the layer-switching handoff (which is what caught the
`pendingInspectLayer` ordering bug above).

## Round 5: parallelize the local Moran's I summary pass

Tested against a real, large sample: `compute_local_moran_summary` was a
plain sequential Python loop over every feature, no parallelism at all —
unlike `compute_spatial_autocorr` (global), which hands `n_jobs` straight
to squidpy's own internal joblib-based parallel scoring. User's own
standing instruction once this was pointed out: "Whenever you can, always
parallelize" (saved as a durable preference, see the
`parallelize-by-default-preference` memory).

**New config field**: `local_n_workers: int | None = None` (config
version 19 -> **20**) — deliberately separate from `n_jobs` (which only
ever affected the global pass's own squidpy call), same
`None`/`0`-means-`os.cpu_count()`, `1`-means-serial,
clamped-to-available-work convention as `precursor_purity.n_workers`/
`h5ad.n_workers` elsewhere in this codebase.

**Implementation** (`core/spatial/local_moran.py`): one
`concurrent.futures.ProcessPoolExecutor` worker process per chunk of
features, not per feature — `_resolve_n_workers` (clamping) and
`_chunk_indexed` (contiguous, non-empty, index-preserving chunks — kept
as `(original_index, mz)` pairs rather than bare `mz` floats, so results
reassemble in the original `var_names` order afterward without ever using
a float as a dict key). Each worker's `_init_local_moran_worker`
initializer loads its **own** copy of the sample's `.h5ad` and builds the
neighbor graph **once per worker process** (reusing
`_ensure_spatial_neighbors`, factored out of `local_moran_values` for
exactly this reuse), rather than receiving an already-loaded `AnnData`
through `initargs` — deliberately not relying on `fork`'s copy-on-write
memory sharing (Linux's default start method) to avoid rebuilding shared
state, since `spawn` (Windows/macOS's default) starts each worker as a
genuinely fresh interpreter with no access to the parent's already-loaded
Python objects at all. This mirrors `core.annotation.annotate`'s own
spectral-library-loading initializer exactly — the second instance of
this pattern in the codebase now, worth reusing again for a third.
`n_workers == 1` (or a single feature) skips the pool entirely and runs
the original serial loop in-process.

**Testing note**: mocking `squidpy.gr.spatial_neighbors_grid` in the test
process cannot observe what a *forked/spawned worker process* calls — two
existing tests
(`test_compute_local_moran_summary_builds_neighbor_graph_once_for_every_feature`,
`test_compute_local_moran_summary_reuses_neighbor_graph_from_a_prior_global_run`)
had to pin `n_workers=1` to keep exercising the serial path they were
actually written to test, once the default (`None`) started silently
routing through the new parallel path on any multi-core test machine. The
real correctness check for the parallel path itself is a dedicated test
(`test_compute_local_moran_summary_parallel_matches_serial_results`) that
runs the exact same input through both `n_workers=1` and `n_workers=2`
against **real** subprocesses (not mocked) and asserts the two summaries
are identical — proving the pickling/initializer/chunking/reassembly
plumbing is correct, not just that "some numbers come out."

## Round 6: store the full per-pixel layer, make the threshold live in the GUI

Tested against a real sample: `local_threshold: 1.0` (the config default)
produced "0 features above threshold" — a poorly-chosen number with no way
to recover short of a full re-run, since round 4's design collapsed
straight to one threshold's percentage and discarded the actual per-pixel
values. User's own framing: "can we still show them, but making the user
choose the threshold in GUI? Default is set in config... then user can, in
GUI, decide new threshold." Confirmed with the user first that this is
*not* GUI-only (a real storage-format + pipeline change), then built it.

**Storage**: `compute_local_moran_layer` (renamed from
`compute_local_moran_summary` — it no longer computes a summary) now
writes `adata.layers["localMoranI"]`, the full `(n_pixels, n_features)`
per-pixel result, same shape as `.X`/`.layers["TIC"]` — not a
threshold-collapsed `adata.uns` table. The expensive part (parallelized
per-feature scoring, round 5) is completely unchanged; only the very last
step (collapse-to-percentage) moved out of the pipeline. `threshold` is no
longer a parameter `compute_local_moran_layer` even accepts.

**Config**: `local_threshold`'s role changed from "baked into the stored
summary, needs a re-run to change" to "seeds the GUI's *initial* value
only" — its own field help was rewritten to say so explicitly (still
General/Formula/Range/Interaction, ADR 44's template, but the Interaction
paragraph now says a re-run only changes what the page *starts* at, never
what you can see). No new fields, so no config version bump this round
(19 stays 19) — only what an *existing* field means and what the
`.h5ad` stores changed, not the schema's field set.

**GUI**: `LocalMoranSummaryModel`'s `_all_rows` now hold raw per-pixel
`np.ndarray`s per (feature, sample), not pre-computed floats, plus a new
mutable `threshold` `Property`. Both `data()` (display) and `_sort_key()`
(so sort order can change with the threshold, not just the displayed text)
call `core.spatial.local_moran.pct_above_threshold` (made public,
un-prefixed, specifically so this shared formula lives in exactly one
place) against the raw array *at the model's current threshold*, computed
fresh every time — `setThreshold` just re-applies the existing sort/filter,
no new fetch, no `.h5ad` I/O, an instant in-memory recompute. A new
`localMoranThresholdField` `TextField` in `SpatialCorrelationSection.qml`
(commit-on-`editingFinished`, same convention as `HeatmapControlsPanel.qml`'s
vmin/vmax fields) is the only new UI element.

**Bridge**: `_load_local_moran_layer_data` replaces
`_load_local_moran_summary_table` — reads `adata.layers["localMoranI"]`
per sample (materializing just that one layer out of a `backed="r"` open,
so `.X`/other layers still aren't loaded unnecessarily) and assembles
`LocalMoranSummaryModel.setRows`'s shape directly, bypassing the
flat-DataFrame/`_pivot_spatial_correlation_rows` convention entirely —
that convention exists for `_dataframe_to_records`'s QML-safety scrubbing,
which raw `np.ndarray` cell values never need since they never reach QML
directly (only the formatted percentage strings `data()` computes from
them do). `TableQueryWorker` — previously documented as DataFrame-specific
— turned out to already be generic enough (`Signal(object)`) to carry this
loader's plain `(feature_rows, samples)` tuple return with no changes
needed beyond a docstring/type-hint correction.

**New provenance-recovery helper**, `_load_local_moran_threshold_default`:
reads this analysis' own `commands.arguments` for its `spatial_correlation`
row (most recent, if the stage ran more than once) to recover the
`local_threshold` it was actually configured with, seeding
`LocalMoranSummaryModel.threshold` before any rows load — the same
`SELECT arguments FROM commands WHERE ...` + `json.loads` +
defensive-except pattern `Plotter` already uses to recover
`noise_threshold`/`fragment_ppm` for the mirror plot, reused rather than
invented fresh. Falls back to `1.0` (the config dataclass's own default)
if the stage never ran or the value can't be parsed — never raises.

No new dependency, no config version bump. Tests: rewrote every
local-Moran's-I test file's fixtures to build synthetic
`layers["localMoranI"]` arrays (a small helper realizing an exact
percentage as N pixels above/below a fixed value, so existing percentage-
based assertions carry over unchanged); new `LocalMoranSummaryModel` tests
for `setThreshold` (display update, signal-on-real-change-only, and —
the one that actually matters — sort order changing as the threshold
does); a new QML test driving the real `localMoranThresholdField`
`TextField`'s `editingFinished` signal end-to-end and confirming the
table updates with no new fetch involved.
