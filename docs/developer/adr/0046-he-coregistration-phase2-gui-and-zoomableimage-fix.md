# 46 — H&E coregistration Phase 2: `CoregistrationWindow` GUI, and a `ZoomableImage` input bug found along the way

**Status:** Accepted

## Context

Phase 2 of [ADR 45](0045-he-image-coregistration-phase1.md)'s plan: a GUI
for attaching an H&E/brightfield image to a sample and placing the
landmark pairs Phase 1's `core/registration/` fits a transform from. No
GUI existed yet — Phase 1 was core-only.

While building and testing the new window, tapping the MSI-side pane
never registered a landmark, while tapping the H&E-side pane (once an
image was attached) worked. Isolating this (a minimal reproduction QML
file outside the app, with two side-by-side `Flickable`+`TapHandler`
panes) showed the actual cause: **a `ZoomableImage` whose `Image` element
has collapsed to `0x0` (which is exactly what happens whenever
`Image.status !== Ready` — empty `source`, still loading, or a failed
provider request) swallows every tap/click anywhere in that
`Flickable`**, for every `PointerHandler` declared inside it, even though
the `Flickable` itself keeps a nonzero width/height throughout. This is a
real, general `ZoomableImage.qml` bug, not something specific to the new
window — it just never had a way to surface before, because every
existing caller (Visual Inspection's heatmap tiles, ROI Design) always
has a real image behind the pane by the time a user could click on it.
`CoregistrationWindow` is the first caller that can legitimately be
interactive before its image has resolved: the H&E pane before an image
is attached, and the MSI pane before Visual Inspection's controls have
settled on a feature/obs column.

## Decision

### GUI shape

- `attachHeImage`/`getRegistrationInfo`/`saveRegistration` added to
  `AnalysisBridge`, plus `setHeImageAnalysis` (the `image://he_image/...`
  analogue of `setHeatmapAnalysis`). All three resolve the sample's raw
  database via the new `analysis_db.get_sample_raw_db_path` (a plain
  `SELECT raw_db_path FROM samples WHERE name = ?` — the same two values
  (`analysis_db_path`, `sample_name`) every other bridge slot already
  takes, now also reaching into the raw layer). `getRegistrationInfo`
  returns a flat dict (`hasImage`, `width`/`height`/`format`, `hasFit`,
  `transformType`, `rmse`, `landmarks`) so `CoregistrationWindow.qml` has
  one call to (re-)seed all of its state from on open/sample-switch.
- `HEImageProvider` (`gui/utils/he_image_provider.py`), registered as
  `image://he_image/...` in `main.py` and in `tests/gui/conftest.py`'s
  `analysis_view` fixture (parity with `main.py`'s wiring, needed for
  `CoregistrationWindow`'s GUI tests to exercise a real image).
- `CoregistrationWindow.qml` — a `Loader`+`openFor()` singleton popup, the
  same shape as `RoiDesignWindow`/`MirrorPlotDetailWindow` (see
  `developer/architecture/gui.md`), opened from a new "Coregister H&E
  Image" button in Visual Inspection. Reuses the already-showing
  `HeatmapControlsPanel` for its MSI-side pane, exactly like
  `RoiDesignWindow` does — no duplicate feature/colormap controls.
- **Landmark placement UX**: tap a point on either pane; the next tap on
  the *other* pane completes that pair (`handleTap(side, x, y)`, shared by
  both panes). Tapping the same pane again before pairing just moves the
  pending point rather than starting a stray cross-pane pair — cheap
  "I misclicked" recovery with no separate undo step needed for that
  case. Coordinates are plain continuous floats in each pane's own native
  pixel space (`tapPosition / effectiveScale`) — deliberately *not*
  ROI's `+0.5` grid-cell-center convention (`core/plotting/roi.py`'s
  `polygon_pixel_mask`), since a landmark is one clicked point being fit
  against, not a polygon membership test against a pixel-grid cell.
- `LandmarkOverlay.qml` — the landmark-marker analogue of `RoiOverlay.qml`
  (numbered, colored circles instead of polygon borders), shared by both
  panes so a pair's H&E-side and MSI-side markers visibly match by color.
- Minimum-viable coverage indicator: `RegistrationFit.reprojection_errors`
  (Phase 1) is surfaced per-landmark in the side panel (`"#1 (err 0.42)"`)
  after a fit, and as an overall RMSE in the save-status line.

### `ZoomableImage.qml` fix

```qml
width: image.status === Image.Ready ? sourceSize.width * root.effectiveScale : root.width
height: image.status === Image.Ready ? sourceSize.height * root.effectiveScale : root.height
```

replacing the previous unconditional `sourceSize.width/height *
effectiveScale`. A non-`Ready` image now fills the viewport instead of
collapsing to `0x0`, which is what actually keeps the enclosing
`Flickable`'s `PointerHandler`s receiving input. Applies to every
`ZoomableImage` user, not just the new window.

## Alternatives considered

- **Simulating the "Attach Image..." `FileDialog` in GUI tests** (setting
  `selectedFile` and firing `accepted`). Rejected — no existing test in
  this codebase attempts to drive a native `FileDialog` headlessly either
  (checked: zero hits for `FileDialog`/`selectedFile`/`invokeMethod`
  across `tests/gui/views/`); the dialog itself is a thin wrapper with no
  custom logic. Tested instead: the bridge slot (`attachHeImage`)
  directly at the Python level (`tests/gui/utils/test_analysis_bridge.py`)
  for the actual attach logic, and the window's *reaction* to a
  successful attach (via `core.registration.attach_he_image` called
  directly before opening the window in
  `tests/gui/views/test_coregistration_window.py`) for the QML wiring.
- **A `MouseArea` instead of `TapHandler`** for landmark placement, once
  the real bug was found and briefly suspected to be `TapHandler`-specific
  during isolation. Not needed — the actual cause was the `0x0` image
  size, unrelated to which pointer-handling primitive was used;
  `RoiDrawingCanvas.qml`'s existing "`TapHandler`, not `MouseArea`, so
  panning still works" reasoning still applies and was kept.

## Consequences

- Any future `ZoomableImage` caller that can legitimately show before its
  image resolves is now safe by default; no caller-side workaround needed.
- `tests/gui/conftest.py`'s `analysis_view` fixture now registers
  `he_image` too — any future test opening `CoregistrationWindow` (or
  anything else that reads a real attached image through it) gets a
  working provider for free.
- Phase 3 (deferred, per ADR 45): warp-and-overlay the MSI heatmap onto
  the H&E pane with opacity control, and an H&E-drawing mode for
  `RoiDrawingCanvas.qml` that applies `invert_transform` to vertices
  before they reach the existing, unmodified `core/plotting/roi.py`.
