# 44 — Structured Markdown field help, in a dedicated window

**Status:** Accepted

## Context

Config field help text (New Analysis's per-field "?" button) was one
flat, hand-wrapped paragraph per field, shown in a hover `ToolTip` —
fine for a single sentence, unreadable once a field's explanation
genuinely needs several distinct pieces (what it does, the formula
behind it, what its numbers mean, how it interacts with other
settings), all run together with no visual separation and gone the
moment the mouse moves away.

Agreed convention for a field's help text, in order: a plain-language
general description; the formula, if the setting is mathematical; for
a numeric setting, its range and what happens near each end; and
always, an "Interaction with other settings" statement — explicitly
`None.` when there genuinely isn't one, never silently omitted (for a
bool/string field, only the description + interaction paragraphs
apply — no formula/range placeholder).

## Decision

- **Format: Markdown**, rendered by Qt Quick's own `Text {
  textFormat: Text.MarkdownText }` — no custom markup language or
  renderer. `**bold**` marks numbers worth a reader's eye (defaults,
  thresholds, range bounds); blank lines become real paragraph breaks.
- **Docstring convention** (`core/config/config.py`): each `Attributes:`
  entry may now be several Markdown paragraphs instead of one run-on
  sentence — general description, then (blank line) formula, then
  (blank line) range/near-min/near-max, then (blank line)
  `**Interaction:** ...`. Still hand-wrapped at ~79 columns like every
  other docstring here; only a genuinely blank line signals a new
  paragraph.
- **Parser** (`config_schema.py`): `_parse_docstring`'s attribute-text
  collection previously skipped blank lines outright and joined every
  continuation line with `" "`, flattening any paragraph structure. Now
  factored into `_join_paragraphs`: consecutive non-blank lines still
  unwrap into one paragraph (undoing the hand-wrap), but a blank line
  starts a new one, rejoined with `"\n\n"` so the Markdown source
  reaching the GUI has its paragraph breaks intact. Fully backward
  compatible — a field whose docstring has no blank line (every
  not-yet-converted field) parses to exactly the same single-paragraph
  string as before.
- **GUI**: every field's "?" `ToolButton` now opens one shared,
  read-only `Dialog` (`fieldHelpDialog`) on click instead of showing a
  hover `ToolTip` — a multi-paragraph explanation doesn't fit a
  tooltip's cramped, mouse-away-dismisses shape. The dialog is safe to
  share across every field on the page (unlike `libraryPathDialog`/
  `targetListPathsDialog`, which write into one specific field — see
  their own comments) because it only ever *displays* whatever
  `openFieldHelp(title, body)` was last called with, never writes
  anywhere. Content renders inside a `ScrollView` (some fields'
  explanations run several paragraphs).
- **First-batch content conversion**, to prove the mechanism before a
  full sweep: `purity.enabled` (bool — no formula/range, per the "skip,
  don't placeholder" rule), `purity.fragmentation_factor_mz_tol_da`,
  `group_ms2.flat_fragmentation_cv_threshold`,
  `annotate.min_purity_score` (all three numeric, with formula/range/
  interaction), and `annotate.library_path` (string/path-list — no
  formula/range, an interaction note only). The remaining ~75 fields
  keep their original one-paragraph docstrings for now — parsed and
  rendered exactly as before, nothing broken by leaving them
  unconverted — and get the same treatment in a follow-up pass.

## Alternatives considered

- **A structured per-field dict** (separate `description`/`formula`/
  `range`/`interaction` keys in the schema, each rendered into its own
  `Label`) instead of one Markdown blob. Rejected — would need a new,
  stricter docstring grammar (explicit section markers) instead of
  reusing Google-style `Attributes:` almost as-is, and gains nothing
  `Text.MarkdownText` doesn't already render for free.
- **Keeping the hover `ToolTip`, just widening it.** Rejected —
  explicitly asked for a dedicated window so multi-paragraph content
  isn't dismissed by the mouse moving away mid-read.

## Consequences

- `field.help` values are now Markdown source, not plain sentences —
  any other future consumer of `ConfigSchema`'s `help` field (there are
  none today besides this button) needs to either render it as
  Markdown too or accept the literal `**`/`\n\n` characters.
- The three target-list-specific help buttons (previously hand-authored
  outside the generic per-field loop) now share the exact same dialog/
  click mechanism as every generic field's button, rather than each
  carrying its own `ToolTip`.
- Converting the remaining ~75 fields' docstrings to the structured
  format is follow-up work, not done here.

**Follow-up (2026-09-17, same day):** two real problems found once the
user actually opened the dialog in the running app:

- **Text rendered black-on-dark.** The `fieldHelpDialogText` element had
  no `color:` binding — a plain `Text` doesn't inherit the app's palette
  the way `Controls` do, so it fell back to Qt Quick's own default
  (black), unreadable against `Theme`'s dark background. Fixed with an
  explicit `color: Theme.textColor` binding.
- **The "convert a first batch, do the rest later" staging was the
  wrong call** — reported back as "this is a huge mistake that you
  shouldn't have made." Every remaining GUI-visible field across every
  `Config` dataclass (`ms1`, `centroid`, `peak`, `align.align_ppm`/
  `mz_decimals`, `target_list.paths`/`polarity`/`match_ppm`, the rest of
  `group_ms2`/`purity`/`annotate`, `consensus`, `report`, `h5ad`,
  `analysis`, `normalization` — everything with a "?" button in the New
  Analysis form) was converted to the structured format in the same
  sitting, not staged. `align.sample_names` (marked `gui_hidden`, no
  help button at all) and `target_list.adducts` (rendered as a checkbox
  grid, no help button either) are the only fields left with a plain
  one-paragraph docstring, since neither has anywhere in the GUI to
  show it.
