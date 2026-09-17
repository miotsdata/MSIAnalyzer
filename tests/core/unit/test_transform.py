import numpy as np
import pytest

from msianalyzer.core.registration.transform import (
    apply_transform,
    fit_affine_transform,
    fit_similarity_transform,
    invert_transform,
    reprojection_errors,
)


def test_fit_affine_transform_recovers_exact_transform_from_3_points():
    known = np.array([[2.0, 0.5, 10.0], [-0.3, 1.5, -4.0]])
    src = np.array([[0.0, 0.0], [10.0, 0.0], [0.0, 10.0]])
    dst = apply_transform(known, src)

    fitted = fit_affine_transform(src, dst)

    np.testing.assert_allclose(fitted, known, atol=1e-9)


def test_fit_affine_transform_least_squares_with_redundant_points():
    known = np.array([[1.2, -0.4, 5.0], [0.3, 0.9, -2.0]])
    src = np.array([[0.0, 0.0], [10.0, 0.0], [0.0, 10.0], [10.0, 10.0], [5.0, 3.0]])
    dst = apply_transform(known, src)

    fitted = fit_affine_transform(src, dst)

    np.testing.assert_allclose(fitted, known, atol=1e-6)


def test_fit_affine_transform_requires_at_least_3_points():
    with pytest.raises(ValueError, match="at least 3"):
        fit_affine_transform(
            np.array([[0.0, 0.0], [1.0, 0.0]]), np.array([[0.0, 0.0], [1.0, 0.0]])
        )


def test_fit_affine_transform_rejects_collinear_points():
    src = np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]])
    dst = np.array([[0.0, 0.0], [1.0, 1.0], [2.0, 2.0]])
    with pytest.raises(ValueError, match="collinear"):
        fit_affine_transform(src, dst)


def test_fit_affine_transform_rejects_mismatched_shapes():
    with pytest.raises(ValueError):
        fit_affine_transform(
            np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]]),
            np.array([[0.0, 0.0], [1.0, 0.0]]),
        )


def test_fit_similarity_transform_recovers_exact_rotation_scale_translation():
    theta = np.deg2rad(30.0)
    scale = 1.7
    rotation = np.array(
        [[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]]
    )
    translation = np.array([5.0, -3.0])
    known = np.column_stack([scale * rotation, translation])

    src = np.array([[0.0, 0.0], [10.0, 0.0], [0.0, 10.0], [7.0, 4.0]])
    dst = apply_transform(known, src)

    fitted = fit_similarity_transform(src, dst)

    np.testing.assert_allclose(fitted, known, atol=1e-9)


def test_fit_similarity_transform_requires_at_least_2_points():
    with pytest.raises(ValueError, match="at least 2"):
        fit_similarity_transform(np.array([[0.0, 0.0]]), np.array([[0.0, 0.0]]))


def test_fit_similarity_transform_rejects_coincident_points():
    src = np.array([[3.0, 3.0], [3.0, 3.0], [3.0, 3.0]])
    dst = np.array([[0.0, 0.0], [1.0, 1.0], [2.0, 2.0]])
    with pytest.raises(ValueError, match="coincide"):
        fit_similarity_transform(src, dst)


def test_fit_similarity_transform_never_introduces_reflection():
    # A mirrored correspondence: a true reflection would fit perfectly, but
    # a similarity transform must never introduce one, only the best
    # rotation+scale approximation.
    src = np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
    dst = np.array([[0.0, 0.0], [1.0, 0.0], [0.0, -1.0]])

    fitted = fit_similarity_transform(src, dst)

    rotation_block = fitted[:, :2]
    assert np.linalg.det(rotation_block) > 0


def test_apply_transform_identity():
    identity = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    points = np.array([[1.0, 2.0], [3.0, 4.0]])
    np.testing.assert_allclose(apply_transform(identity, points), points)


def test_invert_transform_round_trips():
    matrix = np.array([[2.0, 0.5, 10.0], [-0.3, 1.5, -4.0]])
    points = np.array([[1.0, 2.0], [30.0, -5.0], [0.0, 0.0]])

    forward = apply_transform(matrix, points)
    inverse = invert_transform(matrix)
    back = apply_transform(inverse, forward)

    np.testing.assert_allclose(back, points, atol=1e-9)


def test_reprojection_errors_zero_for_exact_fit():
    matrix = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    points = np.array([[1.0, 2.0], [3.0, 4.0]])
    errors = reprojection_errors(matrix, points, points)
    np.testing.assert_allclose(errors, [0.0, 0.0], atol=1e-12)


def test_reprojection_errors_nonzero_for_offset():
    matrix = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    src = np.array([[0.0, 0.0]])
    dst = np.array([[3.0, 4.0]])
    errors = reprojection_errors(matrix, src, dst)
    np.testing.assert_allclose(errors, [5.0])
