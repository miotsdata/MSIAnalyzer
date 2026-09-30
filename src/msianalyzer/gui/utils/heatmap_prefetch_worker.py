from __future__ import annotations

import logging

from PySide6.QtCore import QThread, Signal

from msianalyzer.gui.utils.heatmap_provider import HeatmapImageProvider

logger = logging.getLogger(__name__)


class HeatmapPrefetchWorker(QThread):
    """Loads one or more samples' `.h5ad` into `HeatmapImageProvider`'s
    cache, and (when `default_mz` is given) also pre-renders each
    sample's default tile at that feature — all on a background thread,
    ahead of actually switching to the Visual Inspection tab.

    Why this exists: `ZoomableImage.qml`'s `Image` is deliberately
    `asynchronous: false` (its own comment explains a real, reproduced
    hang with `asynchronous: true` and 2+ concurrent tiles — likely
    matplotlib global state under concurrent access). That's still
    respected here — see the concurrency note below — but it means the
    very first tile requested for a sample runs both
    `HeatmapImageProvider._load_adata`'s `ad.read_h5ad()` **and** the
    actual matplotlib render (`_render_target`) synchronously on the GUI
    thread the moment `sectionStack.currentIndex` flips to Visual
    Inspection: a real, user-visible freeze on a cold cache, reported
    after the "Show spatial distribution" handoff (but the same cost on
    a plain first-time nav-button click too). Doing the same *read and
    render* on a background thread first, into `HeatmapImageProvider`'s
    two existing caches, means the GUI-thread cost once the tab actually
    activates is just two cache hits — no disk I/O, no matplotlib call.

    `default_mz` is the feature Visual Inspection will land on by
    default (`analysis_db.load_feature_list`'s first row — lowest m/z,
    matching `HeatmapControlsPanel.qml`'s own default `sortMode: "mz"`),
    and the rendered target string
    (`"<sample>|<mz>|TIC|viridis|auto|auto"`) is built to match
    `HeatmapControlsPanel.tileTarget`'s default-state output exactly. A
    mismatch (e.g. a float-to-string formatting difference between
    Python and QML/JS in some edge case) only costs a cache *miss* —
    `requestImage` falls back to rendering live, so this is a best-effort
    optimization, never a correctness risk.

    Concurrency: this worker calls into the exact same matplotlib-backed
    render path (`HeatmapImageProvider._render_target`) the GUI thread's
    `requestImage` also uses — but never *at the same time* as it. The
    caller (`AnalysisPage.goToVisualInspection`) disables the whole nav
    rail (`currentSectionReady`/`switchingToVisual`) for the entire
    duration of this worker's run, so no `ZoomableImage` exists yet to
    make a competing GUI-thread render call, and Visual Inspection (the
    only place that opens ROI Design / Coregistration, the other
    `HeatmapImageProvider` consumers) isn't reachable until this worker
    finishes. One prefetch in flight at a time, one render happening
    anywhere in the app at a time — the same "never concurrent" property
    that made `asynchronous: false` safe in the first place, just with
    the single render allowed to happen on a background thread instead
    of only ever the GUI thread.

    One-shot per request, not reused — same lifecycle as
    `TableQueryWorker`/`MirrorPlotWorker`.
    """

    succeeded = Signal()
    failed = Signal(str)  # human-readable error message

    def __init__(
        self,
        heatmap_provider: HeatmapImageProvider,
        sample_names: list[str],
        default_mz: float | None = None,
        parent=None,
        dataset_token: str = "ds-1",
    ) -> None:
        super().__init__(parent)
        self._heatmap_provider = heatmap_provider
        self._sample_names = sample_names
        self._default_mz = default_mz
        # The trailing request-id field `HeatmapControlsPanel.tileTarget`
        # appends to name the active dataset (ADR 49). It has to match
        # exactly, or the pre-rendered tile's cache key would differ from
        # the one QML then requests.
        self._dataset_token = dataset_token

    def run(self) -> None:
        try:
            for name in self._sample_names:
                self._heatmap_provider._load_adata(name)
                if self._default_mz is not None:
                    target = str(self._default_mz)
                    parts = [name, target, "TIC", "viridis", "auto", "auto",
                             self._dataset_token]
                    self._heatmap_provider._render_target(name, target, parts)
        except Exception as e:
            logger.exception("heatmap prefetch failed for %s", self._sample_names)
            self.failed.emit(str(e))
            return
        self.succeeded.emit()
