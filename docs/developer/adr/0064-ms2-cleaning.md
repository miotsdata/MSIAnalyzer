# 64 — MS2 cleaning: background fragments and fragments above the precursor

**Status:** Accepted

## Context

Many MS2 spectra are dirty but rich in peaks: the same contaminant or
chemical-noise fragments show up whatever was fragmented, and some spectra
have peaks above the precursor m/z, which a singly charged ion can't
produce. Both hurt annotation (emp coverage, flat-fragmentation, dot
product).

## Decision

- **A new config group, `ms2_cleaning`** (config v23), in two parts: background
  fragments (`background_enabled`, `background_ppm`,
  `background_min_percent`) and fragments above the precursor
  (`above_precursor_enabled`, `above_precursor_ppm`). A field can now name
  a `section` (dataclass field metadata); the GUI forms show a heading
  there.
- **Raw data is never changed.** Spectra are cleaned when read, by one
  function (`ms2_cleaning.Ms2Cleaner`): in grouping (so flat-fragmentation,
  fragmentation factor and `n_peaks` — and so consensus — see the cleaned
  spectrum), in the annotation worker, and in the plotter's "filtered"
  view. `ms2_annotations.emp_raw_*` stay the spectrum as acquired, so the
  "raw" mirror plot still shows what was removed. The best-scan plot of an
  unannotated feature stays raw, as decided for that view.
- **Counted per precursor, not per scan.** An imaging run fragments one
  ion in hundreds of pixels; per scan, its own real fragments would look
  like background. A precursor is the feature nearest to a scan's
  precursor m/z within `assoc_ppm`, or for an unmatched scan its precursor
  m/z binned to `assoc_ppm`; it has a fragment if any of its scans does.
  Removal threshold: a percentage of the polarity's precursors.
- **Clustering** is vectorised: all fragments sorted, split where
  neighbours are more than `background_ppm` apart, and clusters wider than
  twice that cut, so a dense run of noise peaks can't merge a whole m/z
  range. 20 M peaks take about 4 s; reading the raw databases (threads,
  one per sample) is the larger cost, so no process pool.
- **The table is always computed**; `background_enabled` only decides
  removal. `ms2_background_fragments` lists clusters in at least 5 % of
  precursors (or removed), shown on the Summary page; `ms2_cleaning` holds
  the one settings row the stored spectra are cleaned with, so later
  readers clean the same way without the run config.
- **Above the precursor** uses the matched feature's m/z (the scan's
  `precursor_mz` can be the coarse isolation centre), and skips scans with a
  precursor charge of 2 or more. The precursor's own isotope peaks are
  removed too, which is harmless.
- **Part of re-annotation**: `ms2_cleaning` is an `MS2_GROUPS` group and
  both tables are in `MS2_TABLES`, so tuning needs no re-run and older
  analyses can be cleaned. A snapshot taken before the tables existed
  restores them empty.

## Consequences

- Real fragments shared by a compound class (m/z 184.07 of phosphocholine
  lipids, fatty-acid anions in negative mode) can pass a low threshold;
  the default is 50 % and the Summary list lets you check first.
- The plotter doesn't know a scan's charge, so its filtered view of a 2+
  precursor hides peaks above it that scoring kept.
