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
quantity as a feature) and uses the window only to count how many features could
have contributed. See [ADR 5](../developer/adr/0005-three-ppm-tolerances.md).

## Fragmentation failure

Sometimes fragmentation does not really happen — the MS2 spectrum is essentially
just the surviving precursor. That is a signal about the analyte, not a reason to
throw the scan away. MSIAnalyzer keeps every MS2 scan and flags the ones where
the base peak sits on the precursor and carries most of the intensity
(`precursor_only`). See [MS2 annotation](ms2-annotation.md).
