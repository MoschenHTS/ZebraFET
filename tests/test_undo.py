"""
test_undo.py — Scoring a well can be taken back.

Every edit is written the moment it is made, which is the right storage model but
left no way back from a misclick. What these tests pin is the granularity: the
editor saves on every checkbox toggle and on a debounce timer while notes are
typed, so without merging one visit to a well would cost a dozen presses of
Ctrl+Z to undo.
"""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QUndoStack

from src.core.project_manager import ProjectManager
from src.ui.commands import PlateLayoutCommand, WellEditCommand, well_state

CONCENTRATIONS = [
    {"id": "ctrl", "type": "Control", "value": 0.0, "replicates": 1, "wells": 2, "per_plate": True},
    {"id": "s1", "type": "Substrate", "value": 1.0, "replicates": 1, "wells": 2, "per_plate": True},
]


@pytest.fixture
def project(tmp_path):
    m = ProjectManager.create_new(
        str(tmp_path / "Undo"), {"project_name": "Undo", "num_days": 2, "num_plates": 2}
    )
    m.set_concentrations(CONCENTRATIONS, required_embryos=4, required_plates=2)
    m.commit_plate_layout({"1": {"A1": "ctrl", "A2": "s1"}, "2": {"A1": "ctrl"}})
    m.save_well_data(1, 1, "A1", "Live Embryo", [], [], "")
    yield m
    m.close()


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def stack():
    return QUndoStack()


def _edit(project, status, sublethal=(), lethal=(), notes="", well="A1", day=1, plate=1):
    """Apply an edit the way the editor does, and build its command."""
    before = project.get_well_data(day, plate, well)
    after = {
        "status": status, "sublethal_conditions": list(sublethal),
        "lethal_conditions": list(lethal), "notes": notes,
    }
    project.save_well_data(day, plate, well, status, list(sublethal), list(lethal), notes, 0)
    return WellEditCommand(project, day, plate, well, before, after, lambda *a: None)


class TestWellState:
    def test_ignores_condition_ordering(self):
        a = {"status": "Dead Embryo", "sublethal_conditions": ["b", "a"], "lethal_conditions": [], "notes": ""}
        b = {"status": "Dead Embryo", "sublethal_conditions": ["a", "b"], "lethal_conditions": [], "notes": ""}
        assert well_state(a) == well_state(b)

    def test_distinguishes_a_real_change(self):
        a = {"status": "Live Embryo", "sublethal_conditions": [], "lethal_conditions": [], "notes": ""}
        b = {"status": "Dead Embryo", "sublethal_conditions": [], "lethal_conditions": [], "notes": ""}
        assert well_state(a) != well_state(b)

    def test_missing_fields_normalize(self):
        assert well_state({})["notes"] == ""
        assert well_state({})["sublethal_conditions"] == []


class TestSingleEdit:
    def test_push_does_not_reapply(self, project, stack):
        """The editor already wrote it; pushing must not write it a second time."""
        command = _edit(project, "Dead Embryo")
        stack.push(command)
        assert project.get_well_data(1, 1, "A1")["status"] == "Dead Embryo"

    def test_undo_restores_the_previous_status(self, project, stack):
        stack.push(_edit(project, "Dead Embryo"))
        stack.undo()
        assert project.get_well_data(1, 1, "A1")["status"] == "Live Embryo"

    def test_redo_reapplies(self, project, stack):
        stack.push(_edit(project, "Dead Embryo"))
        stack.undo()
        stack.redo()
        assert project.get_well_data(1, 1, "A1")["status"] == "Dead Embryo"

    def test_conditions_and_notes_are_restored(self, project, stack):
        stack.push(_edit(project, "Dead Embryo",
                         sublethal=["Yolk sac oedema"],
                         lethal=["Lack of heartbeat"], notes="coagulated"))
        stack.undo()
        data = project.get_well_data(1, 1, "A1")
        assert data["sublethal_conditions"] == []
        assert data["lethal_conditions"] == []
        assert data["notes"] == ""

    def test_undo_notifies_the_interface(self, project):
        seen = []
        before = project.get_well_data(1, 1, "A1")
        after = {"status": "Dead Embryo", "sublethal_conditions": [],
                 "lethal_conditions": [], "notes": ""}
        project.save_well_data(1, 1, "A1", "Dead Embryo", [], [], "", 0)
        command = WellEditCommand(project, 1, 1, "A1", before, after,
                                  lambda *args: seen.append(args))
        stack = QUndoStack()
        stack.push(command)
        stack.undo()
        assert seen == [(1, 1, "A1")]


class TestMerging:
    def test_a_run_of_edits_on_one_well_is_one_step(self, project, stack):
        """One well visit costs one Ctrl+Z, not one per checkbox."""
        for notes in ("a", "ab", "abc", "abcd"):
            stack.push(_edit(project, "Dead Embryo", notes=notes))
        assert stack.count() == 1

    def test_undoing_that_run_returns_to_the_start(self, project, stack):
        for notes in ("a", "ab", "abc"):
            stack.push(_edit(project, "Dead Embryo", notes=notes))
        stack.undo()
        data = project.get_well_data(1, 1, "A1")
        assert data["status"] == "Live Embryo"
        assert data["notes"] == ""

    def test_redo_after_a_merged_run_restores_the_final_state(self, project, stack):
        for notes in ("a", "ab", "abc"):
            stack.push(_edit(project, "Dead Embryo", notes=notes))
        stack.undo()
        stack.redo()
        assert project.get_well_data(1, 1, "A1")["notes"] == "abc"

    def test_a_different_well_starts_a_new_step(self, project, stack):
        stack.push(_edit(project, "Dead Embryo", well="A1"))
        stack.push(_edit(project, "Dead Embryo", well="A2"))
        assert stack.count() == 2

    def test_the_same_well_on_another_day_is_separate(self, project, stack):
        stack.push(_edit(project, "Dead Embryo", day=1))
        stack.push(_edit(project, "Dead Embryo", day=2))
        assert stack.count() == 2

    def test_the_same_well_on_another_plate_is_separate(self, project, stack):
        stack.push(_edit(project, "Dead Embryo", plate=1))
        stack.push(_edit(project, "Dead Embryo", plate=2))
        assert stack.count() == 2

    def test_returning_to_a_well_after_another_does_not_merge(self, project, stack):
        """Merging only ever applies at the top of the stack."""
        stack.push(_edit(project, "Dead Embryo", well="A1"))
        stack.push(_edit(project, "Dead Embryo", well="A2"))
        stack.push(_edit(project, "Live Hatched", well="A1"))
        assert stack.count() == 3


class TestPlateLayoutCommand:
    class _Page:
        def __init__(self):
            self.temp_layout = {}
            self.applied = []

        def apply_layout_snapshot(self, layout):
            self.temp_layout = layout
            self.applied.append(layout)

    def test_push_does_not_reapply(self):
        page = self._Page()
        stack = QUndoStack()
        stack.push(PlateLayoutCommand(page, {"1": {"A1": "ctrl"}}, {"1": {}}, "clear plate 1"))
        assert page.applied == []

    def test_undo_restores_the_buffer(self):
        page = self._Page()
        stack = QUndoStack()
        stack.push(PlateLayoutCommand(page, {"1": {"A1": "ctrl"}}, {"1": {}}, "clear plate 1"))
        stack.undo()
        assert page.temp_layout == {"1": {"A1": "ctrl"}}

    def test_redo_reapplies(self):
        page = self._Page()
        stack = QUndoStack()
        stack.push(PlateLayoutCommand(page, {"1": {"A1": "ctrl"}}, {"1": {}}, "clear plate 1"))
        stack.undo()
        stack.redo()
        assert page.temp_layout == {"1": {}}

    def test_layout_changes_never_merge(self):
        """Each is a deliberate action on a whole plate."""
        page = self._Page()
        stack = QUndoStack()
        stack.push(PlateLayoutCommand(page, {"1": {"A1": "ctrl"}}, {"1": {}}, "clear plate 1"))
        stack.push(PlateLayoutCommand(page, {"1": {}}, {"2": {}}, "clear plate 2"))
        assert stack.count() == 2


class TestEditorIntegration:
    """The signal the undo stack is fed from, emitted by the real editor."""

    @pytest.fixture
    def editor(self, project):
        from PySide6.QtWidgets import QApplication
        from src.ui.widgets.well_editor_widget import WellEditorWidget

        QApplication.instance() or QApplication([])
        widget = WellEditorWidget(project)
        yield widget
        widget.deleteLater()

    def _captured(self, editor):
        seen = []
        editor.edit_committed.connect(seen.append)
        return seen

    def test_a_real_change_is_emitted(self, editor, project):
        editor.set_active_well("A1", 1, 1)
        seen = self._captured(editor)
        editor.status_buttons["Dead Embryo"].setChecked(True)
        assert seen and seen[-1]["well"] == "A1"
        assert seen[-1]["after"]["status"] == "Dead Embryo"
        assert seen[-1]["before"]["status"] == "Live Embryo"

    def test_reselecting_the_same_status_emits_nothing(self, editor, project):
        """An edit that changed nothing must not occupy a slot on the stack."""
        editor.set_active_well("A1", 1, 1)
        seen = self._captured(editor)
        editor.force_save()
        assert seen == []

    def test_opening_a_well_emits_nothing(self, editor, project):
        seen = self._captured(editor)
        editor.set_active_well("A1", 1, 1)
        assert seen == []

    def test_a_finalized_day_emits_nothing(self, editor, project):
        """Read-only days are locked; nothing from them may reach the stack."""
        project.finalize_day(1)
        editor.set_active_well("A1", 1, 1, read_only=True)
        seen = self._captured(editor)
        editor.force_save()
        assert seen == []


class TestUndoHistorySurvivesSameProjectReloads:
    """Rebuilding the pages must not silently discard the operator's history.

    The pages are rebuilt whenever the project changes, but also when the
    concentration plan or the project settings are edited. Clearing the stack on
    every rebuild meant that changing the day count threw away an afternoon of
    scoring with no warning, so the clear is now tied to the project identity
    rather than to the rebuild.
    """

    def _window(self, qapp, project):
        from PySide6.QtCore import QSettings
        from src.ui.theme_manager import ThemeManager
        from src.ui.main_window import MainWindow
        settings = QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                             "ZebraFET", "ZebraFET Test")
        window = MainWindow(settings, ThemeManager(qapp, settings))
        window.project_manager = project
        return window

    def test_reloading_the_same_project_keeps_the_stack(self, qapp, project, stack):
        window = self._window(qapp, project)
        window._undo_stack_project = project.db_path
        window.undo_stack.push(
            WellEditCommand(project, 1, 1, "A1",
                            {"status": "Live Embryo"}, {"status": "Dead Embryo"},
                            lambda *a: None)
        )
        assert window.undo_stack.count() == 1
        window._reload_project_pages(is_new_project=False)
        assert window.undo_stack.count() == 1, "same-project reload discarded the history"
        window.close()

    def test_opening_a_different_project_clears_the_stack(self, qapp, project, tmp_path):
        window = self._window(qapp, project)
        window._undo_stack_project = "/some/other/Project.db"
        window.undo_stack.push(
            WellEditCommand(project, 1, 1, "A1",
                            {"status": "Live Embryo"}, {"status": "Dead Embryo"},
                            lambda *a: None)
        )
        window._reload_project_pages(is_new_project=False)
        assert window.undo_stack.count() == 0, "history from another project survived"
        window.close()
