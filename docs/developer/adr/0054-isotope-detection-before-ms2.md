# 54 — Isotope detection before MS2, isotopes taken out of the feature list

**Status:** Accepted. Image extraction superseded by ADR 55.

## Context

Nothing recognized ¹³C isotope peaks. Every M+1 / M+2 of a real ion became
a feature of its own, so it:

- was searched in the libraries at its own precursor mass. Any hit was a
  compound about 1 Da heavier than the real one, which is wrong by
  construction;
- inflated the feature count, the `.h5ad` and Moran's I with features that
  carry no information of their own.

LCAnalyzer already links isotopes (`core/peaks/isotopes.py`). Its mass and
intensity rules carry over unchanged. Its co-elution test (same RT window,
correlated EIC shapes) does not, because MSI has no retention time.

## Decision

- **New stage `detect_isotopes`**, right after target-list matching and
  before `group_ms2`, so isotopes are never associated with MS2, annotated,
  put in a consensus, written to an `.h5ad`, normalized or scored by
  Moran's I. Untargeted runs only; a targeted run has no feature list.
- **Co-localization replaces co-elution.** The isotope's ion image must
  correlate with the monoisotopic one: Pearson r over the pixels where the
  mono is non-zero, at least `isotopes.min_spatial_corr`. Pixels where both
  are zero are left out, since otherwise any two on-tissue ions would
  correlate through the shared off-tissue background.
- **Images come from `create_spatial_adata`**, called per sample on the
  **candidate** features only: those with a partner at
  `mz + k × 1.0033548 / z` (k ≤ `max_isotopes`, z ≤ `max_charge`) within
  `isotopes.ppm`. It reuses `h5ad.integration_ppm`, `h5ad.scan_handling`
  and `h5ad.n_workers`, so the images match what the `.h5ad` will hold.
  This costs one extra pass over each sample's MS1 scans.
- **Per-sample linking is LCAnalyzer's algorithm.** Features are visited
  from the most intense down (mean image intensity in that sample); each
  one not yet claimed is a mono candidate. For each charge, M+1, M+2, … are
  collected while a feature exists that is within `ppm` of the expected
  m/z, less intense than the mono, for M+1 below the carbon bound
  `1.1 × 0.0107 × (mz·z/14) + 0.02`, and co-localized. The charge with the
  longest chain wins (ties: lower charge).
- **One decision per feature, by majority.** A link (mono, isotope, k, z)
  is accepted when it was found in more than half of the samples where both
  features are present. When an isotope wins links to several monos, the
  one with most votes is kept (ties: lower k). M+k is kept only if M+(k−1)
  of the same mono was. A feature accepted as an isotope can't be a mono.
- **Target-list features are never removed** (matched or injected, ADR 26):
  the user asked for them by name. They can still be monos.
- **Removed from the analysis, kept on record.** Isotope rows move from
  `features` to a new `feature_isotopes` table (same `feature_id`, `mz`,
  `members_json`, plus `mono_feature_id`, `isotope_index`, `charge`, the
  median intensity ratio and image correlation, and the vote counts).
  Every existing reader of `features` therefore sees monoisotopic features
  only, with no change. The summary report gets an "Isotopes" section.
- **Config:** a new `isotopes` section (`enabled`, `max_isotopes` = 3,
  `ppm` = 5, `max_charge` = 1, `min_spatial_corr` = 0.5). It's a new group
  with defaults, so existing config files still load: no version bump
  (as in ADR 20/21). `max_charge` defaults to 1 rather than LCAnalyzer's 2,
  because MALDI ions are almost always singly charged.
- **The GUI's config tabs now follow the pipeline order**, including the
  bespoke target-list tab (after "align") and the report (after the
  `.h5ad`, normalization and spatial-correlation steps it summarizes).

## Calibration of `min_spatial_corr`

Measured on run A's `1_Sez1_Tumor` `.h5ad` (DAN, negative mode, 3173
features): 507 feature pairs pass the mass and intensity rules for M+1, and
about 300 random pairs serve as a stand-in for unrelated ions.

| | median r | r ≥ 0.5 | r ≥ 0.7 |
|---|---|---|---|
| M+1 candidates, isotope in < 30 % of the mono's pixels | 0.16–0.41 | ~22 % | ~3 % |
| M+1 candidates, isotope in ≥ 30 % of the mono's pixels | 0.58–0.82 | ~74 % | ~48 % |
| random pairs | 0.05 | ~2 % | 0 % |

A weak isotope drops below detection in most pixels, which lowers r even
though it sits in the same place, so 0.7 missed most of them. 0.5 keeps the
random-pair rate at about 2 %. Two alternatives were tried and did no
better: the share of the isotope's pixels where the mono is present
(containment ≥ 0.9), and r over the pixels where both are present.
There's no ground truth here: some M+1 candidates are not real isotopes.

## Tighter M+1 bound than LCAnalyzer

LCAnalyzer allows an all-carbon molecule (`m/12` carbons) +30 % +0.05.
On run A this accepted M+1 links like 789.57 → 790.58 at a ratio of 0.94,
about 88 carbons: physically impossible for one compound, so another ion
overlaps the M+1 position, and removing that feature would delete a real
compound. Of 254 M+1 links, 17 implied more than `m/14` carbons and 10 more
than `m/12`; the median implied one carbon per ~20 Da, as expected for
lipids and metabolites. The bound is therefore one carbon per 14 Da (CH2
chains, the most carbon-dense common organic ions; DAN is 15.8), +10 % and
+0.02 for intensity noise. Unusually carbon-dense compounds (polyaromatics,
~12.6 Da per carbon) would have their isotopes kept as features.

## Alternatives

- **Flag only, keep isotopes as features** (as first proposed): rejected by
  the user. Annotating isotopes is wrong by construction, and filtering them
  out in every consumer would touch a dozen queries.
- **Delete isotopes outright:** a false link would be invisible, and the
  M+1/M ratio (a rough carbon count) would be lost. It's useful later as an
  annotation sanity check and for formula prediction.
- **Per sample before alignment**, as LCAnalyzer does: needs images
  before any feature list exists, so a pass over every peak, not just
  candidates. Mass and intensity rules alone give many false links in
  dense MSI spectra (matrix ions at the right mass).
- **After the `.h5ad`**, reusing its matrix: free images, but annotation
  has already run by then, and removing features would mean rewriting
  multi-GB `.h5ad` files.
- **Sharing LCAnalyzer's module:** about 80 lines, different data model
  (no RT, EIC or peak boundaries). A shared package isn't worth it, so the
  code is copied and adapted.

## Parked: MS2 veto

Comparing the MS2 of M and M+1 (shift-aware cosine on per-feature consensus
spectra) was measured on the October DAN data (1 Da isolation window), and
parked:

- Single-pixel MS2 scans are mostly noise. Usable consensus spectra (peaks
  recurring in ≥ 3 scans and ≥ 10 % of them) exist on both sides for 167 of
  379 M+1 links.
- Consensus spectra are dominated by peaks found in most features: 226.442
  (77 % of spectra, likely an instrument artifact), DAN [M−H]⁻ 157.077 and
  PO₃⁻ 78.959. Median scores: same mono split in two halves 0.93, M+1 links
  0.68, random pairs 0.34; with those peaks removed, random pairs drop to 0
  but half of the M+1 links do too (no specific fragments).
- A veto (both sides ≥ 4 specific fragments, score < 0.2) rejected 86 % of
  random pairs but could judge only 50 of 379 links, and reversed 3 of them.

Too little reach to justify the stage on this kind of data. Revisit if
single-pixel MS2 quality improves (e.g. summed or targeted MS2 acquisitions).

## Consequences

- One extra pass over the MS1 scans per sample, roughly 50–100 % of the
  `.h5ad` assembly time, depending on the number of candidates.
- An isotope whose mono fell below the MAD filter can't be recognized and
  stays a feature.
- Only ¹³C spacing is checked: ³⁴S, ³⁷Cl and ¹⁸O M+2 peaks differ by more
  than `ppm` from 2 × 1.00336 and are not linked.
- The feature count in the report and `.h5ad` drops; analyses run before
  this ADR need a re-run to get it.
- A wrongly removed feature can be inspected in `feature_isotopes`, and
  restored from its stored `members_json` if ever needed.
