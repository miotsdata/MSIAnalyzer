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
