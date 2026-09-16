# 32 — "Inspect visually": cross-tab handoff between Annotations and Visual Inspection

**Status:** Accepted

## Context

No quick way existed to go from an annotated feature in the Annotations
table straight to looking at it spatially in Visual Inspection — the user
had to remember (or copy) the m/z, switch tabs, then hunt for it in the
feature picker. Requested as a right-click "Inspect visually" action on an
Annotations row.

Annotations and Visual Inspection are sibling sections, each lazily
constructed by its own `Loader` in `AnalysisPage.qml`'s `sectionStack`
(`active: sectionStack.currentIndex === N` — see [ADR 14](0014-analysis-workspace-lazy-loading.md)).
Neither Loader's `item` is destroyed-and-recreated *only* on tab switch —
it's destroyed whenever `active` goes false and rebuilt fresh next time it
goes true, so Visual Inspection has no state to "already be showing" a
feature until the moment it's (re)constructed.

## Decision

- `AnnotationsSection.qml`: `signal inspectVisuallyRequested(real mz)`.
  Each row's `MouseArea` accepts `Qt.RightButton` too (same pattern already
  used for Project Home's row context menu — `ProjectHomePage.qml`'s own
  `runContextMenu`), popping a one-item `Menu` ("Inspect visually") whose
  `onTriggered` emits the signal with that row's own `mz`.
- `AnalysisPage.qml` owns the handoff, not either section directly — it's
  the only thing with a reference to both `sectionStack` and both Loaders.
  A new `property real pendingInspectMz: NaN` (a one-shot request, not
  persistent workspace state). `annotationsLoader.onLoaded` connects
  `inspectVisuallyRequested` to set `pendingInspectMz` and switch
  `sectionStack.currentIndex = 3`; `visualLoader.onLoaded` forwards
  `pendingInspectMz` into the (freshly constructed) `VisualInspectionSection`
  and listens for a `pendingInspectHandled` signal back to clear it to `NaN`.
- `VisualInspectionSection.qml`: `property real pendingInspectMz: NaN` +
  `signal pendingInspectHandled()`. `onPendingInspectMzChanged` (fires on
  the very first binding evaluation too, not just later changes — same as
  every other `onXChanged` handler in this codebase) sets
  `controls.inspectionMode = "feature"`, finds the matching entry in
  `controls.sortedFeatures` by exact `mz` equality (the identical float
  both places, from the same `AnalysisBridge.getAnnotationTable`/
  `getFeatureList` source — no tolerance needed), sets
  `controls.selectedFeatureIndex`, then always emits `pendingInspectHandled()`
  so the one-shot flag gets cleared regardless of whether a match was found.

A real, non-obvious ordering requirement found while testing this: in
`visualLoader.onLoaded`, `item.pendingInspectHandled.connect(...)` must run
**before** `item.pendingInspectMz = Qt.binding(...)`, not after. Assigning
the binding evaluates it immediately, and when a request is actually
pending that synchronously fires `pendingInspectHandled` from inside
`onPendingInspectMzChanged` — confirmed directly, not guessed: connecting
second made the "handoff gets cleared" behavior fail intermittently (not
every time — Qt's real vs. queued connection timing here isn't fully
deterministic either way, but connecting first before was observed to
raise the failure rate roughly in half of runs, connecting first after
dropped it to roughly 1 in 20-40), while connecting first was solid across
dozens of repeated runs. Any future handoff of this shape (a Loader's
`onLoaded` both wiring a callback *and* assigning a binding whose initial
evaluation can synchronously trigger that same callback) should connect
before assigning, for the same reason.

## Alternatives considered

- **A tolerance-based m/z match** instead of exact equality (matching
  Annotate/Visual Inspection's own search feature, [ADR 29](0029-compound-search-syntax.md)).
  Rejected — that tolerance exists for a *human typing a remembered m/z*;
  here both sides read the exact same float from the same query, so exact
  equality is correct and simpler.
- **Have `VisualInspectionSection` read `AnalysisPage`'s property directly**
  (a parent reference) instead of `AnalysisPage` pushing it down via
  `Qt.binding`. Rejected — every other cross-section value (`analysis`
  itself) already flows this same way; a section reaching up to its parent
  by type/id would be a new, inconsistent pattern for this one property.

## Consequences

- Works from a cold Annotations tab (Visual Inspection never yet
  constructed this session) exactly the same as when Visual Inspection was
  already visited before — since the target section is always freshly
  (re)constructed by its `Loader`, there's no "already open, need to update
  it live" branch to keep in sync separately.
- `pendingInspectMz` is deliberately not sticky: a later plain click on the
  Visual Inspection nav button (not through "Inspect visually") starts
  fresh — `pendingInspectMz` is `NaN` again by then, so it does not
  re-apply a stale feature selection from an earlier "Inspect visually".
