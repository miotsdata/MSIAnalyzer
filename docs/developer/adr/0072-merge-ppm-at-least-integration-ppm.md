# 72 — `centroid.merge_ppm` must be at least `h5ad.integration_ppm`

**Status:** Accepted

## Context

In the DTX run (merge 5 ppm, align 10 ppm, integration 10 ppm) lactate
(m/z 89.0244) came with two Orbitrap side lobes, ~0.3 % of its height,
10 ppm either side (89.0235, 89.0253). Centroiding kept them (10 > 5 ppm)
and alignment could not merge them, because each sample contributes at most
one peak per feature. They became features 43 and 45.

Per-pixel integration takes the **maximum** intensity within
±`integration_ppm` of the feature m/z (`process_spectrum_batch`). When two
features of one sample are closer than that window, each window holds the
other's peak, so the weaker feature mostly reports the stronger one's
intensity: a duplicate image that inflates statistics and correlations.

## Decision

- `Config.__init__` raises `ValueError` when
  `centroid.merge_ppm < h5ad.integration_ppm` for an untargeted run.
  Equality is allowed (defaults are both 5). Targeted runs are exempt: they
  don't centroid and build their `.h5ad` with `H5adConfig()` defaults.
- The check lives in the config, not as a dynamic bound on the form field:
  the New Analysis form is generated from the dataclasses and has no
  cross-field bounds, and a config check covers the CLI too. The GUI shows
  the message through the existing `invalidConfig` → error dialog path.
- A margin above integration (e.g. 12 for 10) is recommended in the help,
  not enforced — any fixed margin would be arbitrary.
- No config version bump: no field changed. Old configs that break the rule
  are refused only when re-run (`Config.from_dict`/`from_yaml`); opening an
  analysis, re-annotation and the saved run config read the YAML as a plain
  dict and are unaffected.
- Help corrected alongside: integration takes the maximum, not the sum;
  `align_ppm` is "about integration_ppm", not "looser than merge_ppm".

## Consequences

- Merging keeps the most intense peak and drops weaker ones within
  `merge_ppm` regardless of their intensity ratio, so a real weaker ion that
  close is lost — but it could not be quantified separately anyway.
- Side-lobe spacing grows with m/z; a fixed ppm may not catch satellites at
  high m/z. An intensity-ratio-aware merge would be the next step if needed.
