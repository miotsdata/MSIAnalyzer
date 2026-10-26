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

- **Select** (idle only): click inside an ROI on the canvas (topmost one,
  point-in-polygon in item pixels) or its name in the list; click on
  empty space or Esc deselects. Drawn thicker with a light fill.
- **Move:** drag inside the selected ROI, or arrow keys (Shift = 10
  pixels). While the pointer is over the selected ROI the image's
  drag-to-pan is paused (`ZoomableImage.interactive`), so the drag reaches
  a `DragHandler` instead of the Flickable; dragging elsewhere still pans.
  The preview snaps to whole cells on the grid surface; nothing is saved
  until release. On the H&E surface the drag is converted to grid units
  with the linear part of the He-pixel -> grid affine.
- **Saved on release**, matching "closing the polygon saves". Ctrl+Z
  undoes the last move (one level, cleared on a sample switch).
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
