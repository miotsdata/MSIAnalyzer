# 45 — H&E/brightfield image coregistration, Phase 1: raw-DB storage + landmark fit

**Status:** Accepted

## Context

New feature: overlay a brightfield/H&E microscopy image onto a sample's
MSI spatial data, mainly so ROI can be drawn on the higher-resolution
histology image and mapped onto the MSI pixel grid. Discussed and planned
with the user before any implementation (coregistration approaches,
resolution mismatch, storage location).

Two open questions decided before coding:

1. **Which registration approach?** H&E (tissue morphology, RGB) and an
   MSI ion/TIC image (chemical intensity) share no visual or statistical
   signal, so automatic feature-matching (SIFT/ORB) or intensity-based
   registration (mutual information) are weak fits as the primary method —
   both assume some shared appearance the two modalities don't have here.
   Manual landmark-based registration (user clicks corresponding points on
   both images, fit a transform) is the standard approach for this exact
   problem in the MSI literature, and what the user's own second idea
   already proposed.
2. **Where does the registration live?** The MSI pixel grid
   (`spatial_pixels`, written by
   `core/utils/spectra_pixels_association.py::map_pixels_to_db` from the
   AP-MALDI raster XML, see `core/parser/xml_parser.py::parse_raster_xml`)
   is fixed at parse time and identical across every re-analysis of a
   sample — annotation config, library, and scoring can all change per
   analysis, but the pixel grid never does. The user explicitly did not
   want to re-register the image every time they re-run analysis with
   different settings (as ROIs currently require, being stored per
   analysis in `.h5ad`, see `core/plotting/roi.py`). Decision: registration
   lives in the **raw per-sample database** (same file `spatial_pixels`
   lives in), read through by any analysis at render time — not
   `adata.uns`.

Confirmed constraints (from the user) that shaped scope:
- Images are regular TIFF/PNG/JPEG, a few thousand px/side — not
  whole-slide pyramidal formats (`.svs`/`.ndpi`/`.mrxs`). No tiled/
  streaming rendering needed.
- The image file is copied into the sample's raw data folder at
  attach-time (self-contained), not referenced by an external path.
- H&E/MSI sections are sometimes the same physical slice, sometimes
  serial/adjacent sections (varies by experiment) — real local tissue
  deformation is possible, so raw landmark pairs are stored (not just the
  fitted matrix), keeping a future non-rigid (thin-plate spline) upgrade a
  re-fit rather than a redo of manual landmark placement.

## Decision

New `core/registration/` package (parallel to `core/parser/`,
`core/plotting/`, `core/annotation/`):

- `transform.py` — pure point-based 2D transform math, no I/O:
  `fit_affine_transform`/`fit_similarity_transform` (least-squares affine,
  and closed-form Umeyama similarity with reflection correction),
  `apply_transform`, `invert_transform`, `reprojection_errors`. Returns a
  uniform `(2, 3)` matrix `[[a, b, c], [d, e, f]]` regardless of transform
  type, so downstream code doesn't need to special-case similarity vs.
  affine.
- `image_registration.py` — raw-DB + file I/O: `attach_he_image` (copies
  the image into `<raw_db_parent>/images/he_image<ext>`, records it in a
  new `registered_images` table), `fit_and_save_registration` (fits from
  `[((he_x, he_y), (grid_x, grid_y)), ...]` landmark pairs, stores the
  matrix, landmarks, and per-landmark reprojection error in
  `image_registrations`/`registration_landmarks`), `load_registration`
  (read-only, returns `None` if the raw DB predates this feature or has no
  image attached).

At most **one** attached image/registration per sample: re-attaching an
image deletes the previous image file and every row (`registered_images`,
`image_registrations`, `registration_landmarks`) for that sample, matching
`map_pixels_to_db`'s existing delete-then-reinsert convention for
raw-DB-derived tables — the user confirmed re-registration will be rare
enough that keeping history isn't worth the complexity.

No new heavy dependency: images are small enough (a few thousand px/side)
that Pillow — already present transitively via matplotlib — was promoted
to a direct dependency (`pillow>=12.3.0`) and is sufficient for image I/O.
Affine/similarity fitting is plain `numpy` least-squares/SVD. No
scikit-image/OpenCV/SimpleITK needed unless non-rigid registration is
actually built later, which matters given the project's PyInstaller/
AppImage packaging size concerns.

Not a pipeline step: like `core/plotting/roi.py`, this is a direct
interactive action (attach an image, place landmarks, save) — nothing
here touches `analysis_db.log_command`/`is_command_already_run`
bookkeeping.

## Alternatives considered

- **Automatic feature/intensity-based registration as the primary
  method.** Rejected for the reason above — H&E and an ion image share no
  reliable visual/statistical signal for SIFT/ORB or mutual information to
  latch onto. Left as a possible future *refinement* step after landmark
  initialization, not the primary method.
- **Storing registration in the per-analysis `.h5ad`, like ROIs.**
  Rejected per the user's explicit reasoning: the pixel grid a
  registration is fitted against doesn't change between re-analyses of the
  same sample, so tying it to the analysis layer would force needless
  re-registration on every re-run.
- **OpenCV/scikit-image for image I/O and warping.** Rejected for the
  MVP — regular-sized images and a plain affine/similarity fit don't need
  it; Pillow (already present) covers I/O, and `numpy` covers the fit
  math. Revisit only if non-rigid registration is built.

## Consequences

- Phase 1 only: raw-DB schema + core fit/attach/load functions, with unit
  tests (`tests/core/unit/test_transform.py`,
  `tests/core/unit/test_image_registration.py`). No GUI yet.
- Still to build (later phases, not yet started): "Attach H&E Image" GUI
  action; a `CoregistrationWindow.qml` (mirrors `RoiDesignWindow.qml`) for
  clicking landmark pairs against the existing MSI TIC heatmap provider;
  an H&E-mode toggle in `RoiDrawingCanvas.qml` that applies
  `invert_transform` to drawn vertices before they reach the existing,
  unmodified `roi.py` rasterization; a warped-heatmap-on-H&E overlay
  display.
- `pyproject.toml` dependencies gained `pillow>=12.3.0` (was already
  resolved transitively via matplotlib, so `pdm.lock` changed minimally).
