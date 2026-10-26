# 58 — Select, move, copy and paste ROIs in ROI Design

**Status:** Accepted

## Context

ROI Design could only add an ROI (draw, close, saved) or delete one. A
slightly misplaced ROI had to be deleted and redrawn, and the same shape
on several samples had to be drawn once per sample.

## Decision

All in QML (`RoiDesignWindow`, `RoiDrawingCanvas`, `RoiOverlay`); no core,
bridge or schema change. Every edit is an ordinary
`AnalysisBridge.saveRoi`, which already overwrites a name present on the
sample, so the mask, `merged.h5ad` and an imported dataset's write-back
(ADR 49) all follow, as for a drawn ROI.

- **Select** (idle only): click inside an ROI on the canvas or its name
  in the list; click on empty space or Esc deselects. Drawn thicker with a
  light fill. Where ROIs overlap, a click picks the smallest one under
  the pointer, so an ROI drawn inside another can be selected whatever
  the drawing order (the outer one by clicking outside the inner one).
- **Move:** drag inside the selected ROI, or arrow keys (Shift = 10
  pixels). While the pointer is over the selected ROI (its own outline,
  even if another ROI overlaps it) the image's drag-to-pan is paused
  (`ZoomableImage.interactive`), so the drag reaches a `DragHandler`
  instead of the Flickable; dragging elsewhere still pans. That state is
  refreshed on hover, on every press and on a selection change: on a real
  Wayland session no hover may arrive between selecting and dragging, and
  the image then took the drag (offscreen tests never showed it, since
  there the handler always wins). A move cursor shows when a drag will
  move the ROI.
  The preview snaps to whole cells on the grid surface; nothing is saved
  until release. On the H&E surface the drag is converted to grid units
  with the linear part of the He-pixel -> grid affine.
- **Saved on release**, matching "closing the polygon saves". Ctrl+Z
  undoes moves one at a time, back through every move on the sample
  (cleared on a sample switch). Pastes aren't undone by it; delete the
  pasted ROI instead.
- **Arrow keys are batched:** each press only shifts the outline on
  screen; the ROI is saved once the keys have been idle for 0.4 s. A save
  rewrites the sample's `.h5ad` and `merged.h5ad` and re-renders the tile
  (~0.1-0.2 s on a real dataset), so one save per press queued up into
  seconds of freeze while a key was held. Anything that needs the real
  geometry saves the pending moves first (selection, drag, undo,
  copy/paste, delete, sample switch, closing the window). A burst of
  presses is one undo step.
- **Copy/paste:** Ctrl+C / Ctrl+V or the Copy/Paste buttons. The
  clipboard is window-local and survives a sample switch. Paste reuses the
  naming step: the same name when the target sample doesn't have it (the
  same ROI on another sample, same position), otherwise `<name>_copy` with
  a fresh color, offset by 5 pixels so it isn't hidden under the original.
  The pasted ROI is selected, ready to drag.
- **Name guard:** a new or pasted ROI whose name already exists on the
  sample is refused. Before this, "Add ROI" with an existing name silently
  overwrote that ROI on save.
- **`roiSaved` fires for moves and pastes too**: in the calibration
  windows an unknown is re-measured (upsert) and a standard asks for its
  amount again, so stored values follow the new geometry.

## Alternatives

- **A pending move with an explicit Save:** rejected by the user; saving on
  release with undo matches the drawing flow.
- **A dedicated "Move" mode toggle** instead of pausing pan over the
  selection: one more click per move, no gain.
- **The system clipboard:** a window-local one is enough to copy between
  samples; nothing outside the app consumes ROI geometry.

## Consequences

- An ROI moved in Visual Inspection's ROI Design (not a calibration
  window) leaves calibration values measured on it stale, as redrawing it
  under the same name already did.
- Moves aren't clamped to the image: an ROI dragged off the grid covers
  fewer (possibly zero) pixels until dragged back or undone.
- Not included: editing single vertices, resize/rotate, multi-select.
