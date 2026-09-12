# 14 — Analysis workspace: lazy `Loader` sections + blocking loading overlay

**Status:** Accepted

## Context

`AnalysisPage.qml`'s four sections (Summary, MS1 Spectra, Annotations,
Visual Inspection) were originally all constructed eagerly when the
workspace opened, via a `StackLayout` of directly-embedded QML items. As the
sections grew (MS1 and Annotations each embed a `WebEngineView`; Visual
Inspection renders a grid of heatmap tiles), this made opening any analysis
pay the full construction cost of all four sections up front, and produced a
visible "page construction" delay/flicker when switching tabs on a large
analysis.

`StackLayout` keeps every child as a permanent sibling rather than
destroying hidden ones — so unlike a `StackView` push/pop, there was no
natural point at which an inactive section's `WebEngineView` got torn down
between visits.

## Decision

- Wrap each non-default section in a `Loader` with `active: sectionStack.currentIndex === N`
  (already the existing pattern for MS1, since it owns a `WebEngineView`;
  extended to Annotations and Visual Inspection). Only Summary (cheap, and
  the default landing section) stays eager. Only the active tab's section
  is ever constructed; switching tabs constructs on first visit and reuses
  after.
- A reusable `LoadingOverlay` component (opaque backdrop + `BusyIndicator`)
  covers the content area — and disables the nav rail / "Back to project"
  button, dimmed to reduce ambiguity about why — whenever the active
  section isn't ready yet: `Loader.status !== Loader.Ready`, or, for MS1
  specifically, its embedded plot hasn't finished loading either
  (`MS1SpectraSection.plotLoading`, derived from the `WebEngineView`'s own
  `url`/`loading` properties). The overlay also carries its own
  click/wheel-blocking `MouseArea` so a click cannot reach not-yet-ready
  content underneath it, not just visually hide it.
- Deliberately **not** `Loader.asynchronous: true`: tried during
  development, and it reproducibly (~1 in 6 full-suite `tests/gui` runs)
  crashed with `Fatal Python error: Aborted` under the offscreen QPA
  platform used for testing. Reverting it stopped that specific signature.

## Consequences

- A rarer crash (roughly 1 in 15-30 full-suite runs, vs. zero in 16+ runs
  on the pre-change code) persisted even with every `Loader` synchronous,
  and was never root-caused despite substantial investigation (bisection,
  `gdb`, standalone repro attempts, a baseline `git stash` comparison). It
  did not reproduce in any standalone (non-pytest) script, and the one full
  traceback captured pointed at an unrelated *later* test — suggesting a
  deferred effect (something torn down mid-animation or mid-load under
  offscreen-QPA-specific conditions) rather than an immediate one.
  Suspected area: `BusyIndicator`'s continuous spin animation and/or
  `WebEngineView`'s async load interacting with Qt's view-teardown
  lifecycle. Shipped anyway rather than drop the feature over a rare,
  only-partially-diagnosed finding.
- As a follow-up mitigation (not a confirmed fix — see above), the nav
  rail/back-button disabling was added specifically to close off one
  concrete, reachable race: nothing previously stopped a click landing on
  another tab, or "Back to project", while the *previous* tab's content was
  still mid-load/mid-teardown. 20 full-suite runs with this mitigation
  showed zero crashes; an 8-run baseline without it still hit the known
  crash once — too small a sample to call this root-caused, but a
  reasonable mitigation regardless of whether it is.
- Any future section added to this workspace that owns a `WebEngineView` or
  other externally-driven async resource should follow the same lazy-Loader
  + `plotLoading`-style readiness signal pattern, not assume eager
  construction is safe once the workspace grows further.
