# MSIAnalyzer

MSIAnalyzer is a pipeline for **mass-spectrometry imaging (MSI)** data. It turns
raw instrument output (mzML + a raster timing file) into:

- a reusable, immutable **raw database** per sample (every scan, plus the pixel
  grid), and
- a per-analysis database holding every parameter-dependent result: averaged
  MS1 spectra, detected peaks, the cross-sample feature list, MS2→feature
  associations, and (later) library annotations,
- one spatial `AnnData` (`.h5ad`) per sample for downstream imaging analysis.

## Where to look



-  **[User guide](user-guide/index.md)**

    What the pipeline does, the concepts behind it, how to configure a run and
    read its outputs. Start here if you *use* MSIAnalyzer.

-  **[Developer docs](developer/developer.md)**

    Module map, database schemas, the reasoning behind the design
    ([decision records](developer/adr/index.md)), testing approach and the API
    reference. Start here if you want to *contribute* to MSIAnalyzer.


## The pipeline at a glance

```
mzML + raster XML
      │  parse            ─────────────►  <sample>.db      (raw, immutable)
      │  map pixels        ─────────────►  <sample>.db      (pixel geometry)
      ▼
  average MS1  →  detect centroids  →  filter peaks   ────►  analysis_<id>.db
      ▼
  align m/z across samples           ────────────────────►  analysis_<id>.db  (features)
      ▼
  associate MS2 scans → features     ────────────────────►  analysis_<id>.db  (grouper)
      ▼
  build spatial AnnData per sample   ────────────────────►  <sample>.h5ad
```

Two databases, one rule: the **raw DB holds objective facts about the
acquisition**; the **analysis DB holds everything that depends on a parameter
choice**. Raw databases are parsed once and live at the **project** level
(`<project_folder>/parsed/` by default), shared by every analysis; each
analysis' `out_dir` holds only its own results. See
[ADR 1](developer/adr/0001-raw-vs-analysis-db-split.md).
