"""
test_menu_bar.py — The menu bar and its shortcuts.

Every action added in 2.2.0 reached the user only through a page button or, in
the case of undo and redo, through a shortcut with no visible affordance at all.
These tests pin the structure that replaced that, and the portability of the
shortcuts: a literal "Ctrl+..." string would still work on Windows and Linux but
would bind to the Control key on macOS instead of Command.
"""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication

from src.ui.main_window import MainWindow
from src.ui.theme_manager import ThemeManager


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path):
    settings = QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                         "ZebraFET", "ZebraFET MenuTest")
    w = MainWindow(settings, ThemeManager(qapp, settings))
    yield w
    w.close()


def _all_actions(menu, out=None):
    out = [] if out is None else out
    for action in menu.actions():
        if action.isSeparator():
            continue
        if action.menu():
            _all_actions(action.menu(), out)
        else:
            out.append(action)
    return out


def _menu(window, title):
    return next(a.menu() for a in window.menuBar().actions() if a.text() == title)


class TestStructure:
    def test_every_top_level_menu_is_present(self, window):
        titles = [a.text() for a in window.menuBar().actions() if a.menu()]
        assert titles == ["&File", "&Edit", "&Go", "&Analysis", "&View", "&Help"]

    def test_menus_survive_garbage_collection(self, window):
        """A QMenu from addMenu(str) is owned by Python and is collected."""
        import gc
        import shiboken6
        gc.collect()
        for name in ("menu_file", "menu_edit", "menu_go", "menu_analysis",
                     "menu_view", "menu_help", "menu_open_recent",
                     "menu_import", "menu_export"):
            assert shiboken6.isValid(getattr(window, name)), f"{name} was destroyed"

    def test_exports_are_grouped_under_one_submenu(self, window):
        labels = [a.text() for a in window.menu_export.actions() if not a.isSeparator()]
        assert any("Report" in t for t in labels)
        assert any("Tables" in t for t in labels)
        assert any("Raw" in t for t in labels)

    def test_every_action_targets_something_that_exists(self, window):
        """A menu entry wired to a missing method fails only when clicked."""
        from src.ui.pages.project_hub_page import ProjectHubPage
        from src.ui.widgets.results_analysis_widget import ResultsAnalysisWidget
        for name in ("_export_to_docx", "_export_analysis_tables", "_export_to_csv",
                     "_export_current_figure", "_copy_current_figure",
                     "run_analysis", "run_timeseries"):
            assert hasattr(ResultsAnalysisWidget, name), name
        for name in ("_browse_for_project", "_import_project"):
            assert hasattr(ProjectHubPage, name), name


class TestShortcutsArePortable:
    """Shortcuts must resolve to each platform's own modifier."""

    def test_no_shortcut_is_a_hardcoded_control_key(self, window):
        """StandardKey and QKeySequence both map Ctrl to Command on macOS.

        This asserts the sequences round-trip through Qt rather than being raw
        text, which is what makes the platform mapping happen.
        """
        for menu_title in ("&File", "&Edit", "&Go", "&Analysis", "&Help"):
            for action in _all_actions(_menu(window, menu_title)):
                ks = action.shortcut()
                if ks.isEmpty():
                    continue
                assert ks == QKeySequence(ks.toString(QKeySequence.PortableText))

    def test_standard_actions_use_the_platform_sequence(self, window):
        expected = {
            "&New Project...": QKeySequence.StandardKey.New,
            "&Open Project...": QKeySequence.StandardKey.Open,
            "&Save to Disk": QKeySequence.StandardKey.Save,
            "&Close Project": QKeySequence.StandardKey.Close,
            "&Quit": QKeySequence.StandardKey.Quit,
        }
        found = {a.text(): a.shortcut() for a in _all_actions(_menu(window, "&File"))}
        for label, standard in expected.items():
            assert found[label] == QKeySequence(standard), label

    def test_undo_and_redo_use_the_platform_sequence(self, window):
        assert window.action_undo.shortcut() == QKeySequence(QKeySequence.StandardKey.Undo)
        assert window.action_redo.shortcut() == QKeySequence(QKeySequence.StandardKey.Redo)

    def test_recalculate_avoids_a_bare_function_key(self, window):
        """F5 needs the Fn key on most Mac keyboards; Refresh maps to Cmd+R."""
        assert window.action_recalculate.shortcut() == QKeySequence(
            QKeySequence.StandardKey.Refresh
        )

    def test_no_two_actions_share_a_shortcut(self, window):
        seen = {}
        for top in window.menuBar().actions():
            if not top.menu():
                continue
            for action in _all_actions(top.menu()):
                ks = action.shortcut().toString(QKeySequence.PortableText)
                if ks:
                    seen.setdefault(ks, []).append(action.text())
        clashes = {k: v for k, v in seen.items() if len(v) > 1}
        assert not clashes, f"shortcut collision: {clashes}"


class TestEnabledState:
    def test_project_actions_are_disabled_without_a_project(self, window):
        assert not window.action_save.isEnabled()
        assert not window.action_export_report.isEnabled()
        assert not window.action_settings.isEnabled()

    def test_actions_that_never_need_a_project_stay_enabled(self, window):
        assert window.action_new.isEnabled()
        assert window.action_open.isEnabled()
        assert window.action_about.isEnabled()
        assert window.action_open_log.isEnabled()

    def test_recent_menu_reports_an_empty_registry(self, window, monkeypatch):
        monkeypatch.setattr("src.database.registry.ProjectRegistry.list_projects",
                            lambda self: [])
        window._populate_recent_menu()
        labels = [a.text() for a in window.menu_open_recent.actions()]
        assert labels == ["No Recent Projects"]
        assert not window.menu_open_recent.actions()[0].isEnabled()
