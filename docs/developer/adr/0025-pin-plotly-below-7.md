# 25 — Pin plotly below 7

**Status:** Accepted

## Context

`plotly>=6.8.0` had no upper bound. A `pdm lock` run for an unrelated
change (the [ADR 22](0022-distribution-and-ci-strategy.md) core/`[gui]`
extras split) silently resolved it to 7.0.0 instead of preserving the
existing 6.8.0 pin — PDM's default lock strategy does a fresh resolution,
not a reuse-current-pins one, so any unbounded constraint is one `pdm
lock` away from a surprise major bump. That bump landed in the single
lockfile every install path shares, and broke MS1's spectrum view and the
mirror-plot detail window — including running `msianalyzer-gui` straight
from source, nothing to do with the PyInstaller packaging work happening
in parallel that was initially (reasonably) suspected instead.

Root-caused directly, not guessed: `plotly-python` 7.0.0 bundles
`plotly.js` **v4.0.0**; the 6.x line bundles `plotly.js` **v3.7.0**. That's
a major rewrite of the underlying JS charting engine itself, not just the
Python API surface — exactly the kind of change that silently breaks
custom JS event-wiring (MS1's point-click handler, mirror-plot
interactivity) embedded via `fig.to_html(...)` into a `WebEngineView`,
without raising any Python-visible error.

## Decision

`plotly>=6.8.0,<7`. Relocked to 6.9.0 (plotly.js v3.7.0), matching what was
already working.

No attempt was made to migrate to plotly 7.x / plotly.js v4 — it had
shipped too recently to have any specific feature this app needs, and a
major JS-engine rewrite breaking custom embedded event handling is exactly
the kind of change that needs a deliberate migration with real UI testing,
not a version-bump-and-see.

## Alternatives considered

- **Upgrade to 7.x and fix whatever broke.** Rejected for now — no feature
  need pulls toward 7.x, and the actual JS-level breakage (which specific
  API changed under the custom event wiring) was never diagnosed, only
  confirmed to exist. Worth revisiting deliberately if a real 7.x feature
  becomes needed.
- **Pin to an exact version** (`==6.9.0`) instead of a range. Rejected —
  over-tight for a well-established major line with no known cross-patch
  breakage; `<7` captures the actual risk (major version bumps) without
  blocking routine 6.x patch updates.

## Consequences

- Every other dependency with an unbounded upper constraint
  (`anndata>=0.13.2`, `libviz>=0.1.3`, `matplotlib>=3.11.1`,
  `tomli-w>=1.2.0`, `kaleido>=1.3.0`) carries the same latent risk — a
  future `pdm lock` (for any reason, not necessarily touching these
  packages) can silently jump any of them to an untested major version.
  Not audited/fixed in this pass.
- A future deliberate plotly 7.x migration needs to re-verify MS1's
  point-click handler and the mirror-plot detail view specifically — the
  two integrations that broke silently here, and the ones most likely to
  depend on plotly.js internals that changed between v3 and v4.
