import numpy as np

from msianalyzer.core.registration.overlay import warp_heatmap_to_he_space


def _solid_rgba(width, height, color):
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    rgba[:, :] = color
    return rgba


def test_warp_identity_transform_preserves_positions_and_leaves_rest_transparent():
    # A 4x4 heatmap, identity transform onto an equally-sized H&E canvas
    # -> every pixel should read back exactly the source pixel at the
    # same position.
    heatmap = np.zeros((4, 4, 4), dtype=np.uint8)
    heatmap[0, 0] = (255, 0, 0, 255)
    heatmap[0, 3] = (0, 255, 0, 255)
    heatmap[3, 0] = (0, 0, 255, 255)
    heatmap[3, 3] = (255, 255, 0, 255)

    identity = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    warped = warp_heatmap_to_he_space(heatmap, identity, he_width=4, he_height=4)

    assert warped.shape == (4, 4, 4)
    assert tuple(warped[0, 0]) == (255, 0, 0, 255)
    assert tuple(warped[0, 3]) == (0, 255, 0, 255)
    assert tuple(warped[3, 0]) == (0, 0, 255, 255)
    assert tuple(warped[3, 3]) == (255, 255, 0, 255)


def test_warp_scaling_transform_upscales_quadrants():
    # A 2x2 heatmap, each quadrant a distinct solid color; grid = he/2
    # onto a 4x4 H&E canvas should read each quadrant back 2x upscaled.
    heatmap = np.zeros((2, 2, 4), dtype=np.uint8)
    heatmap[0, 0] = (255, 0, 0, 255)  # grid (x=0, y=0)
    heatmap[0, 1] = (0, 255, 0, 255)  # grid (x=1, y=0)
    heatmap[1, 0] = (0, 0, 255, 255)  # grid (x=0, y=1)
    heatmap[1, 1] = (255, 255, 0, 255)  # grid (x=1, y=1)

    half_scale = np.array([[0.5, 0.0, 0.0], [0.0, 0.5, 0.0]])
    warped = warp_heatmap_to_he_space(heatmap, half_scale, he_width=4, he_height=4)

    assert tuple(warped[0, 0]) == (255, 0, 0, 255)  # he (0,0) -> grid (0,0)
    assert tuple(warped[0, 2]) == (0, 255, 0, 255)  # he (2,0) -> grid (1,0)
    assert tuple(warped[2, 0]) == (0, 0, 255, 255)  # he (0,2) -> grid (0,1)
    assert tuple(warped[2, 2]) == (255, 255, 0, 255)  # he (2,2) -> grid (1,1)


def test_warp_translation_shifts_the_sampled_region():
    heatmap = _solid_rgba(4, 4, (10, 20, 30, 255))
    heatmap[0, 0] = (255, 255, 255, 255)  # a single marker pixel at grid (0, 0)

    # he_to_grid: grid = he - (1, 1) -> sampling H&E pixel (1, 1) reads
    # grid pixel (0, 0), the marker.
    matrix = np.array([[1.0, 0.0, -1.0], [0.0, 1.0, -1.0]])
    warped = warp_heatmap_to_he_space(heatmap, matrix, he_width=5, he_height=5)

    assert tuple(warped[1, 1]) == (255, 255, 255, 255)
    assert tuple(warped[0, 0]) == (0, 0, 0, 0)  # maps to grid (-1,-1): out of bounds


def test_warp_out_of_bounds_pixels_are_transparent():
    heatmap = _solid_rgba(2, 2, (100, 100, 100, 255))
    # Scale grid space down so most of a larger H&E canvas maps outside
    # the 2x2 heatmap's bounds.
    matrix = np.array([[3.0, 0.0, 0.0], [0.0, 3.0, 0.0]])
    warped = warp_heatmap_to_he_space(heatmap, matrix, he_width=6, he_height=6)

    assert tuple(warped[5, 5]) == (0, 0, 0, 0)
    assert tuple(warped[0, 0]) == (100, 100, 100, 255)


def test_warp_output_shape_matches_requested_he_dimensions():
    heatmap = _solid_rgba(3, 2, (1, 2, 3, 255))
    matrix = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])

    warped = warp_heatmap_to_he_space(heatmap, matrix, he_width=10, he_height=7)

    assert warped.shape == (7, 10, 4)
    assert warped.dtype == np.uint8
