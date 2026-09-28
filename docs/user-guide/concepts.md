# Concepts

## Mass-spectrometry imaging

An MSI acquisition rasters a sample: the instrument fires at a grid of spatial
positions (**pixels**) and records a mass spectrum at each. MSIAnalyzer targets
AP-MALDI-style acquisitions where pixel identity comes from a **raster timing
file** (XML): each pixel owns a time window `[t_start, t_end]`, and every scan
whose retention time falls inside that window belongs to that pixel.

## Scans: MS1 and MS2

- **MS1 scan** — a full-range survey spectrum. Its peaks are intact ions
  (precursors). MS1 is what carries spatial/quantitative information: "how much
  of m/z X is at this pixel".
- **MS2 scan** — the instrument selected one precursor, fragmented it, and
  recorded the fragments. MS2 is what carries **structural** information used for
  identification.

Each scan stored by the parser has a retention time, a polarity, a peak list
(`m/z` + intensity arrays), and a TIC (total ion current). MS2 scans also carry
precursor information (see below).

## Profile spectra, peaks and centroids

A raw spectrum is a **profile**: a quasi-continuous trace. A real ion shows up as
a bump several points wide. **Centroiding** collapses each bump to a single
`(m/z, intensity)` **peak**. MSIAnalyzer centroids the *averaged* MS1 spectrum of
a sample, then filters the peak list by intensity to drop noise.

## ppm

Mass tolerances are expressed in **parts per million** because absolute mass
error scales with m/z:

```
Δppm = (m/z_a − m/z_b) / m/z_b × 1 000 000
```

At m/z 500, 10 ppm ≈ 0.005 Da. A ppm window is therefore *narrow* — much
narrower than an isolation window (below).

## Features (the master m/z list)

Run the pipeline over several samples and each produces its own peak list. Peaks
that represent the *same ion* land at slightly different m/z in each sample
(calibration drift, noise). **Alignment** clusters peaks across samples within a
ppm tolerance and represents each cluster by one consensus m/z — a **feature**.

The feature list is the backbone of an analysis: images are quantified at feature
m/z, and MS2 scans are associated to features.

## Precursor m/z vs isolation window

When the instrument picks an MS2 precursor it records two different kinds of
number:

| field | meaning | kind |
|---|---|---|
| `precursor_mz` | the m/z it *measured* for the selected ion in the survey scan | a measurement |
| `isolation_window_target` | the m/z the quadrupole was *told* to centre on | an instruction (often rounded) |
| `isolation_window_lower` / `_upper` | Da offsets defining the band actually transmitted: `[target − lower, target + upper]` | physical, 0.4–4 Da wide |

The window is **hundreds of ppm wide** — far wider than a ppm match tolerance. So
the quadrupole often transmits, and co-fragments, *several* MS1 ions at once: an
MS2 scan can be **chimeric**. MSIAnalyzer matches on `precursor_mz` (same kind of
quantity as a feature), narrowing its search to features physically inside that
window before picking the nearest one. See
[ADR 5](../developer/adr/0005-three-ppm-tolerances.md).

How many features happen to fall inside that window is *not* a reliable
chimericity signal — it's a property of how dense the analysis-wide feature
list is, not of what actually co-fragmented into any one scan (see
[ADR 19](../developer/adr/0019-retire-feature-density-chimeric-flag-and-peak-based-purity.md)).
The **precursor purity** stage
([ADR 8](../developer/adr/0008-precursor-ion-purity.md)) instead measures how
much of the ion current in the window actually belonged to the precursor,
reading the scan's own parent MS1 — a chimericity signal that does not depend on
how many samples the analysis spans, or how many features it detected.

## Fragmentation failure

Sometimes fragmentation does not really happen — the MS2 spectrum is essentially
just the surviving precursor. That is a signal about the analyte, not a reason to
throw the scan away. MSIAnalyzer keeps every MS2 scan and flags the ones where
the base peak sits on the precursor and carries most of the intensity
(`precursor_only`). See [MS2 annotation](ms2-annotation.md).

## Spatial autocorrelation (Moran's I)

A feature's per-pixel intensities can either form a real spatial pattern (e.g.
concentrated where the tissue is) or look basically like noise scattered at
random. **Moran's I** is a score that tells the two apart by comparing each
pixel's value to its neighbors':

- Close to **+1** — neighboring pixels have similar values: the feature is
  spatially organized.
- Close to **0** — no relationship between neighbors: looks like noise.
- Close to **−1** — neighbors tend to have *opposite* values (checkerboard-like):
  unusual in real MSI data.

MSIAnalyzer computes one Moran's I **per feature, per sample** (never pooled
across samples — a metabolite organized in one slide isn't assumed to be
organized in another), via the opt-in `spatial_correlation` pipeline stage
(see [Workflow](workflow.md)'s "Spatial correlation" stage). Browse every
feature's score per sample in the **Spatial Correlation** tab, or see the
currently-selected feature's score next to each sample's name in **Visual
Inspection**.

### Local Moran's I — *where*, not just *whether*

The score above is **global**: one number for the whole sample. If a sample's
image contains both bare matrix and actual tissue (a common setup), a high
global score might just mean "the tissue and the matrix look different" — it
doesn't say whether the signal is *also* organized *within* the tissue itself.

**Local Moran's I** answers that. Instead of one score per sample, it's one
score *per pixel*, comparing each pixel only to its own immediate neighbors.
Select **Local Moran's I** as a layer in Visual Inspection to see it as a
heatmap: a sharp band right at a matrix/tissue boundary is a visibly different
pattern from scattered clusters *inside* the tissue, letting you tell coarse
matrix-vs-tissue structure apart from genuine within-tissue organization. It's
computed on the fly the moment you select it — not stored on disk, and it
doesn't require the `spatial_correlation` pipeline stage to have been run at
all.

Local Moran's I is **not** bounded to −1–1 the way the global score is — a
pixel and its neighborhood sitting about one standard deviation from the
sample's own mean, together, scores around **±1**; a stronger, more unusual
local concentration scores higher still. There's no single "high" value that
applies everywhere — look at a sample's own color scale (it's centered on 0,
so a pixel's color already tells you roughly how extreme it is) before
picking a number to compare against.

If you'd rather see "which feature has the *most* high-value pixels" as a
sortable table instead of eyeballing each feature's map one at a time, the
opt-in `spatial_correlation.local_enabled` pipeline pass computes every
feature's full per-pixel local Moran's I once (this is the only place
local Moran's I is ever written to disk — the Visual Inspection layer
above is always computed live) — the **Spatial Correlation** tab then
turns that into a "% of pixels above a threshold" table, sortable by any
sample's column. Crucially, the threshold itself is a **live GUI control**,
not baked into the pipeline run: type a different number into the table's
threshold field and every percentage updates instantly from the same
already-loaded data, no re-run needed. `local_threshold` in the config
only sets what number that field starts at when the page first opens —
if your first guess turns out to be too high or too low (e.g. showing 0
features above it), just change it right there.

See [ADR 48](../developer/adr/0048-spatial-correlation-morans-i.md) for the
full computation details (both scores).
