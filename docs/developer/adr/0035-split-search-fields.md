# 35 — Split compound search into separate name and m/z fields

**Status:** Accepted. Supersedes [ADR 29](0029-compound-search-syntax.md).

## Context

ADR 29 shipped one combined search box (Annotate + Visual Inspection),
auto-detecting whether the typed text was a name, a single m/z, or an
m/z range — and explicitly considered and rejected separate fields:
"more controls for a feature meant to be a quick, low-friction lookup;
one box covers the common cases without asking the user to pick which
field to use first." Real use turned up exactly the ambiguity that
rejection assumed away: requested directly — "doesn't mean that the
search box is just one. I need one for mz and one for name."

## Decision

Two fields everywhere the one combined box was — `AnnotationsSection.qml`'s
table header (`Name` / `m/z`, side by side, the panel is wide enough) and
`HeatmapControlsPanel.qml`'s feature picker (stacked, the panel is a fixed
300px wide). `SearchQuery.js`'s single `matches(query, name, mz)` is
replaced by `matchesName(query, name)` (plain substring, unchanged
behavior) and `matchesMz(query, mz)` (the same range/single-value/
tolerance parsing ADR 29 already had, just no longer auto-detected against
a name). Both queries are ANDed — a row must satisfy whichever fields are
non-empty, not just one of them — so narrowing by both name and m/z at
once works as a combined filter, not two independent searches.

## Alternatives considered

- **Keep the single auto-detecting box.** Rejected — this is the decision
  being reversed; real use showed the auto-detection genuinely ambiguous
  often enough to be worth two explicit fields instead.
- **OR semantics instead of AND** (a row matches if either field matches).
  Rejected — two separate fields read as "narrow by this AND that," the
  same expectation any faceted-filter UI sets; OR would make filling in
  both fields *widen* results, which is the opposite of what a second
  filter field normally means.

## Consequences

- Every existing test asserting against the old single `searchQuery`
  property/`*SearchField` objectName needed updating to the new
  `nameQuery`/`mzQuery` properties and `*NameSearchField`/`*MzSearchField`
  objectNames — mechanical, not a design change.
- `SearchQuery.matchesMz`'s ±0.01 Da tolerance and range/single-value
  parsing (ADR 29's own reasoning) is unchanged — only which field it's
  wired to changed, not how m/z matching itself behaves.
