# 19 — Retire the feature-density chimeric flag and peak-based purity

**Status:** Accepted

## Context

A user comparing two runs that differed only in `peak.filter_mad_nmads`
(0.5 vs 2.5) found the *looser* threshold — which correctly detected far
more features (3,173 vs 857, as expected: a lower MAD multiplier keeps more
low-intensity peaks) — produced dramatically *fewer* annotated features (35
vs 251) and distinct compounds (35 vs 248). Root-caused live, from the
actual databases:

1. More features packed into the same m/z range meant far more MS2
   isolation windows now physically overlapped 2+ aligned features:
   `n_features_in_window > 1` (the grouper's `is_chimeric` flag) fired on
   99.1% of associated scans in the loose-threshold run (403,273 of
   406,969), versus 91.1% in the tighter one.
2. The two runs' configs also differed on `annotate_chimeric`: `false` in
   the loose-threshold run, `true` in the other. With it `false`, every one
   of those now-chimeric-flagged scans was skipped by `annotate.py` before
   scoring even started — only the tiny sliver of *non*-chimeric scans
   (3,696 of 406,969) was ever eligible, and only 472 of those actually had
   a candidate. That number matches `n_scans_scored` exactly.

This is not a new bug — it's a known, previously documented design
limitation that was never fully addressed. [ADR 8](0008-precursor-ion-purity.md)
introduced `precursor_purity` specifically because `n_features_in_window`
"measures feature-grid density, not co-isolation" and "over-flags on large
acquisitions" — the exact failure mode this incident reproduced, just
triggered by a denser feature list instead of a larger sample count. That
ADR's consequences section explicitly deferred the fix: *"wiring the
grouper (and `ms2_annotations.is_chimeric`) to consume purity is left to a
later change."* [ADR 9](0009-consume-purity-and-consensus.md) then
considered and rejected redefining `is_chimeric` around purity, keeping
both signals — but never touched `annotate_chimeric`, so the flawed
feature-density flag remained the one actually gating whether a scan got
scored at all.

Separately, [ADR 8](0008-precursor-ion-purity.md)'s own "Follow-up (v8)"
note recorded that the peak-picking-based `purity` measurement (msPurity-
style: detect a peak in the isolation window, compare its intensity to the
window total, optionally interpolate across the parent + next MS1 scan)
failed to resolve the precursor as a discrete peak in **~56%** of scans on
real MALDI-imaging data, in dense, matrix-heavy, low-m/z windows — even
when the signal was plainly present by direct integration. That's why
`precursor_frac` (peak-detection-free profile-area integration) was added
as a fallback in the first place, and why `core/report/summary.py`
independently already treats `precursor_frac` as "the metric" for
reporting purity, `purity` only as a secondary, often-missing value.

Given both signals this codebase already leaned on for "is this scan
trustworthy" were each independently known to be unreliable — one by
design (a feature-list proxy, not a measurement), one empirically (fails
half the time on real data) — the fix is to stop gating scoring on either,
and consolidate around the one signal that's always computable and
genuinely scan-intrinsic.

## Decision

**Every MS2 scan associated with a feature is scored, unconditionally.**
`AnnotateConfig.annotate_chimeric` and the `is_chimeric`-based skip in
`annotate.annotate_feature`/`_annotate_feature_batch` are removed outright
— no config knob re-derives the old behavior. How many other aligned
features happen to share a scan's isolation window says nothing about that
scan's own spectrum and must never again decide whether it gets a chance
at a library match.

**The feature-density "chimeric" concept is removed, not just its use as a
gate.** `group_ms2.associate_scan` still restricts its candidate search to
features physically inside the isolation window before picking the
nearest one within `assoc_ppm` (matching behavior is unchanged — in every
realistic configuration `assoc_ppm`'s Da-equivalent width is far tighter
than a real isolation window, so this restriction was never the limiting
constraint on which feature wins), but no longer persists the count of
other candidates found there: `ScanAssociation.n_features_in_window`, the
`ms2_window_features` table, `FeatureMs2Summary.n_chimeric`, and
`ms2_annotations.is_chimeric`/`n_features_in_window` are all removed from
the schema and the Python types. Keeping the raw count around "just as
information" would keep the same misleading label in front of users that
caused this incident.

**The peak-picking-based `purity` measurement is retired.**
`precursor_purity.py` no longer computes `purity`, `n_peaks_in_window`,
`runner_up_rel_int`, or `purity_parent` — and with them, the entire
parent+next-MS1 raster-bracketing machinery that existed solely to
interpolate that value (`RasterGeometry`/`infer_raster_geometry`, the
`next_*` half of `resolve_parent_next` — renamed `resolve_parent`, parent
resolution only — `detect_window_peaks`, `score_window`,
`interpolate_purity`). `precursor_frac` (profile-area integration within
`precursor_confirm_ppm` of the recorded precursor m/z, relative to the
whole window) becomes the sole, always-computable "how clean was this
scan's precursor selection" signal, alongside `precursor_confirmed` and
the m/z-snap refinement — none of which ever depended on peak-picking or
raster geometry in the first place.

**Every downstream consumer of `purity` switches to `precursor_frac`.**
`ConsensusConfig.neutral_purity`/`min_purity` become
`neutral_precursor_frac`/`min_precursor_frac`; `consensus.purity_term`
becomes `precursor_frac_term`; `AnnotateConfig.min_purity` becomes
`min_precursor_frac`, gating on `precursor_frac` instead of `purity`. The
report's `purity_unscored`/`UnscoredSummary`/`figure_purity_unscored`
(built entirely around "was the peak-based value resolved, and why not")
are removed — there is no more "unscored" case worth classifying, since
`precursor_frac` fails to compute only when there's no parent MS1 at all.
The confidence-funnel's `n_confident_not_chimeric` stat is removed from
the report for the same reason `is_chimeric` itself is gone.

`PurityConfig` sheds every field that existed only for peak-picking or
raster interpolation: `ppm_precursor_match`, `min_rel_intensity`,
`merge_ppm`, `use_next_ms1`, `max_interpixel_gap_sec`. What remains:
`enabled`, `default_half_window_da`, `precursor_confirm_ppm`,
`precursor_confirm_min_frac`, `precursor_snap_ppm`, `n_workers`.

`Config.version` bumps 14 → 15 — a schema and config-field change, no
migration, matching every prior precedent in this project (most recently
[ADR 18](0018-reconstruct-filtered-spectra-on-demand.md)).

## Alternatives considered

- **Keep `annotate_chimeric` but default it to a no-op / always-True.**
  Rejected — the config field's mere existence invites exactly the
  configuration this incident hit; removing it is the only way to
  guarantee it can't happen again.
- **Keep `purity`/peak-picking as a secondary, optional signal instead of
  removing it.** Considered, but a ~56% failure rate on real data makes it
  a worse foundation than `precursor_frac` even where it *does* resolve, so
  maintaining two parallel "purity" concepts (one working half the time)
  bought no real analytical capability against the ongoing cost of keeping
  the raster-bracketing machinery correct. `precursor_frac` also already
  measures the same physical quantity (how much of the window's ion
  current is the precursor) without needing a resolved peak at all.
- **Redefine `is_chimeric` as a new frozen threshold on `precursor_frac`.**
  Rejected for the same reason [ADR 9](0009-consume-purity-and-consensus.md)
  gave for not doing this with `purity`: it freezes a verdict into the
  table instead of storing a measurement a query can threshold however it
  likes. `precursor_frac` is that measurement.

## Consequences

- **Existing analysis databases must be re-run.** The removed
  `ms2_annotations`/`ms2_associations`/`feature_ms2_summary`/
  `precursor_purity`/`feature_ms2_consensus` columns and the
  `ms2_window_features` table mean old-schema databases no longer match
  what this code reads or writes at all — same category of break as
  [ADR 18](0018-reconstruct-filtered-spectra-on-demand.md), not a
  gracefully-degrading one.
- Re-running `annotate` on data whose feature list is dense enough to make
  most scans "chimeric" under the old definition will now score
  dramatically more scans than before — by design; this was the actual
  bug. Runs relying on `annotate_chimeric=False` to hold down annotation
  volume must use `min_precursor_frac` instead, which filters on a
  meaningful per-scan signal rather than an accidental one.
- `precursor_purity.py` is substantially smaller: no local peak-picker, no
  raster-geometry inference, no parent+next bracketing. Precursor purity
  scoring for one sample now only ever touches its own parent MS1 scans.
- `group_ms2.persist_grouping` no longer writes `ms2_window_features` at
  all — one fewer table write per scan, and `associate_scan` needs no
  `dataclasses.replace` bookkeeping over a candidate list.
- No GUI code referenced any of the removed fields (`is_chimeric`,
  `n_features_in_window`, `purity`, `runner_up_rel_int`) — this is a
  core-only change. Surfacing `precursor_frac` to a user per-annotation
  (e.g. a mirror-plot badge) is worthwhile future GUI work, not yet built.

**Note (2026-09-17):** `precursor_frac`, the metric this ADR introduces
as the feature-density flag's replacement, was later renamed
`purity_score` — see [ADR 43](0043-fragmentation-factor-purity-score-rename.md).
The reasoning above is unaffected; only the field/column name changed.
