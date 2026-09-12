# 16 — Persist the untouched library spectrum alongside the filtered one

**Status:** Accepted; storage design superseded by
[ADR 18](0018-reconstruct-filtered-spectra-on-demand.md), which stops
persisting the filtered/normalised copies at all (reconstructing them from
the raw arrays instead) and extends the same "persist the untouched
spectrum" idea this ADR established for the library side to the empirical
side too. The core argument below — a live re-read of a possibly slow or
unreachable file is a real crash risk, so persist the raw material once and
avoid re-deriving anything — still holds and motivated ADR 18 directly.

## Context

The GUI's Annotations section lets the user view a mirror plot's library
side as either the stored, noise-filtered spectrum or the "raw" (untouched)
one. Viewing "raw" previously meant re-opening the *original spectral
library file* live, on demand (`annotate.load_library` + a `Spectrum`
query by id) — a file that isn't guaranteed to be local or fast: spectral
libraries are commonly kept in one shared, central location (unlike a
per-project raw per-sample database), often on a network mount.

That live re-read ran synchronously and — even after being moved to a
background thread (see [ADR 14](0014-analysis-workspace-lazy-loading.md)'s
sibling `MirrorPlotWorker` fix) — remained a real dependency on that file
still existing, at that path, reachable, every single time a user wanted
to look at it. A slow or now-unreachable library file meant a slow or
broken "raw" view no matter how well the GUI itself behaved.

Meanwhile, `annotate.py`'s scoring path already reads the *exact* raw
spectrum it needs — `Candidate.mz`/`Candidate.intensity`, built once per
candidate from `library.get_spectra_in_mz_range(...)` — before any
noise-filtering happens. It was already in memory, unmutated, at the same
point `lib_filtered_mz`/`lib_filtered_intensity` (the *filtered* copy) get
attached to the row for storage.

## Decision

Persist the untouched candidate spectrum too: `ms2_annotations` gains two
columns, `lib_raw_mz` / `lib_raw_intensity` (`create_analysis_schema`,
`analysis_db.py`), populated from `Candidate.mz`/`Candidate.intensity` in
`_row_from_match` and written by `persist_annotations` under the same
`store_filtered_spectra` flag that already gates the four `*_filtered_*`
blobs — one flag, one meaning: "keep enough of the raw material around to
redraw mirror plots without re-deriving anything," now including the raw
library side.

`Plotter._resolve_side`'s library "raw" branch now checks the stored
column first and only falls back to the live library-file re-read when
it's empty (a row written before this column existed, or with
`store_filtered_spectra` off) — existing analyses keep working exactly as
before, just via the slower path, until re-run.

The raw *empirical* spectrum is unaffected by this ADR — it already lives
in the sample's own raw per-sample database, which (unlike a spectral
library) is typically local to the project, and is read the same way as
before.

## Consequences

- `ms2_annotations` grows two more BLOB columns per row — on the same
  order of size as the existing `lib_filtered_mz`/`lib_filtered_intensity`
  pair (the raw spectrum before noise-filtering is usually similar in
  size, sometimes larger). No `Config.version` bump: this doesn't change
  any config field, just what one existing flag persists — matching the
  established "column addition to `ms2_annotations`, no migration"
  precedent ([ADR 10](0010-score-weights-and-flat-fragmentation.md)'s
  `flat_fragmentation` addition). Existing analysis databases have NULL
  here until re-run; the GUI's live-read fallback covers that gap, not a
  hard break.
- The GUI's raw-library view is now local/fast (no file I/O at all) for
  any analysis run after this change — closing off the slow-network-mount
  crash risk for the common case; the background-thread fix
  ([ADR 14](0014-analysis-workspace-lazy-loading.md)) remains the safety
  net for the fallback path and for the still-live raw-empirical read.
