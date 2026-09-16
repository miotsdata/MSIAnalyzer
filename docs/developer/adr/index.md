# Decision records

Architecture Decision Records capture a choice, the context that forced it, and
the consequences we accepted. They are immutable once accepted: a later reversal
gets its own record that supersedes the old one.

Format: **Status · Context · Decision · Consequences** (plus *Alternatives* where
useful).

| # | Title | Status |
|---|---|---|
| [1](0001-raw-vs-analysis-db-split.md) | Raw vs analysis database split | Accepted |
| [2](0002-ms2-feature-association-design.md) | MS2→feature association design | Accepted |
| [3](0003-no-min-peaks-filter-flag-instead.md) | No min-peaks filter for MS2 — flag instead | Accepted (supersedes an earlier draft) |
| [4](0004-two-table-association-storage.md) | Two-table storage for MS2 associations | Accepted |
| [5](0005-three-ppm-tolerances.md) | Three separate ppm tolerances | Accepted |
| [6](0006-schema-single-source-of-truth.md) | One schema builder per database | Accepted |
| [7](0007-library-annotation-design.md) | Library annotation design (Stage B) | Accepted |
| [8](0008-precursor-ion-purity.md) | Precursor ion purity (Stage A′) | Accepted; partially superseded by 19 |
| [9](0009-consume-purity-and-consensus.md) | Consume purity: annotation filter + per-feature consensus | Accepted; partially superseded by 19 |
| [10](0010-score-weights-and-flat-fragmentation.md) | Configurable score weights + flat-fragmentation flag | Accepted |
| [11](0011-run-progress-callback.md) | `Run` step-progress callback for GUI consumption | Accepted |
| [12](0012-tic-normalization.md) | TIC normalization + merged AnnData | Accepted |
| [13](0013-hybrid-plotting-approach.md) | Hybrid plotting: Plotly/WebEngineView + raster heatmaps | Accepted |
| [14](0014-analysis-workspace-lazy-loading.md) | Analysis workspace: lazy `Loader` sections + blocking loading overlay | Accepted |
| [15](0015-professional-desktop-theme-direction.md) | Professional desktop-app theme direction | Accepted |
| [16](0016-store-raw-library-spectrum.md) | Persist the untouched library spectrum alongside the filtered one | Accepted; superseded by 18 |
| [17](0017-mirror-plot-background-worker.md) | Background thread for mirror-plot data resolution, not figure-building | Accepted |
| [18](0018-reconstruct-filtered-spectra-on-demand.md) | Reconstruct filtered spectra on demand instead of storing them | Accepted |
| [19](0019-retire-feature-density-chimeric-flag-and-peak-based-purity.md) | Retire the feature-density chimeric flag and peak-based purity | Accepted |
| [20](0020-already-parsed-db-only-samples.md) | Already-parsed, db-only samples | Accepted |
| [20](0020-already-parsed-db-only-samples.md) | Already-parsed, db-only samples | Accepted |
| [21](0021-representative-compound-peak-count-tolerance.md) | Representative-compound selection: prefer more matched peaks within a score tolerance | Accepted |
| [22](0022-distribution-and-ci-strategy.md) | Distribution & CI strategy | Accepted |
| [23](0023-pyinstaller-appimage-packaging.md) | PyInstaller/AppImage packaging for the Linux standalone build | Accepted |
| [24](0024-single-instance-app-lock.md) | Single-instance app lock | Accepted |
| [25](0025-pin-plotly-below-7.md) | Pin plotly below 7 | Accepted |
| [26](0026-target-list-annotation.md) | Target-list compound annotation | Accepted |
| [27](0027-formula-prediction.md) | On-demand molecular-formula prediction | Accepted |
| [28](0028-gui-launch-directory-independence.md) | GUI launch-directory independence | Accepted |
| [29](0029-compound-search-syntax.md) | Compound search syntax (Annotate + Visual Inspection) | Accepted |
