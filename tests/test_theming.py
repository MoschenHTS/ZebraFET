"""
test_theming.py — Theme resolution, icon variants and scaled type.

The dark theme is the default, so a dark-only icon or a hardcoded color reads as
correct during development and only fails for whoever switches to light. These
tests pin the parts of that which do not need a visible window: which theme a
stored preference resolves to, which icon file each theme serves, and that the
type scale follows the application font instead of a fixed 11pt.
"""
import re

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QFontMetrics
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QLabel, QStyle,
                               QStyleOptionButton, QStyleOptionComboBox)

import src.core.utils as utils
from src.core.utils import set_icon_theme, themed_icon_name
from src.ui.theme_manager import ThemeManager
from src.ui.typography import DESIGN_BASE_PT, scaled_font, scaled_pt


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "theme.ini"), QSettings.Format.IniFormat)


@pytest.fixture(autouse=True)
def _restore_icon_theme():
    """The icon theme is process-global; put it back so tests stay independent."""
    original = utils._active_icon_theme
    yield
    set_icon_theme(original)


class TestThemeSelection:
    def test_stored_theme_is_applied(self, qapp, settings):
        manager = ThemeManager(qapp, settings)
        settings.setValue(ThemeManager.THEME_SETTING_KEY, "light")
        manager.apply_last_theme()
        assert manager.current_theme == "light"

    def test_unselectable_theme_falls_back(self, qapp, settings):
        """nini.qss ships and loads but toggle_theme cannot reach it.

        Without the fallback, a settings key naming it left the window in a state
        the theme control could not get back out of.
        """
        manager = ThemeManager(qapp, settings)
        assert "nini" in manager.themes, "fixture assumes nini.qss is still shipped"
        settings.setValue(ThemeManager.THEME_SETTING_KEY, "nini")
        manager.apply_last_theme()
        assert manager.current_theme == ThemeManager.DEFAULT_THEME

    def test_missing_theme_falls_back(self, qapp, settings):
        manager = ThemeManager(qapp, settings)
        settings.setValue(ThemeManager.THEME_SETTING_KEY, "no-such-theme")
        manager.apply_last_theme()
        assert manager.current_theme == ThemeManager.DEFAULT_THEME

    def test_toggle_only_cycles_light_and_dark(self, qapp, settings):
        manager = ThemeManager(qapp, settings)
        manager.apply_theme("dark")
        manager.toggle_theme()
        assert manager.current_theme == "light"
        manager.toggle_theme()
        assert manager.current_theme == "dark"


class TestThemedIcons:
    @pytest.mark.parametrize("theme,expected_suffix", [("dark", "-light"), ("light", "-dark")])
    @pytest.mark.parametrize("base", ["check", "eye"])
    def test_variant_matches_theme(self, theme, expected_suffix, base):
        """The suffix names the ink, not the background: dark themes need light strokes."""
        set_icon_theme(theme)
        assert themed_icon_name(base) == f"{base}{expected_suffix}.svg"

    def test_single_tone_icon_passes_through(self):
        set_icon_theme("light")
        assert themed_icon_name("house") == "house.svg"

    def test_cache_is_dropped_on_theme_change(self, qapp):
        set_icon_theme("dark")
        utils.create_icon(themed_icon_name("check"))
        assert utils._icon_cache, "nothing cached to invalidate"
        set_icon_theme("light")
        assert not utils._icon_cache

    def test_check_icon_is_a_check_not_a_cross(self):
        """check-light.svg carried the lucide 'x' path, so a finalized day was
        marked with a cross."""
        for name in ("check-light.svg", "check-dark.svg"):
            svg = open(utils.resource_path(f"resources/icons/{name}")).read()
            assert "lucide-check" in svg, name
            assert "lucide-x" not in svg, name


class TestScaledTypography:
    def test_design_size_is_unchanged_at_the_baseline(self, qapp, monkeypatch):
        monkeypatch.setattr("src.ui.typography.base_point_size", lambda: DESIGN_BASE_PT)
        assert scaled_pt(18) == pytest.approx(18.0)

    def test_sizes_track_the_application_font(self, qapp, monkeypatch):
        """A heading must stay larger than body text once the OS font grows."""
        monkeypatch.setattr("src.ui.typography.base_point_size", lambda: DESIGN_BASE_PT * 2)
        assert scaled_pt(18) == pytest.approx(36.0)
        assert scaled_pt(9) < scaled_pt(DESIGN_BASE_PT) < scaled_pt(18)

    def test_scaled_font_carries_weight_and_style(self, qapp):
        font = scaled_font(14, bold=True, italic=True)
        assert font.bold() and font.italic()
        assert font.pointSizeF() == pytest.approx(scaled_pt(14))


class TestLabelBackgrounds:
    """Plain labels must not paint a box over the page behind them.

    Both themes set a background on the blanket `QWidget` selector while the
    page container underneath uses a different color. A custom QWidget subclass
    does not paint that rule — it needs WA_StyledBackground — but QLabel does,
    so every label drew a visible rectangle: `#2d2d2d` on `#1e1e1e` in dark,
    `#ffffff` on `#f0f0f0` in light.
    """

    @staticmethod
    def _window_color(qapp, theme, obj_name=None, properties=None, factory=None):
        qapp.setStyleSheet(open(utils.resource_path(f"resources/themes/{theme}.qss")).read())
        widget = (factory or (lambda: QLabel("Analysis Day:")))()
        if obj_name:
            widget.setObjectName(obj_name)
        for key, value in (properties or {}).items():
            widget.setProperty(key, value)
        widget.show()
        qapp.processEvents()
        color = QColor(widget.palette().window().color())
        widget.close()
        widget.deleteLater()
        qapp.processEvents()
        return color

    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_plain_label_is_transparent(self, qapp, theme):
        assert self._window_color(qapp, theme).alpha() == 0

    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_checkbox_is_transparent(self, qapp, theme):
        """Neither theme had a QCheckBox rule, so Abbott's correction and the nine
        sublethal endpoints all sat in an opaque box."""
        color = self._window_color(
            qapp, theme, factory=lambda: QCheckBox("Abbott's correction")
        )
        assert color.alpha() == 0

    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_checkbox_indicator_survives_the_rule(self, qapp, theme):
        """Giving a widget class its first stylesheet rule can stop Qt drawing its
        subcontrols; the tick has to keep its box."""
        qapp.setStyleSheet(open(utils.resource_path(f"resources/themes/{theme}.qss")).read())
        box = QCheckBox("Abbott's correction")
        box.show()
        qapp.processEvents()
        option = QStyleOptionButton()
        option.initFrom(box)
        indicator = box.style().subElementRect(QStyle.SE_CheckBoxIndicator, option, box)
        assert indicator.width() > 0 and indicator.height() > 0
        box.close()

    @pytest.mark.parametrize("theme", ["light", "dark"])
    @pytest.mark.parametrize("obj_name,properties", [
        ("PlateBadge", {"status": "ok"}),
        ("SaveFeedbackLabel", {"success": True, "failure": False}),
        ("InvalidTestLabel", {}),
        ("ReadOnlyNotice", {}),
    ])
    def test_badges_keep_their_fill(self, qapp, theme, obj_name, properties):
        """ID selectors outrank the type selector, so deliberate fills survive."""
        assert self._window_color(qapp, theme, obj_name, properties).alpha() > 0

    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_page_and_label_colors_would_have_differed(self, qapp, theme):
        """Guards the premise: if the two ever match, this test set is moot."""
        qss = open(utils.resource_path(f"resources/themes/{theme}.qss")).read()
        assert "QLabel, QCheckBox, QRadioButton { background-color: transparent; }" in qss
        assert "QStackedWidget {" in qss


class TestComboBoxReservesRoomForItsArrow:
    """Combos sized to their text with no lane set aside for the drop-down arrow.

    "Bonferroni" needs 61 px and the combo came to 91 px — 2 px of border, 12 px
    of padding and the 16 px arrow, leaving nothing spare. The stylesheet already
    handles this correctly for spin boxes (`padding-right: 20px` with a matching
    button width); combos never got the equivalent.
    """

    #: Border (2) + padding-left (6) + padding-right (24). The right padding is
    #: the arrow's lane and must exceed the 20 px drop-down width.
    MIN_RESERVED_PX = 32

    #: Widest item of each combo the application builds.
    LONGEST_ITEMS = [
        "Bonferroni",                   # NOEC correction
        "Pooled (control + solvent)",   # reference control
        "Auto-select (AICc)",           # LC50 model
        "Natural Surface Water",        # water type
        "Semi-static renewal",          # test procedure
    ]

    @pytest.mark.parametrize("theme", ["light", "dark"])
    @pytest.mark.parametrize("item", LONGEST_ITEMS)
    def test_width_exceeds_the_text_by_the_reserved_lane(self, qapp, theme, item):
        qapp.setStyleSheet(open(utils.resource_path(f"resources/themes/{theme}.qss")).read())
        combo = QComboBox()
        combo.addItems([item])
        combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        combo.show()
        qapp.processEvents()

        text_width = QFontMetrics(combo.font()).horizontalAdvance(item)
        reserved = combo.sizeHint().width() - text_width
        assert reserved >= self.MIN_RESERVED_PX, (
            f"{theme}: {item!r} needs {text_width}px and the combo is only "
            f"{combo.sizeHint().width()}px — {reserved}px reserved, which does not "
            f"clear the arrow"
        )
        combo.close()

    @pytest.mark.parametrize("theme", ["light", "dark", "nini"])
    def test_right_padding_covers_the_drop_down_width(self, qapp, theme):
        """The lane has to be at least as wide as the arrow that sits in it."""
        qss = open(utils.resource_path(f"resources/themes/{theme}.qss")).read()
        padding = int(re.search(r"QComboBox\s*\{[^}]*padding-right:\s*(\d+)px", qss).group(1))
        drop_down = int(re.search(r"QComboBox::drop-down\s*\{[^}]*width:\s*(\d+)px", qss).group(1))
        assert padding >= drop_down


class TestThemedObjectNames:
    """Object names that no QSS rule matched rendered identically in every state."""

    def _polished(self, qapp, theme, obj_name, properties):
        qapp.setStyleSheet(open(utils.resource_path(f"resources/themes/{theme}.qss")).read())
        label = QLabel("x")
        label.setObjectName(obj_name)
        for key, value in properties.items():
            label.setProperty(key, value)
        label.show()
        qapp.processEvents()
        color = label.palette().windowText().color().name()
        label.close()
        return color

    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_save_feedback_distinguishes_success_from_failure(self, qapp, theme):
        """This label is the only report that a flush to disk did not happen."""
        success = self._polished(qapp, theme, "SaveFeedbackLabel",
                                 {"success": True, "failure": False})
        failure = self._polished(qapp, theme, "SaveFeedbackLabel",
                                 {"success": False, "failure": True})
        assert success != failure

    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_wizard_validation_label_is_styled(self, qapp, theme):
        valid = self._polished(qapp, theme, "ValidationLabel", {"valid": True})
        invalid = self._polished(qapp, theme, "ValidationLabel", {"valid": False})
        assert valid != invalid

    @staticmethod
    def _well_palette(qapp, theme):
        """The painted colors a fresh WellWidget picks up under *theme*.

        The values are copied out and the widget destroyed before returning:
        applying the next stylesheet re-polishes every live widget, so holding
        on to one would report the last theme applied rather than its own.
        """
        from src.ui.components import WellWidget

        qapp.setStyleSheet(open(utils.resource_path(f"resources/themes/{theme}.qss")).read())
        well = WellWidget("A1", 1, 1)
        well.show()
        qapp.processEvents()
        palette = {
            "unassignedFill": QColor(well.unassignedFill),
            "hoverTint": QColor(well.hoverTint),
            "selectionRing": QColor(well.selectionRing),
        }
        well.close()
        well.deleteLater()
        qapp.processEvents()
        return palette

    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_well_palette_comes_from_the_theme(self, qapp, theme):
        """WellWidget paints itself, so its colors arrive as Qt properties."""
        palette = self._well_palette(qapp, theme)
        assert palette["unassignedFill"].alpha() > 0
        assert palette["selectionRing"].isValid()
        assert palette["hoverTint"].alpha() > 0

    def test_well_palette_differs_between_themes(self, qapp):
        light = self._well_palette(qapp, "light")
        dark = self._well_palette(qapp, "dark")
        assert light["selectionRing"].name() != dark["selectionRing"].name()
        assert light["hoverTint"].getRgb() != dark["hoverTint"].getRgb()
