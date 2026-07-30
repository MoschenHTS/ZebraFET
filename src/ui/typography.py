"""
typography.py — Font sizes expressed relative to the application font.

The QSS used to pin 11pt on QWidget, so every size chosen in Python could be an
absolute point size and still look consistent. Now that the base font comes from
the system, absolute sizes no longer hold their relationship to body text: at a
large system font setting an 18pt heading would be smaller than the paragraph
under it. These helpers restate the original design sizes as ratios of the
11pt baseline they were chosen against.
"""
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

#: The base size the interface was laid out at, and the divisor that turns the
#: sizes originally written into the widgets back into ratios.
DESIGN_BASE_PT = 11.0


def base_point_size() -> float:
    """The application font's point size, falling back to the design baseline."""
    app = QApplication.instance()
    if app is None:
        return DESIGN_BASE_PT
    size = app.font().pointSizeF()
    return size if size > 0 else DESIGN_BASE_PT


def scaled_pt(design_pt: float) -> float:
    """Convert a size chosen against the 11pt baseline into a scaled size."""
    return design_pt * (base_point_size() / DESIGN_BASE_PT)


def scaled_font(design_pt: float, bold: bool = False, italic: bool = False,
                base: QFont = None) -> QFont:
    """Return *base* (or the application font) resized to a scaled design size."""
    font = QFont(base) if base is not None else QApplication.font()
    font.setPointSizeF(scaled_pt(design_pt))
    font.setBold(bold)
    font.setItalic(italic)
    return font
