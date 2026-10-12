from PySide6.QtGui import QGuiApplication

from msianalyzer.gui.main import apply_font_scale


def _effective_size(font):
    """`pointSizeF()`, or `pixelSize()` when the font is pixel-sized
    instead (`pointSizeF()` returns the sentinel `-1` in that case)."""
    return font.pointSizeF() if font.pointSizeF() > 0 else font.pixelSize()


def test_apply_font_scale_shrinks_default_font(application):
    original = QGuiApplication.font()
    try:
        original_size = _effective_size(original)
        apply_font_scale(0.9)
        scaled_size = _effective_size(QGuiApplication.font())

        assert scaled_size < original_size
    finally:
        QGuiApplication.setFont(original)


def test_apply_font_scale_handles_pixel_sized_default_font(application):
    # `pointSizeF()` returns the sentinel `-1` (not a real size) when the
    # platform default is pixel-sized instead — scaling that blindly
    # passes a negative size to `setPointSizeF` (a no-op, with a Qt
    # warning) rather than actually shrinking the font.
    from PySide6.QtGui import QFont

    original = QGuiApplication.font()
    try:
        pixel_font = QFont(original)
        pixel_font.setPixelSize(20)
        QGuiApplication.setFont(pixel_font)
        assert QGuiApplication.font().pointSizeF() == -1

        apply_font_scale(0.9)

        assert QGuiApplication.font().pixelSize() == 18
    finally:
        QGuiApplication.setFont(original)


def test_gui_startup_does_not_import_heavy_scientific_libraries():
    # Startup speed guard: these cost seconds to import and are only
    # needed once a dataset is opened or a Run starts, so they're
    # deferred (function-level imports, or core.utils.lazy_import for
    # anndata). A fresh interpreter, since this test session has long
    # since imported all of them.
    import os
    import subprocess
    import sys
    from pathlib import Path

    import msianalyzer

    code = (
        "import sys, msianalyzer.gui.main\n"
        "heavy = ['squidpy', 'scanpy', 'dask', 'numba', 'scipy.stats', 'scipy.signal', 'matplotlib.pyplot']\n"
        "loaded = [m for m in heavy if m in sys.modules]\n"
        # type(), not isinstance(): isinstance reads __class__, and any
        # attribute access is exactly what loads a lazy module.
        "if type(sys.modules.get('anndata')).__name__ not in ('NoneType', '_LazyModule'):\n"
        "    loaded.append('anndata')\n"
        "print(','.join(loaded))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        # The same source tree this session imports, whatever the cwd is.
        env={**os.environ, "PYTHONPATH": str(Path(msianalyzer.__file__).parents[1])},
    ).stdout.strip()

    assert out == "", f"imported at GUI startup: {out}"
