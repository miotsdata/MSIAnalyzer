"""
overlay.py
Warp an MSI heatmap raster into an H&E image's pixel space, using a
fitted registration transform (see `image_registration.py`) — for
displaying the heatmap as a semi-transparent overlay on top of the
higher-resolution H&E image in `CoregistrationWindow`.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

__all__ = ["warp_heatmap_to_he_space"]


def warp_heatmap_to_he_space(
    heatmap_rgba: np.ndarray,
    he_to_grid_matrix: np.ndarray,
    he_width: int,
    he_height: int,
) -> np.ndarray:
    """Resample `heatmap_rgba` (rendered in MSI pixel-grid-index space,
    see `core/plotting/heatmap.py`) into an `(he_height, he_width, 4)`
    RGBA array in H&E pixel space, via `he_to_grid_matrix`.

    Uses nearest-neighbor resampling — matches the rest of this app's
    heatmap rendering (see `ZoomableImage.qml`'s `smooth: false`): a
    heatmap pixel is one spatial measurement, not something to blur into
    its neighbors. Any H&E pixel that maps outside the heatmap raster's
    own bounds renders fully transparent rather than clamping to an edge
    pixel or erroring.

    Args:
        heatmap_rgba: `(grid_height, grid_width, 4)` uint8 RGBA, as
            returned by `render_feature_heatmap`/`render_obs_heatmap`/
            `render_obs_categories_heatmap`/`render_heatmap_by_target`.
        he_to_grid_matrix: `(2, 3)` affine matrix mapping H&E pixel
            coordinates onto MSI grid-index coordinates — the direction
            `fit_and_save_registration` already stores (`RegistrationFit.matrix`).
        he_width: The H&E image's width, in pixels.
        he_height: The H&E image's height, in pixels.

    Returns:
        `(he_height, he_width, 4)` uint8 RGBA.
    """
    heatmap_image = Image.fromarray(np.ascontiguousarray(heatmap_rgba), mode="RGBA")

    # PIL's AFFINE transform maps each OUTPUT pixel (x, y) to the INPUT
    # pixel it samples via `input = (a*x + b*y + c, d*x + e*y + f)` —
    # exactly what `he_to_grid_matrix` already computes (output = H&E
    # space, input = grid space), so its coefficients are used directly,
    # with no inversion needed.
    coeffs = tuple(float(v) for v in np.asarray(he_to_grid_matrix, dtype=float).flatten())
    warped = heatmap_image.transform(
        (he_width, he_height),
        Image.AFFINE,
        data=coeffs,
        resample=Image.NEAREST,
        fillcolor=(0, 0, 0, 0),
    )
    return np.array(warped)
