# 36 — Reserve fixed layout space instead of swapping visible siblings

**Status:** Accepted

## Context

Titles and panels visibly shifted position depending on whether the
content below them was empty or populated — reported specifically for the
Annotations table when a search had no matches, and for the mirror-plot
panel's own title when selecting a feature with no MS2 spectrum.

Root cause, confirmed by reading the code (not guessed): in
QtQuick.Layouts, an item with `visible: false` claims zero space and its
siblings reflow to fill the gap — true of *any* item, not just ones with
`Layout.fillHeight/fillWidth: true`. `AnnotationsSection.qml` has this
exact "swap a hidden empty-state label for a shown real-content item"
shape in four places (the feature table, the top-hits list, the match
stats, the mirror-plot image), plus a fifth introduced by the search
feature added earlier the same day (`HeatmapControlsPanel.qml`'s feature
combo/empty-label). Whichever one was empty at the moment shrank its own
panel's implicit height, which shifted every sibling pane below it inside
the vertical `SplitView` (`detailSplit`) — including `basicPlotPanel`'s own
"Mirror plot:" title, the one specifically reported.

## Decision

Same fix, all five places: wrap the "empty-state label OR real content"
pair in one plain `Item` carrying the `Layout.fillWidth`/`Layout.fillHeight`
(or, for `HeatmapControlsPanel.qml`'s feature combo — which never stretched
to fill leftover space to begin with — `Layout.preferredHeight` bound to
the combo's own `implicitHeight`) that the two children used to carry
individually. The wrapper is what the outer `ColumnLayout`/`SplitView`
pane actually measures; the two children switch from `Layout.fillWidth`/
`Layout.fillHeight` to `anchors.fill: parent` (the real content) /
`anchors.centerIn: parent` or `anchors.top: parent.top` (the short
empty-state message), so only what's drawn *inside* that now-fixed space
changes, never the space itself.

This removes the need to reason about exactly how `SplitView.preferredHeight`
falls back when a pane's own implicit content size changes (a question this
same file's own history already flags — see `detailSplit`'s comment on
`SplitView.preferredHeight` vs. `Layout.fillHeight` fights) — once no
pane's implicit height can change at all when its content toggles, the
mechanism behind the shift doesn't exist regardless of `SplitView`'s exact
internal fallback behavior.

## Alternatives considered

- **Fix `SplitView.preferredHeight`/add explicit sizing overrides to the
  three vertical panes instead.** Rejected — would require confirming
  exactly how `SplitView` falls back to implicit sizing (undocumented,
  and this file's own history already records this codebase fighting
  `SplitView` sizing surprises more than once); fixing it at the source
  (never let implicit height change) sidesteps needing to pin that down.
- **Keep the empty-state label and real content as visibility-toggled
  siblings, but give the *empty-state label itself* `Layout.fillHeight:
  true`.** Rejected — a label with `Layout.fillHeight` doesn't grow its
  own implicit height to match its sibling's; it would still leave the
  panel's total content height different between the two states, just
  with the label mispositioned within it rather than fixing the actual
  problem.

## Consequences

- Purely a rendering/layout fix — no property, signal, or test-facing API
  changed; every `objectName` and property this touches (`tableFlickable`,
  `basicPlotImage`, `featureCombo`, etc.) is unchanged, just now living one
  level deeper inside a wrapping `Item`.
- The same pattern is worth reaching for by default whenever a future
  panel needs an "empty state OR real content" toggle inside a
  `ColumnLayout`/`SplitView` pane in this workspace — reserving space with
  an always-visible container is now the established convention here, not
  a one-off fix.
