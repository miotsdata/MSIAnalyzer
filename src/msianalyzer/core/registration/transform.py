"""
transform.py
Pure point-based 2D coordinate transform math for H&E/brightfield image
coregistration: fitting a similarity or affine transform from user-picked
landmark pairs (H&E pixel coords -> MSI pixel-grid-index coords, see
`core/plotting/heatmap.py::pixel_grid_indices`), and applying/inverting it.

No file or database I/O here — see `image_registration.py` for that.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = [
    "Landmark",
    "RegistrationFit",
    "fit_affine_transform",
    "fit_similarity_transform",
    "apply_transform",
    "invert_transform",
    "reprojection_errors",
]


@dataclass(frozen=True)
class Landmark:
    """One user-picked point correspondence: a pixel in the H&E image and
    the MSI grid-index cell (see `pixel_grid_indices`) it corresponds to."""

    he_x: float
    he_y: float
    grid_x: float
    grid_y: float


@dataclass(frozen=True)
class RegistrationFit:
    """A fitted H&E -> MSI-grid-index transform plus the landmarks and
    per-landmark reprojection error it was fitted from, for quality
    feedback in the GUI."""

    transform_type: str  # "affine" | "similarity"
    matrix: np.ndarray  # shape (2, 3): [[a, b, c], [d, e, f]]
    landmarks: list[Landmark]
    reprojection_errors: list[float]  # grid-index units, one per landmark
    rmse: float


def _validate_point_pairs(src: np.ndarray, dst: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    src = np.asarray(src, dtype=float)
    dst = np.asarray(dst, dtype=float)
    if src.ndim != 2 or src.shape[1] != 2 or src.shape != dst.shape:
        raise ValueError(
            f"src/dst must both be (N, 2) with matching N, got {src.shape} and {dst.shape}"
        )
    return src, dst


def fit_affine_transform(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Least-squares affine transform (rotation, scale, shear, translation)
    mapping `src` points onto `dst` points.

    Args:
        src: (N, 2) source points, N >= 3, not all collinear.
        dst: (N, 2) destination points, same length as `src`.

    Returns:
        A (2, 3) matrix `[[a, b, c], [d, e, f]]` such that
        `dst_x = a*src_x + b*src_y + c`, `dst_y = d*src_x + e*src_y + f`
        (see `apply_transform`).

    Raises:
        ValueError: Fewer than 3 points, `src`/`dst` length mismatch, or
            `src` points are collinear (no unique affine fit exists).
    """
    src, dst = _validate_point_pairs(src, dst)
    if len(src) < 3:
        raise ValueError(f"affine fit needs at least 3 point pairs, got {len(src)}")

    design = np.column_stack([src, np.ones(len(src))])  # (N, 3): [x, y, 1]
    if np.linalg.matrix_rank(design) < 3:
        raise ValueError("landmark points are collinear; cannot fit a unique affine transform")

    coeffs, *_ = np.linalg.lstsq(design, dst, rcond=None)  # (3, 2): columns [a,b,c | d,e,f]
    return coeffs.T  # (2, 3)


def fit_similarity_transform(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Least-squares similarity transform (uniform scale, rotation,
    translation only — no shear) mapping `src` points onto `dst` points, via
    the closed-form Umeyama (1991) solution, constrained to never introduce
    a reflection.

    Args:
        src: (N, 2) source points, N >= 2, not all coincident.
        dst: (N, 2) destination points, same length as `src`.

    Returns:
        A (2, 3) matrix in the same form `fit_affine_transform` returns,
        constrained to `scale * [[cos, -sin], [sin, cos]]` plus translation.

    Raises:
        ValueError: Fewer than 2 points, `src`/`dst` length mismatch, or all
            `src` points coincide (zero spread, no scale/rotation to fit).
    """
    src, dst = _validate_point_pairs(src, dst)
    if len(src) < 2:
        raise ValueError(f"similarity fit needs at least 2 point pairs, got {len(src)}")

    n = len(src)
    src_mean = src.mean(axis=0)
    dst_mean = dst.mean(axis=0)
    src_c = src - src_mean
    dst_c = dst - dst_mean

    src_var = float(np.mean(np.sum(src_c**2, axis=1)))
    if src_var < 1e-12:
        raise ValueError(
            "landmark points on the source image all coincide; cannot fit scale/rotation"
        )

    cov = dst_c.T @ src_c / n  # (2, 2)
    u, s, vt = np.linalg.svd(cov)
    sign = 1.0 if np.linalg.det(cov) >= 0 else -1.0
    correction = np.diag([1.0, sign])
    rotation = u @ correction @ vt
    scale = float(np.trace(np.diag(s) @ correction) / src_var)
    translation = dst_mean - scale * (rotation @ src_mean)

    matrix = np.zeros((2, 3))
    matrix[:, :2] = scale * rotation
    matrix[:, 2] = translation
    return matrix


def apply_transform(matrix: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Map `points` (N, 2) through a (2, 3) transform matrix (see
    `fit_affine_transform`/`fit_similarity_transform`)."""
    matrix = np.asarray(matrix, dtype=float)
    points = np.asarray(points, dtype=float)
    return points @ matrix[:, :2].T + matrix[:, 2]


def invert_transform(matrix: np.ndarray) -> np.ndarray:
    """The inverse (2, 3) transform: mapping `dst` points back to `src`.

    Raises:
        numpy.linalg.LinAlgError: `matrix`'s linear part is singular
            (degenerate fit, e.g. zero scale).
    """
    matrix = np.asarray(matrix, dtype=float)
    homogeneous = np.vstack([matrix, [0.0, 0.0, 1.0]])
    inverse = np.linalg.inv(homogeneous)
    return inverse[:2, :]


def reprojection_errors(matrix: np.ndarray, src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Per-point Euclidean distance between `dst` and `matrix` applied to
    `src` — how far each landmark's fitted position lands from where it was
    actually clicked, in `dst`'s coordinate units."""
    predicted = apply_transform(matrix, src)
    return np.linalg.norm(predicted - np.asarray(dst, dtype=float), axis=1)
