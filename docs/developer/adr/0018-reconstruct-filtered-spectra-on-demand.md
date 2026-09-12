# 18 — Reconstruct filtered spectra on demand instead of storing them

**Status:** Accepted

## Context

Every `ms2_annotations` row stored four extra BLOB columns —
`emp_filtered_mz`/`emp_filtered_intensity`/`lib_filtered_mz`/
`lib_filtered_intensity` — the noise-filtered, max-normalised copies of
both spectra actually scored, purely so a mirror plot could redraw exactly
what `reverse_dot_product` compared without re-deriving anything. The user
proposed avoiding that duplication by storing only the untouched spectra
plus a boolean mask of which peaks survived filtering, reconstructing
either view from the one stored copy.

Looking at `spectral_match.reverse_dot_product` more closely, filtering
needs even less than a mask: it's `intensity / intensity.max()` followed by
dropping peaks below `noise_threshold * 1.0` (the cutoff is always a
fraction of the *already max-normalised* max, so the base peak — the
easiest peak to keep — is guaranteed to survive) — a pure, deterministic,
positional function of exactly three things: the raw `mz` array, the raw
`intensity` array, and the single scalar `noise_threshold`. No merging,
reordering, or interpolation ever happens. `noise_threshold` is already one
field on the same `AnnotateConfig` whose `fragment_ppm` a mirror plot
already recovers from `commands.arguments` (see the `fragment_ppm`
resolution added for
[ADR 14](0014-analysis-workspace-lazy-loading.md)) — the same recovery
mechanism trivially extends to it. So neither a second stored copy nor a
stored mask is needed: raw + `noise_threshold` alone is a strictly smaller,
strictly simpler superset of what the mask idea would have stored.

Meanwhile [ADR 16](0016-store-raw-library-spectrum.md) had already
established persisting the untouched *library* spectrum
(`lib_raw_mz`/`lib_raw_intensity`) specifically to avoid a live re-read of a
possibly slow/remote library file — but the untouched *empirical* spectrum
was never persisted at all; viewing it raw always meant re-opening the raw
per-sample database live. Extending "persist the untouched copy" to the
empirical side too closes that same gap symmetrically.

## Decision

`ms2_annotations` now stores only the untouched spectra on both sides —
`emp_raw_mz`/`emp_raw_intensity` (new) and `lib_raw_mz`/`lib_raw_intensity`
(from ADR 16, unchanged) — gated by a single renamed config flag,
`AnnotateConfig.store_raw_spectra` (was `store_filtered_spectra`). The four
`*_filtered_*` columns are dropped entirely, along with `AnnotationRow`'s
corresponding fields.

A new public function,
`spectral_match.normalize_and_filter_spectrum(mz, intensity,
noise_threshold)`, factors the exact per-spectrum preprocessing
`reverse_dot_product` already did inline into something callable on its
own; `reverse_dot_product` now calls it too, so there is exactly one
implementation of "what filtering means" shared between scoring and
reconstruction — they cannot drift apart.

`Plotter` reconstructs on demand: `_resolve_raw` resolves the untouched
spectrum for one side (preferring the stored column, falling back to a live
re-read — the raw per-sample database for "emp", the library file for
"lib" — only when the column is NULL), and `_resolve_side` calls
`normalize_and_filter_spectrum` on that result when the caller asked for
`"filtered"`. `Plotter._resolve_annotate_args` (renamed and extended from
`_resolve_fragment_ppm_tolerance`) parses the owning `annotate_ms2`
command's `arguments` JSON once and returns both `fragment_ppm` and
`noise_threshold`, each falling back to its own `AnnotateConfig` default
when the command or the key can't be found. Both "raw" and "filtered" now
fail the same way when no raw spectrum is available at all — one error
message, not a separate "filtered was never stored" case.

`Config.version` bumps 13 → 14 (a config field renamed) — no migration, per
the established precedent ([ADR 10](0010-score-weights-and-flat-fragmentation.md)):
existing config files must be re-exported.

## Consequences

- Every `ms2_annotations` row shrinks: two BLOB columns removed
  (`lib_filtered_mz`/`lib_filtered_intensity`) for one added
  (`emp_raw_mz`/`emp_raw_intensity` is new, but `emp_filtered_mz`/
  `emp_filtered_intensity` — the pair it replaces — is also removed), a net
  reduction of two BLOB columns per row, on top of the fact that a raw
  spectrum is usually no larger than its filtered copy (filtering only ever
  removes peaks).
- Existing analysis databases keep their old filtered/raw columns exactly
  as before — no migration touches them — but a fresh run against the same
  schema starts writing the new column set instead; there is no code path
  left that reads the old `*_filtered_*` columns, so **existing analyses
  must be re-run** to get mirror plots working again under the new schema
  (same "no migration, re-run needed" tradeoff as every prior schema change
  here). This is a bigger break than ADR 16's (which only left old rows
  with NULL in two *new* columns, degrading gracefully to a live re-read);
  here the columns the GUI reads no longer exist at all in an old-schema
  database, so a stale database must be re-run before its annotations can
  be viewed again at all — a harder break, but one considered acceptable
  because analysis databases are a derived artifact of the raw data, not
  the raw data itself (see [ADR 1](0001-raw-vs-analysis-db-split.md)) —
  re-running is always possible, and preferred over compatibility code that
  would need to keep both schema shapes readable indefinitely.
- If `AnnotateConfig` fields governing filtering (`noise_threshold`) or
  normalisation ever change meaning in a way that isn't backward
  compatible, every future reconstruction implicitly depends on
  `commands.arguments` continuing to carry an accurate historical record —
  the same dependency `fragment_ppm` resolution already carries, now
  doubled up rather than newly introduced.
