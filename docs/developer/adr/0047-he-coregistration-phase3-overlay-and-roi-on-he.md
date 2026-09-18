# 47 — H&E coregistration Phase 3: warped-heatmap overlay, and drawing ROI on the H&E image

**Status:** Accepted

## Context

Phase 3 of [ADR 45](0045-he-image-coregistration-phase1.md)'s plan, the
last piece of the original feature: warp the MSI heatmap onto the H&E
image for a visual overlay, and let ROI be drawn directly on the H&E
image (the actual point of the whole feature — draw on the
higher-resolution histology, have it map onto the MSI pixel grid).

## Decision

### Warped overlay: plain PIL affine warp, not a GPU/QML transform

`core/registration/overlay.py::warp_heatmap_to_he_space` resamples the
rendered heatmap RGBA raster into an array the exact size of the H&E
image, via `PIL.Image.transform((he_width, he_height), Image.AFFINE,
data=he_to_grid_matrix.flatten(), resample=Image.NEAREST, fillcolor=(0,0,0,0))`.
The registration's stored `he_to_grid` matrix is exactly the
"output pixel → input pixel" direction PIL's `AFFINE` transform expects
(H&E space is the output/each pixel of the warped result, grid space is
the input it samples), so its coefficients are used directly — no
inversion, no separate GPU-transform math.

**Alternative considered and rejected**: composing the affine as a QML
`Item.transform: [Matrix4x4 {...}]` on the heatmap `Image`, letting the
GPU warp it live. Rejected — this codebase's established pattern for
every spatial raster (`HeatmapImageProvider`, `HEImageProvider`) is
"render an exact RGBA array in Python, wrap in `QImage`, serve via a
`QQuickImageProvider`" (see [ADR 13](0013-hybrid-plotting-approach.md)),
and matching it here means the overlay is a completely ordinary same-size
`Image` sibling with `anchors.fill: parent`, needing zero special-cased
QML transform/`transformOrigin` reasoning to line it up pixel-for-pixel
with the H&E image underneath.

`HeatmapImageProvider`'s and `HEOverlayImageProvider`'s request-id
dispatch (parsing `sampleName|mz|layer|colormap|vmin|vmax` or
`sampleName|obs:col|...` into a `render_feature_heatmap`/
`render_obs_heatmap`/`render_obs_categories_heatmap` call) is now
factored into `core/plotting/heatmap.py::render_heatmap_by_target`,
shared by both providers instead of duplicated. Likewise
`HeatmapControlsPanel.tileTarget(sampleName)` (the `sampleName|...` tail)
is now the one place that builds it; `tileSource` and the new overlay
URL both just prefix it with their own `image://...` scheme.

### ROI-on-H&E: convert at the input, not the storage

Vertices remain **always stored in MSI grid-index space** —
`RoiDesignWindow`'s `draftVertices`/`savedRois`, and everything in
`core/plotting/roi.py`, are completely unchanged. "Draw on H&E" is purely
an *input* mode: `RoiDrawingCanvas.heMode` converts each H&E-pixel tap to
a grid-index coordinate (via the fitted `he_to_grid` matrix) before it
ever reaches `vertexRequested`, and converts stored grid-index vertices
back to H&E-pixel space (via that matrix's inverse) only for *displaying*
the in-progress polygon and existing saved ROIs on the H&E pane.

This needed the transform math available **client-side, in QML/JS**
(`Utils/AffineTransform.js`, a JS port of `core/registration/
transform.py`'s `apply_transform`/`invert_transform`) — round-tripping
every tap and every repaint through an `AnalysisBridge` call would be far
too slow for live drawing feedback. The fitted matrix itself still comes
from Python once per sample-switch, added as a `matrix` field on
`AnalysisBridge.getRegistrationInfo`'s existing response (`[[a,b,c],
[d,e,f]]`, JSON/QML-plain via `.tolist()`).

`RoiOverlay.qml` gained one boolean, `pixelSpace` (default `false`,
unchanged behavior): `false` keeps the original grid-index cell-center
convention (`(col + 0.5) * scale`); `true` treats vertices as continuous
pixel coordinates already in the target canvas' own space (`col * scale`,
no offset — a landmark/vertex on a continuous image isn't a grid-cell
membership test). `RoiDrawingCanvas` sets it to `canvas.heMode` and feeds
it pre-mapped vertices; nothing about `RoiOverlay`'s *own* logic needed to
know about registration matrices at all.

`RoiDesignWindow` gained a "Draw on H&E image" checkbox, visible only
when the selected sample has a fitted registration
(`roiRegistrationInfo.hasFit`) — reset to the grid surface on every
sample switch (a different sample's registration, or lack of one,
shouldn't silently carry over), fetched the same way
`CoregistrationWindow` already does
(`AnalysisBridge.getRegistrationInfo`). While in H&E mode, the pane also
shows the warped-heatmap overlay (reusing `image://he_overlay/...`) so a
user can still see the underlying chemical signal while drawing on tissue
morphology, not just blank H&E.

**Follow-up (2026-09-18, confirmed working in the real app first):** the
overlay show/opacity controls (`CoregistrationWindow`'s "Overlay"
checkbox + slider) were ported to `RoiDesignWindow` too, once "Draw on
H&E image" is active — same two `RoiDrawingCanvas` properties
(`showOverlay`/`overlayOpacity`) the window's checkbox/slider already
existed for on the coregistration side, just plumbed through a second
caller. No new concept, no new provider — the overlay image element
already took an `opacity`/`visible` binding, they just weren't exposed as
window-level controls outside `CoregistrationWindow` yet.

## Alternatives considered

- **Storing ROI vertices in H&E-pixel space when drawn that way.**
  Rejected — would mean `core/plotting/roi.py::polygon_pixel_mask` (and
  every consumer of `adata.uns["rois"]`) would need to know which space a
  given ROI's vertices are in, forever, including ROIs drawn before this
  feature existed. Converting at the input keeps storage format single
  and unchanged; the "which surface was this originally drawn on" fact
  simply isn't needed after the fact.
- **A `QQuickImageProvider`-side round trip for tap-to-grid conversion**
  (call into `AnalysisBridge` on every tap). Rejected for latency — a
  drawing interaction needs to feel instant; the fit matrix is tiny and
  changes only on sample-switch/re-fit, so fetching it once and doing the
  arithmetic in JS is both simpler and faster.

## Consequences

- This closes out the plan from ADR 45: attach an image, place landmarks,
  fit a transform, see it overlaid, and draw ROI directly on the H&E
  image with correct grid-space storage underneath.
- Still deferred (not needed for the above to work, and not requested):
  non-rigid (thin-plate spline) transform option, automatic
  intensity-based refinement after landmark initialization.
- `Utils/AffineTransform.js` is now the second shared QML JS utility
  (after `Utils/SearchQuery.js`) — any future client-side coordinate math
  involving a fitted registration should live there rather than being
  re-derived per-file.
