# 5 — Three separate ppm tolerances

**Status:** Accepted

## Context

"How close is close enough" shows up three times in the pipeline and the numbers
were being conflated. They are different *kinds* of quantity and must not be
folded into one setting.

## Decision

Keep three tolerances, conceptually and in config:

| tolerance | between | units | source | typical |
|---|---|---|---|---|
| `align.align_ppm` | MS1 centroid ↔ MS1 centroid, across samples | ppm | config, used once in `align_mz_across_samples` | 5–10 ppm |
| `group_ms2.assoc_ppm` | MS2 `precursor_mz` ↔ a feature | ppm | config, used in the grouper | ≥ `align_ppm` |
| isolation window | the band the quadrupole physically transmitted | **Da** | read from the mzML | 0.4–4 Da (hundreds of ppm) |

- `align_ppm` decides **feature identity** and sets each feature's internal
  ±`align_ppm` width.
- `assoc_ppm` decides **match acceptance**. It must cover *both* the feature's
  own width *and* the extra error of a single survey-scan `precursor_mz`
  (noisier than an averaged consensus m/z, plus a possible small calibration
  offset). Hence `assoc_ppm ≥ align_ppm`; `group_ms2` logs a `UserWarning` when a
  caller passes it tighter. Default `assoc_ppm = 10.0` against `align_ppm = 5.0`.
- The **isolation window** is physics, in Da, from the file — used only to build
  the candidate set and the chimera count, never as the matcher. Matching on the
  window would be hundreds of ppm loose; matching on the rounded
  `isolation_window_target` would inherit the instrument's quantisation.

## Consequences

- Matching is on `precursor_mz`, the same physical quantity as a feature, so a
  tight ppm comparison is meaningful.
- `run.py` passes `align_ppm=config.align.align_ppm` into `run_grouper` purely so
  the warning can fire.
- Missing isolation offsets fall back to
  `group_ms2.default_isolation_half_width` (Da), not to a ppm value.
