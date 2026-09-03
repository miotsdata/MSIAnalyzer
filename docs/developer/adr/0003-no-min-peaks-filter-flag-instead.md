# 3 — No min-peaks filter for MS2 — flag instead

**Status:** Accepted (supersedes an earlier draft that specified a `min_n_peaks`
drop)

## Context

An early draft of the grouper design said: *"always drop MS2 with an
empty/near-empty fragment spectrum (a `min_n_peaks` filter)"*. On review this is
wrong for imaging work:

- Fragmentation failure is **information about the analyte** — it does not
  fragment well at this collision energy, is low-abundance, etc. Users explicitly
  want to see *"this m/z was fragmented N times, M of them with one fragment
  only"*.
- Raw peak count is a **poor proxy** for "failed": noise inflates it, and a truly
  failed MS2 is usually just the surviving precursor plus a few noise peaks.

## Decision

The grouper filters **nothing**. Every MS2 scan is associated and stored. Two
signals are recorded instead:

- **`precursor_only`** (per scan) — true when the base peak is within
  `precursor_only_mz_tol_da` of the precursor **and** at least
  `precursor_only_tic_frac` of the fragment TIC lies in that band. This is the
  real "fragmentation did not occur" indicator.
- **`n_peaks`** is kept as a weak secondary view; `feature_ms2_summary` exposes
  `n_single_peak` (scans with ≤ 1 peak) separately from `n_precursor_only`.

`feature_ms2_summary` rolls these to one row per feature (`n_ms2`, `n_samples`,
`n_precursor_only`, `n_single_peak`, `n_chimeric`, `median_n_peaks`),
materialised at grouper time.

## Alternatives considered

- **`min_n_peaks` drop** — loses the fragmentation-failure signal; rejected.
- **Live SQL view for the roll-up** — always fresh, but a materialised table is
  queryable by external tools without shipping the view definition, and the
  analysis DB is rebuilt per run anyway. Materialised table chosen.

## Consequences

- Downstream (Stage B) must decide what to do with `precursor_only` scans — the
  natural choice is to exclude them from a consensus spectrum while still
  counting them.
- `association` row count equals the MS2 scan count (minus unmatched only when
  `include_unmatched = false`).
