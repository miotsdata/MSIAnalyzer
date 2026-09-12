import numpy as np

from msianalyzer.core.plotting.mirror_plot_raster import render_mirror_plot_png


def test_render_mirror_plot_png_returns_valid_png_bytes():
    emp_mz = np.array([100.0, 150.0, 200.0])
    emp_int = np.array([0.5, 1.0, 0.2])
    lib_mz = np.array([100.01, 199.99])
    lib_int = np.array([0.8, 1.0])

    png = render_mirror_plot_png(emp_mz, emp_int, lib_mz, lib_int, fragment_ppm_tolerance=10.0)

    assert isinstance(png, bytes)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")  # PNG magic bytes
    assert len(png) > 100


def test_render_mirror_plot_png_handles_empty_spectra():
    # No crash, still a valid (mostly blank) PNG — an annotation with an
    # empty side shouldn't be reachable in practice (plot_ms2_annotation
    # already raises before this point), but the raster renderer itself
    # should stay robust regardless.
    png = render_mirror_plot_png(
        np.array([]), np.array([]), np.array([]), np.array([]),
        fragment_ppm_tolerance=10.0,
    )
    assert png.startswith(b"\x89PNG\r\n\x1a\n")


def test_render_mirror_plot_png_is_deterministic_for_same_input():
    emp_mz = np.array([100.0, 150.0])
    emp_int = np.array([0.5, 1.0])
    lib_mz = np.array([100.01])
    lib_int = np.array([0.8])

    png1 = render_mirror_plot_png(emp_mz, emp_int, lib_mz, lib_int, fragment_ppm_tolerance=10.0)
    png2 = render_mirror_plot_png(emp_mz, emp_int, lib_mz, lib_int, fragment_ppm_tolerance=10.0)

    assert png1 == png2
