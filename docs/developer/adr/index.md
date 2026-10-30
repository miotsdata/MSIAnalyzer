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
| [13](0013-hybrid-plotting-approach.md) | Hybrid plotting: Plotly/WebEngineView + raster heatmaps | Accepted; GUI line plots superseded by 51 |
| [14](0014-analysis-workspace-lazy-loading.md) | Analysis workspace: lazy `Loader` sections + blocking loading overlay | Accepted |
| [15](0015-professional-desktop-theme-direction.md) | Professional desktop-app theme direction | Accepted |
| [16](0016-store-raw-library-spectrum.md) | Persist the untouched library spectrum alongside the filtered one | Accepted; superseded by 18 |
| [17](0017-mirror-plot-background-worker.md) | Background thread for mirror-plot data resolution, not figure-building | Accepted; figure rule obsolete since 51 |
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
| [29](0029-compound-search-syntax.md) | Compound search syntax (Annotate + Visual Inspection) | Accepted; superseded by 35 |
| [30](0030-colorbar-legend.md) | Colorbar legend for the vmin/vmax color scale | Accepted |
| [31](0031-per-sample-progress-callback.md) | Per-sample progress callback for `process_samples` | Accepted |
| [32](0032-inspect-visually-cross-tab-handoff.md) | "Inspect visually": cross-tab handoff between Annotations and Visual Inspection | Accepted |
| [33](0033-annotations-table-partial-index.md) | Partial index for `ms2_annotations.rank_feature` | Accepted |
| [34](0034-async-annotation-feature-fetch.md) | Fetch the Annotations table / feature list on a background thread | Accepted |
| [35](0035-split-search-fields.md) | Split compound search into separate name and m/z fields | Accepted; supersedes 29 |
| [36](0036-fixed-layout-space-for-toggled-content.md) | Reserve fixed layout space instead of swapping visible siblings | Accepted |
| [37](0037-retroactive-schema-refresh-on-analysis-open.md) | Retroactively refresh an analysis DB's schema when its workspace opens | Accepted |
| [38](0038-adduct-cas-hmdb-capture.md) | Capture adduct/CAS/HMDB for MS2-annotated hits | Accepted |
| [39](0039-export-menu-annotation.md) | Export menu: Annotation export | Accepted |
| [40](0040-export-menu-integration.md) | Export menu: Integration export | Accepted |
| [41](0041-export-menu-image.md) | Export menu: Image export | Accepted |
| [42](0042-library-path-list-widget-and-cross-boundary-array-fix.md) | `library_path`: list+remove widget, and a Python→QML array bug | Accepted |
| [43](0043-fragmentation-factor-purity-score-rename.md) | `fragmentation_factor`/`purity_score` rename; flat-fragmentation scans excluded from library matching | Accepted |
| [44](0044-structured-markdown-field-help.md) | Structured Markdown field help, in a dedicated window | Accepted |
| [45](0045-he-image-coregistration-phase1.md) | H&E/brightfield image coregistration, Phase 1: raw-DB storage + landmark fit | Accepted |
| [46](0046-he-coregistration-phase2-gui-and-zoomableimage-fix.md) | H&E coregistration Phase 2: `CoregistrationWindow` GUI, and a `ZoomableImage` input bug found along the way | Accepted |
| [47](0047-he-coregistration-phase3-overlay-and-roi-on-he.md) | H&E coregistration Phase 3: warped-heatmap overlay, and drawing ROI on the H&E image | Accepted |
| [48](0048-spatial-correlation-morans-i.md) | Spatial correlation (Moran's I), computed via squidpy on the `.h5ad` | Accepted |
| [49](0049-imported-analyzed-h5ad-datasets.md) | Imported analyzed `.h5ad` datasets, linked to an analysis | Accepted |
| [50](0050-gui-responsiveness-no-h5ad-io-on-gui-thread.md) | GUI responsiveness: no `.h5ad` I/O on the GUI thread | Accepted |
| [51](0051-qt-graphs-instead-of-plotly-in-gui.md) | Qt Graphs instead of Plotly/WebEngine for the GUI's interactive plots | Accepted |
| [52](0052-windows-build-and-fast-startup.md) | Windows build (one cross-platform spec + Inno Setup), and a fast-starting GUI | Accepted |
| [53](0053-heatmap-scale-bar-and-pixel-size.md) | Heatmap scale bar and per-sample pixel size | Accepted |
| [54](0054-isotope-detection-before-ms2.md) | Isotope detection before MS2, isotopes taken out of the feature list | Accepted; image extraction superseded by 55 |
| [55](0055-isotopes-from-the-h5ad-matrices.md) | Isotopes detected from the `.h5ad` matrices, built before MS2 | Accepted; supersedes part of 54 |
| [56](0056-per-run-log-file-and-output-tab.md) | Per-run log file in the output folder, and an Output tab | Accepted |
| [57](0057-custom-categorical-colors.md) | Custom colors for categorical obs columns, and showing only some categories | Accepted |
| [58](0058-roi-select-move-copy-paste.md) | Select, move, copy and paste ROIs in ROI Design | Accepted |
| [59](0059-portable-project-paths.md) | Portable project paths: stored relative, resolved against the opened project | Accepted |
| [60](0060-target-list-after-the-run.md) | Target list after the run: preview, choose per row, apply, undo | Accepted |
| [61](0061-normalize-to-a-feature-after-the-run.md) | Normalize to a feature after the run, as a new layer | Accepted |
| [62](0062-ms2-annotation-outcome.md) | Why a feature got no library hit: MS2 annotation outcome per feature | Accepted |
| [63](0063-reannotate-a-finished-analysis.md) | Re-annotate a finished analysis, with every annotation kept in a history | Accepted |
| [64](0064-ms2-cleaning.md) | MS2 cleaning: background fragments (counted per precursor) and fragments above the precursor | Accepted |
| [65](0065-precursor-mz-annotation.md) | Annotation by precursor m/z alone, every candidate kept and ranked by ppm | Accepted |
| [66](0066-feature-correlation.md) | Feature correlation: every feature pair per sample, stored in `varp`, its own tab | Accepted |
