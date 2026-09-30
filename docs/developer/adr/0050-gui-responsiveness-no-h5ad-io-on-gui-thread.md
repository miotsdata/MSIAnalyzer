# 50 — GUI responsiveness: no `.h5ad` I/O on the GUI thread

**Status:** Accepted

## Context

On a real 16-sample project, the user saw "python3 is not responding"
while switching features or datasets in Visual Inspection, and when an
import finished. A freeze watchdog (below) captured six freezes of
4.5–14 s. Every one was work on the GUI thread:

- **Heatmap tiles rendered synchronously.** `ZoomableImage` used
  `asynchronous: false`. A cold tile was a full `ad.read_h5ad`, which
  loads X plus the `raw` and `TIC` layers and all of obs, taking
  0.35–1.9 s per sample. The render itself took 3–30 ms.
- **The sample cache thrashed.** It held 4 AnnData objects, but the grid
  shows every sample. So *every* feature switch re-read all 16 files,
  freezing for 5.5 s.
- **Six to eight background threads read the same files at once** (stale
  obs-column and value-range queries, prefetch, the Moran table, the
  feature list), all doing full or `backed="r"` reads. `backed` still
  decodes all of obs. These slowed the GUI thread's own reads further.
- **An import only *looked* frozen.** When it finished, the dialog
  switched to the new dataset before closing. That switch was a 12.5 s
  synchronous re-read of all 16 samples.
- **Opening an analysis took 6.8 s**: the Summary counts
  (`feature_compound_scores` is a view aggregating over every MS2
  annotation, about 2.6 GB of analysis DB) ran on the GUI thread.

A measurement showed that h5py releases the GIL during reads. So
background threads are enough; this doesn't need separate processes.

## Decision

- **Freeze watchdog, kept permanently (`gui/utils/freeze_watchdog.py`).**
  A GUI-thread `QTimer` re-arms `faulthandler.dump_traceback_later(3 s)`.
  A stall of 3 s or more dumps every thread's stack to
  `src/msianalyzer/log/freeze_<ts>.txt`, and stalls of 1 s or more are
  logged. Tile load and render times are logged too.
- **Heatmap and H&E-overlay providers are `QQuickAsyncImageProvider`s**
  (`gui/utils/async_image.py`). `requestImageResponse` only queues a job
  on a **single-thread** `QThreadPool`.
  - Renders stay strictly serial, which was the property
    `asynchronous: false` protected (concurrent Python renders once hung),
    but they never run on the GUI thread.
  - A response Qt cancels skips its render and still emits `finished`.
  - Responses are kept alive until Qt's own `destroyed` fires. Dropping
    them when `finished` is emitted is a use-after-free, because Qt still
    calls `textureFactory()` after that; a test segfaulted on exactly this.
- **The per-sample cache holds `LightSample`s
  (`core/plotting/light_sample.py`), not full AnnData objects.** Each is a
  real `AnnData` holding only:
  - `var`, `obsm["spatial"]` and the grid axes;
  - **only the viewed layer, as CSC**: a column slice takes 0.5 ms
    instead of 30–150 ms;
  - obs columns loaded one at a time when overlaid.

  Every existing render function (feature, obs, categories, local
  Moran's I, H&E warp) works on it unchanged, pixel-identical by test.
  The cache is bounded by **memory** (25 % of RAM) instead of 4 entries.
  The render thread, query workers and prefetch share it under one
  re-entrant lock.
- **Prefetch warms every inspection sample** with the layer the controls
  will default to. It no longer pre-renders, since a render is now about
  10 ms.
- **Tiles show a busy indicator** while they load.
- **Summary counts load in the background** (`requestSummary` →
  `summaryReady`), cached per analysis and gated by the loading overlay.
- **Element-level `.h5ad` edits (`core/utils/h5ad_io.py`).**
  - ROI save and delete write only the ROI's obs column (and
    `column-order`) plus `uns/rois`. This applies to sample files, the
    `merged.h5ad` sync, and the import source write-back (ADR 49).
  - The polygon mask needs only the coordinates.
  - `load_sample_rois`, the spatial-correlation and local-Moran loaders,
    and the imported-dataset feature list read single elements (`var`,
    `uns/...`, `layers/...`) instead of opening an AnnData.

## Rules going forward

1. No `.h5ad` I/O, and no query that can take more than about 100 ms,
   runs on the GUI thread. Use a worker plus a `*Ready` signal, or the
   async providers.
2. Metadata reads go through h5py elements (`h5ad_io`, `read_elem`), not
   `ad.read_h5ad(backed="r")`.
3. Heatmap data goes through `HeatmapImageProvider`'s lock and cache;
   don't add parallel readers of the same files.
4. A new freeze shows up as a `freeze_*.txt` file: read it before guessing.

## Consequences

- Calibration's synchronous reads (`addCalibrationPoint`/`Unknown`,
  targeted analyses) are left as they are. Those files hold only a few
  features.
- Running the import in a separate process (spawn) is **deferred**. The
  import freeze turned out to be the post-import dataset switch. It will
  be revisited if the watchdog catches a freeze during an import itself.
