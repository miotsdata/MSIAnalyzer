# 34 — Fetch the Annotations table / feature list on a background thread

**Status:** Accepted

## Context

Even after indexing the actual slow query (ADR 33), opening Annotations
and switching to Visual Inspection still freezes the UI — because the
fetch itself (`AnalysisBridge.getAnnotationTable`/`getFeatureList`) ran
straight inside a QML property binding (`AnnotationsSection.qml`'s `rows`,
`HeatmapControlsPanel.qml`'s `features`), evaluated synchronously on the
GUI thread during the section's own `Loader` construction. A blocking
Python call there means Qt's whole event loop can't process anything —
including painting a loading indicator — until it returns. Adding a
spinner without fixing this would just be a spinner that never gets a
chance to appear before the freeze.

Async QML *component construction* (`Loader.asynchronous: true`) was
already tried and reverted once, for unrelated reasons — see
[ADR 14](0014-analysis-workspace-lazy-loading.md): it crashed
intermittently under the offscreen test platform. That's a different
mechanism from threading the *data fetch itself* off the GUI thread, which
this codebase already does, twice: `AnalysisBridge.requestMirrorPlot`
(`MirrorPlotWorker`) and `predictFormulas` (`FormulaPredictionWorker`),
both for the identical "blocks Qt's whole event loop" reason (see the
class's own docstring). Neither of those was implicated in ADR 14's crash.
Extending the same, already-proven pattern to
`getAnnotationTable`/`getFeatureList` doesn't touch what crashed before.

## Decision

- New `gui/utils/table_query_worker.py`: `TableQueryWorker(QThread)`,
  parameterized by which single-argument `analysis_db.load_*` function to
  call — `getAnnotationTable` and `getFeatureList` are the exact same
  shape (one query function, one `analysis_db_path` argument, a
  `DataFrame` result), so one small reusable worker instead of duplicating
  `MirrorPlotWorker`'s boilerplate twice for what's structurally identical
  work.
- `AnalysisBridge`: `getAnnotationTable`/`getFeatureList` (synchronous,
  return-value methods) replaced outright — not kept alongside — by
  `requestAnnotationTable`/`annotationTableReady` and
  `requestFeatureList`/`featureListReady`, mirroring
  `requestMirrorPlot`/`mirrorPlotReady` exactly: fire-and-forget, a kept
  worker reference (`self._annotation_table_worker`/
  `self._feature_list_worker`), and the same `self.sender() is not
  self._worker` discard check so a newer request always wins over a
  slower, now-stale one still in flight.
- `AnnotationsSection.qml`/`HeatmapControlsPanel.qml`: `rows`/`features`
  become plain properties (`[]` initially) plus a `rowsLoading`/
  `featuresLoading` flag, populated by a `Connections` listener on the
  `*Ready` signal instead of a synchronous binding. Triggered from
  `onAnalysisChanged` (and, for Annotations, the existing `refreshToken`
  bump after a formula prediction).
- `AnalysisPage.qml`'s `currentSectionReady` (the existing `LoadingOverlay`
  gate already built for MS1's `plotLoading`, see ADR 14) extended to also
  require `!rowsLoading`/`!featuresLoading` for the Annotations/Visual
  Inspection cases — no new spinner component, the exact same mechanism
  MS1 already uses.
- **A real interaction this surfaced**: "Inspect visually" ([ADR 32](0032-inspect-visually-cross-tab-handoff.md))
  matches a pending m/z against `controls.sortedFeatures` the moment
  `pendingInspectMz` is set — with `features` now async, that list is
  empty at that exact instant. Fixed by making the match-and-select logic
  (`VisualInspectionSection.qml`'s `applyPendingInspect`) re-run on *both*
  `pendingInspectMz` changing and `featuresLoading` finishing, skipping
  (not failing) while still loading.

## Alternatives considered

- **A generic "run any callable on a QThread" worker**, instead of one
  scoped to `analysis_db.load_*`-shaped queries specifically. Rejected —
  this codebase has three now (`RunWorker`, `MirrorPlotWorker`,
  `FormulaPredictionWorker`, plus this one), each scoped to its own
  use-case shape rather than a shared generic abstraction; `TableQueryWorker`
  follows that same convention rather than introducing a new one.
- **Keep `getAnnotationTable`/`getFeatureList` as synchronous methods
  alongside the new async ones**, in case something else calls them.
  Rejected — nothing else does (checked); an unused synchronous duplicate
  of a query already known to be too slow to call from the GUI thread is
  dead weight, not a safety net.

## Consequences

- **Test-suite-wide mechanical fallout**: every GUI test that opens
  Annotations or Visual Inspection needs the fetch to have actually
  finished — and, just as important for test isolation as for
  correctness, needs the background `QThread` to have actually completed
  — before the test's view is torn down, or a signal delivered after
  teardown can land during a *later*, unrelated test. Fixed once, at the
  shared `_open_annotations_tab`/`_open_visual_tab` helpers (three of
  them, across `test_annotations_section.py`, `test_visual_inspection_section.py`,
  `test_roi_design_window.py` — the latter reuses the same
  `HeatmapControlsPanel` instance, not a separate fetch), rather than
  touching every individual test — most tests needed no change at all
  once the shared helpers waited for `rowsLoading`/`featuresLoading` to
  clear.
- Scoped to `getAnnotationTable`/`getFeatureList` only — `getSamples`/
  `getObsColumns` (`.h5ad`-backed, not `ms2_annotations`-backed, and not
  implicated by the actual slowness report) are left synchronous.
