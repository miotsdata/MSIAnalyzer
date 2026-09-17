# 42 — `library_path`: list+remove widget, and a Python→QML array bug

**Status:** Accepted

## Context

Reported: reloading a previous run's config as a New Analysis template
showed the annotation library path field as `"//"` (an increasing run
of slashes with more libraries) instead of the actual path(s). Separately,
the user asked for the field itself to work like the `io` tab's sample
table — pick many files, see them listed, remove individual ones — instead
of a single read-only text field showing every path joined by `; `.

Root-caused live (a debug `property string` dumping `typeof`/
`Array.isArray`/`JSON.stringify` on the value `NewAnalysisPage.qml`
actually received, confirmed via a throwaway repro test before writing
any fix): `applyFieldValue`'s `"path_list"` case was
`Array.isArray(value) ? value.join("; ") : (value || "")`. A `list[str]`
built in Python and handed to a QML JS function (here: `run.config`, a
plain dict crossing the C++/Python↔QML boundary) arrives as a genuinely
array-*like* object — `typeof value === "object"`, `value.length` works,
indexing works — but it does **not** satisfy `Array.isArray()`. The code
fell into the `else` branch and assigned that whole array-like object
directly to `control.text` (a `string`-typed property); QML's own
implicit-to-string coercion for that shape is not `Array.prototype.join`,
producing the reported garbage (empirically: 2 paths → `"//"`). A single
bare string path (the far more commonly tested case — see the "Alternatives
considered" note on test coverage) never hit this branch at all, which is
why it went unnoticed.

`target_list.paths` is the same `"path_list"` shape and goes through the
exact same `applyFieldValue` function — it silently had the identical
latent bug, just never reported (no existing test loaded a *previous run's
config* with 2+ target-list paths through `applyConfig`, only a fresh
selection through the FileDialog, which builds the array in JS and never
crosses the Python boundary).

## Decision

- New `normalizePathListValue(value)` in `NewAnalysisPage.qml`: `null`/
  `undefined`/`""` → `[]`, a `string` → `[string]`, anything else iterated
  by `.length`/index (not `Array.isArray`) into a real JS array. This is
  now the one place that knows how to coerce a `path_list`-shaped config
  value regardless of which side of the Python/QML boundary it came from.
  `applyFieldValue`'s `"path_list"` case now routes through it
  (`normalizePathListValue(value).join("; ")`) — fixes `target_list.paths`
  too, for free, with no `target_list`-specific change needed.
- `library_path` itself is pulled out of that generic text-field
  machinery entirely and redesigned as list+remove state:
  `newAnalysisPage.libraryPaths` (a plain array property) plus
  `addLibraryPaths(paths)` (append, de-duplicated) and
  `removeLibraryPath(index)`. The generic per-field QML delegate's
  `"path_list"` branch (previously a read-only `TextField` + "Browse…")
  is now a `ColumnLayout`: the same "Browse…" button (still using
  `libraryPathDialog`, `FileDialog.OpenFiles`, unchanged), then one row
  per already-picked path with its own "Remove" button — same pattern as
  the `io` tab's own sample-row table (`sampleRows`/`removeSampleRow`),
  which is exactly the "like file IO" comparison in the original ask.
- `collectConfig`/`applyConfig`/`resetToBlank`'s three generic
  per-group-field loops each special-case `field.kind === "path_list"` to
  read/write `newAnalysisPage.libraryPaths` directly instead of going
  through a control's `.text` — `library_path` is the only field of this
  kind, so this is a narrow, safe branch, not a general schema change.
  `collectConfig`'s output shape is unchanged (`null` / a bare string for
  one path / a list for 2+), matching what `AnnotateConfig.library_path:
  str | list[str] | None` and `core.annotation.normalize_library_paths`
  already expect.

## Alternatives considered

- **Just fixing `Array.isArray`, keeping the single joined-text-field
  UI.** Would have resolved the reported garbage but not the actually-
  requested UX (list + remove, matching the `io` tab's own pattern) — the
  user asked for both in the same message, not just the bug.
- **A shared `PathListField.qml` component** used by both `library_path`
  and `target_list.paths`. Rejected for this round — `target_list.paths`
  wasn't reported as wanting the list+remove treatment, and forcing it
  into the same component now would be a larger, unrequested UI change.
  Worth revisiting if `target_list.paths` gets the same request later.

## Consequences

- `library_path`'s `objectName` (`field_annotate_library_path`) now
  identifies a `ColumnLayout`, not a `TextField` — any external code or
  test reading `.property("text")`/`.property("readOnly")` on it needs
  updating (none existed outside this feature's own now-updated tests).
- The reproduced bug and its debugging path (a temporary `property var`
  dumping `typeof`/`Array.isArray`/`JSON.stringify`, since QML `console.log`
  output wasn't reliably visible in this test harness — reading the
  property back from Python after the fact was) is worth remembering
  for any future "a Python list looks wrong once it reaches QML" report.
