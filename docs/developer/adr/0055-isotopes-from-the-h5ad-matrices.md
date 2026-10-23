# 55 — Isotopes detected from the `.h5ad` matrices, built before MS2

**Status:** Accepted. Supersedes the "Images come from `create_spatial_adata`,
called per sample on the candidate features" decision of ADR 54.

## Context

ADR 54 extracted ion images for the candidate features with a separate
`create_spatial_adata` pass, before MS2, and built the `.h5ad` files again
after the consensus. On the October DAN data the isotope step took 3 min and
the `.h5ad` build about 4 min, out of about 11. Both are dominated by reading
and decoding every MS1 scan, not by the number of m/z extracted: the same
work was done twice.

The `.h5ad` doesn't depend on any MS2 stage. Its columns are the aligned
features, the injected target-list features and the normalization reference
m/z, all known after target-list matching. Annotations and consensus picks
live in the analysis database; normalization and Moran's I come after and
only add a layer and `uns` entries.

## Decision

- **New order:** align → target list → `assemble_adata` (every feature) →
  `detect_isotopes` → group MS2 → purity → annotate → consensus →
  normalization → Moran's I → report.
- **Isotope images are the `.h5ad` matrices.** Each sample's file is read,
  its candidate columns are linked (ADR 54's rules), and the cross-sample
  vote runs as before.
- **Isotope columns are then removed from every `.h5ad`**: each file is
  read, subset and written to a temporary file that replaces the original
  (`os.replace`), so a crash never leaves a half-written file. Files without
  isotope columns aren't rewritten. One sample is in memory at a time.
- **The normalization reference m/z is never dropped**, even if a feature at
  that m/z is an isotope, because normalization needs that column.
- Re-running an analysis whose `.h5ad` files exist but whose isotope step
  didn't run takes the same path: the step reads the existing files.

## Alternatives

- **Keep all samples' matrices in memory until the vote, write once:**
  fastest, but about 8 GB of RAM for the October data. Too much for a
  16 GB laptop.
- **Keep the isotope columns, flagged in `var`:** no rewrite, but
  normalization, Moran's I and anyone opening the file would have to filter
  them, which goes against "isotopes are removed" (ADR 54).

## Consequences

- The isotope step costs two reads of each `.h5ad` and one rewrite of those
  holding isotope columns, instead of a full pass over the raw MS1 scans.
- The GUI's progress list shows `assemble_adata` before `detect_isotopes`.
- `feature_isotopes` is unchanged; the isotope images can no longer be
  opened from the `.h5ad`. A future "show isotope image" would extract them
  from the raw DB on demand.
