# User guide

MSIAnalyzer processes mass-spectrometry imaging acquisitions into feature tables
and spatial images, ready for annotation and downstream analysis.

## Read in order

1. **[Concepts](concepts.md)** — the vocabulary: pixels, MS1 vs MS2, peaks,
   features, ppm, isolation windows. Everything else assumes these.
2. **[The workflow](workflow.md)** — the pipeline step by step: what each stage
   consumes, what it produces, and which knobs affect it.
3. **[Projects & configuration](projects-and-config.md)** — the project folder,
   the config file, and every setting explained.
4. **[Outputs](outputs.md)** — the files and database tables a run produces and
   how to read them.
5. **[MS2 annotation](ms2-annotation.md)** — how MS2 scans are tied to features,
   the quality flags, and how to interpret them.

## Guiding principles

- **Parse once, analyse many times.** The raw database for a sample is written
  once and never modified. You can run several analyses (different bin widths,
  tolerances, filters) over the same parsed files without re-parsing.
- **Every derived number is reproducible.** Each analysis is one self-contained
  database whose `commands` table records the exact parameters of every step.
- **Nothing is silently dropped.** Where the pipeline could discard data
  (e.g. an MS2 scan that did not fragment), it keeps it and records a flag
  instead, so the counts stay visible.
