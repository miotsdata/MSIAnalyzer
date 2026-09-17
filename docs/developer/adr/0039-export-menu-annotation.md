# 39 — Export menu: Annotation export

**Status:** Accepted

## Context

First of three planned Export-menu actions (Annotation / Integration /
Image — the other two follow in their own ADRs). A new top-level "Export"
menu, visible/enabled only while an analysis workspace is open, writes
one row per feature (identity across whichever tier applies — target
list, ms2, predicted — plus a row for a feature with none at all,
enabled by [ADR 38](0038-adduct-cas-hmdb-capture.md)'s new
`include_unidentified` parameter) to a user-chosen CSV or tab-delimited
text file.

## Decision

- `core/export.py` (new top-level module, not nested under
  `core/annotation/`): `export_annotation_table(db_path, dest_path)`,
  reusing `load_feature_representative_annotations(db_path,
  include_unidentified=True)` directly rather than a parallel query —
  the GUI's own Annotations table and this export now share one function,
  one bug surface, one place to add a future field. Delimiter keyed off
  `dest_path`'s own suffix (`.txt` → tab, else comma); every string field
  quoted via `csv.QUOTE_NONNUMERIC` regardless of content (a compound
  name is free text from a library file, not guaranteed comma-free) —
  `feature_id`/`mz` are the only fields written as real numbers, so
  they're the only ones left unquoted.
- `gui/utils/export_worker.py`: `ExportWorker(QThread)`, generic over
  which export function runs (bound via a closure, since the three planned
  export functions have entirely different argument shapes — a
  destination file vs. a destination folder vs. a folder plus a pile of
  display settings — with no shared signature worth forcing them into).
  Same fire-and-forget/discard-stale-result shape as `TableQueryWorker`
  (ADR 34) — writing a real file is not "fast enough to stay synchronous"
  either.
- `AnalysisBridge.exportAnnotationTable(analysis_db_path, dest_path)` /
  `exportFinished(message)` / `exportFailed(message)` — one pair of
  signals shared by every future Export action too, not one pair per
  action; a human-readable message rather than a typed result, since
  every export action's "what happened" is just prose shown in a dialog.
- `Main.qml`: `window.currentAnalysis` (new, same shape as the existing
  `window.currentProject` — set in `onShowAnalysisRequested`, cleared on
  every navigation-away `Router` signal), a new `Menu { title: "Export";
  enabled: window.currentAnalysis !== null }` right after "Analyses",
  same whole-menu-disabled treatment "Analyses" itself already gets
  without a project. `Export > Annotation…` opens this app's first
  `FileDialog { fileMode: FileDialog.SaveFile }` — every existing
  `FileDialog` here chooses an existing input file, this one chooses a
  destination that doesn't exist yet.

## Alternatives considered

- **A typed/structured result from `ExportWorker`** (e.g. a dict with
  `path`/`n_rows`) instead of a plain message string. Rejected — every
  planned export action's "done" state is fundamentally "tell the user
  what happened," and a shared plain-string pair keeps `AnalysisBridge`
  from growing a new signal pair per action.
- **A separate query for the export**, leaving
  `load_feature_representative_annotations` untouched. Rejected — the
  export wants exactly what the GUI table already computes, just every
  feature instead of only identified ones; a boolean parameter is a much
  smaller surface than a near-duplicate ~40-line query to keep in sync.

## Consequences

- Every future Export action reuses `ExportWorker`/`exportFinished`/
  `exportFailed` — Integration and Image just need their own `AnalysisBridge`
  method and QML dialog, not new plumbing.
- `csv.QUOTE_NONNUMERIC` quotes the header row too (every column name is
  a plain Python `str`), not just values that happen to need it — a
  deliberate, confirmed side effect of "quote every string field
  unconditionally," not a bug.
