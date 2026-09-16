# 29 — Compound search syntax (Annotate + Visual Inspection)

**Status:** Accepted

## Context

Neither the Annotate table nor Visual Inspection's feature picker had any
way to find a specific compound by name or m/z — with dozens to hundreds of
features per analysis, scanning a flat list (or a combobox popup) to find
one was the reported pain point. Both places already hold their full
feature/row list client-side (`AnnotationsSection.rows`,
`HeatmapControlsPanel.features`) and already do JS-side sorting, so filtering
is naturally a client-side, no-bridge-change addition in both.

A single search box needs to accept both a compound name and an m/z, since
users think of a feature by either depending on whether it's annotated yet.

## Decision

One query syntax, shared verbatim between both places via a new
`qml/Utils/SearchQuery.js` (`pragma Library`) module's `matches(query, name,
mz)`:

- `"<num>-<num>"` (e.g. `"150-160"`): m/z range match, order-independent.
- A single parseable number (e.g. `"150.1234"`): m/z within ±0.01 Da.
- Anything else: case-insensitive substring match against the compound name
  (never matches an unannotated feature, which has no name).
- Empty/whitespace: matches everything.

±0.01 Da was picked as "close enough to mean *this* peak" at the mass
resolution this app already works at (the same tolerance IONS scored at in
`AnnotateConfig` are ppm-based and much finer, e.g. `candidate_ppm`; this is
a UI convenience for a human typing a remembered m/z, not a scientific
matching tolerance) — a plain Da window keeps the UI-facing behavior easy to
reason about, independent of any per-analysis config a user typing a search
term wouldn't have in mind anyway.

A shared `.js` module (registered in `resources.qrc`, picked up automatically
by the existing `compile_qml_resources` test fixture) rather than duplicating
the ~20-line parser in both `AnnotationsSection.qml` and
`HeatmapControlsPanel.qml` — small, but non-trivial enough (range parsing,
tolerance, the name/mz precedence) that a drift between two copies would be
an easy, silent way for the two search boxes to stop behaving identically.

## Alternatives considered

- **Separate name field + separate min/max m/z fields**, instead of one
  combined box. Rejected — more controls for a feature meant to be a quick,
  low-friction lookup; one box covers the common cases (a remembered name, a
  remembered m/z, a rough range) without asking the user to pick which field
  to use first.
- **No m/z tolerance for a bare number** (require an explicit range for any
  m/z search). Rejected — typing an exact m/z and getting zero results due
  to float precision (`123.4567` vs `123.45670001`) would be a confusing
  dead end for the most obvious thing to type.

## Consequences

- `HeatmapControlsPanel` is shared by Visual Inspection's multi-sample grid
  and ROI Design's single-sample view — the search box (and its "no
  features match your search" empty state) is available in ROI Design too
  as a side effect, not a separately scoped addition. Harmless: ROI Design
  needs the same feature picker either way.
- The search box's `text` carries a declarative binding back to a
  `searchQuery` property (for consistency with how every other control in
  these panels already exposes its state as a plain property, testable and
  settable directly) — a plain `Qt.setProperty("text", ...)` write from
  outside (e.g. a test) does not fire the box's own change handler the way
  real typing does; confirmed directly while writing this feature's tests,
  which now use real key events instead.
