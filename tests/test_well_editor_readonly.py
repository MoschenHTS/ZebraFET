"""
test_well_editor_readonly.py — Inspecting a finalized day must not alter it.

Finalizing is required to reach the next day, so every day but the current one is
locked. The four OECD lethal endpoints are held only in the Well Editor, and the
editor previously refused to open on a locked day, leaving Reopen Day for Editing
— which cascades to later days and discards their derived rows — as the only way
to read them back.

The editor now opens in read-only form instead. These tests pin the property the
design rests on: populating the panel performs no write. That is not obvious from
the code, because blockSignals() on a QGroupBox does not suppress its children's
signals, so setChecked() during population does reach the save handler; what stops
it is the guard in _save_changes.
"""
import pytest
from PySide6.QtWidgets import QApplication

from src.core.constants import STATUS_DEAD_EMBRYO, STATUS_LIVE_EMBRYO
from src.core.project_manager import ProjectManager
from src.ui.widgets.well_editor_widget import WellEditorWidget

LETHAL = "Lack of heartbeat"
SUBLETHAL = "Yolk sac oedema"
NOTES = "scored at 26.1 C"

INITIAL_DATA = {"project_name": "EditorRO", "num_days": 4, "num_plates": 1}
CONCENTRATIONS = [
    {"id": "Co1", "type": "Control", "value": 0.0, "replicates": 1, "wells": 1},
    {"id": "C1", "type": "Substrate", "value": 1.0, "replicates": 1, "wells": 1},
]
LAYOUT = {1: {"A1": "Co1", "A2": "C1"}}


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch):
    """Fail on an unexpected modal instead of blocking the run forever.

    The editor raises a confirmation dialog on an impossible state transition.
    Under a headless test there is nothing to dismiss it, so a stray one hangs
    the suite with no indication of which test is responsible.
    """
    def _fail(*args, **kwargs):
        raise AssertionError(f"unexpected modal dialog: {args[2] if len(args) > 2 else args}")

    from PySide6.QtWidgets import QMessageBox
    for name in ("warning", "information", "critical", "question"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(_fail))


@pytest.fixture
def manager(tmp_path):
    m = ProjectManager.create_new(str(tmp_path / "EditorRO"), INITIAL_DATA)
    m.set_concentrations(CONCENTRATIONS, required_embryos=2, required_plates=1)
    m.commit_plate_layout(LAYOUT)
    m.save_well_data(
        day=1, plate_index=1, well_id="A1",
        status=STATUS_DEAD_EMBRYO,
        sublethal_conditions=[SUBLETHAL],
        lethal_conditions=[LETHAL],
        notes=NOTES,
    )
    yield m
    m.close()


@pytest.fixture
def editor(qapp, manager):
    widget = WellEditorWidget(manager)
    yield widget
    widget.deleteLater()


def stored(manager):
    return manager.get_well_data(1, 1, "A1")


class TestReadOnlyPerformsNoWrite:
    def test_opening_a_well_leaves_the_record_untouched(self, editor, manager):
        before = stored(manager)
        editor.set_active_well("A1", 1, 1, read_only=True)
        assert stored(manager) == before

    def test_save_changes_is_a_no_op_when_called_directly(self, editor, manager):
        editor.set_active_well("A1", 1, 1, read_only=True)
        before = stored(manager)
        editor._save_changes()
        assert stored(manager) == before

    def test_status_hotkeys_are_ignored(self, editor, manager):
        """handle_key_press must not reassign status while read-only."""
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QKeyEvent

        editor.set_active_well("A1", 1, 1, read_only=True)
        before = stored(manager)
        editor.handle_key_press(
            QKeyEvent(QKeyEvent.KeyPress, Qt.Key_1, Qt.NoModifier)
        )
        assert stored(manager)["status"] == before["status"] == STATUS_DEAD_EMBRYO

    def test_a_dead_well_is_not_silently_revived(self, editor, manager):
        """The status buttons are populated, which must not re-save the well."""
        editor.set_active_well("A1", 1, 1, read_only=True)
        assert stored(manager)["status"] == STATUS_DEAD_EMBRYO


class TestReadOnlyStillShowsEverything:
    def test_recorded_data_is_populated(self, editor):
        editor.set_active_well("A1", 1, 1, read_only=True)
        assert editor.status_buttons[STATUS_DEAD_EMBRYO].isChecked()
        assert editor.notes_edit.toPlainText() == NOTES

    def test_lethal_endpoints_are_readable(self, editor):
        """These exist nowhere else in the interface."""
        editor.set_active_well("A1", 1, 1, read_only=True)
        checked = [cb.text() for cb in editor.lethal_checkboxes if cb.isChecked()]
        assert LETHAL in checked

    def test_sublethal_conditions_are_readable(self, editor):
        editor.set_active_well("A1", 1, 1, read_only=True)
        checked = [cb.text() for cb in editor.sublethal_checkboxes if cb.isChecked()]
        assert SUBLETHAL in checked


class TestReadOnlyPresentation:
    def test_input_groups_are_disabled(self, editor):
        editor.set_active_well("A1", 1, 1, read_only=True)
        assert not editor.status_group.isEnabled()
        assert not editor.lethal_endpoints_group.isEnabled()
        assert not editor.sublethal_main_layout.parentWidget().isEnabled()

    def test_notes_remain_scrollable_and_selectable(self, editor):
        """Read-only rather than disabled, so long notes can be read and copied."""
        editor.set_active_well("A1", 1, 1, read_only=True)
        assert editor.info_group.isEnabled()
        assert editor.notes_edit.isReadOnly()

    def test_the_panel_says_it_is_viewing(self, editor):
        editor.set_active_well("A1", 1, 1, read_only=True)
        assert editor.info_label.text().startswith("Viewing:")
        assert editor.read_only_label.isVisibleTo(editor)
        assert "finalized" in editor.read_only_label.text()


class TestModeTransitions:
    def test_editing_is_restored_when_pointed_at_an_open_day(self, editor, manager):
        # A2 rather than A1: A1 is seeded dead on day 1, and populating an
        # editable panel for day 2 would re-save it as live, raising the
        # impossible-transition dialog. That is a property of the fixture, not
        # of read-only mode, and it is exercised separately below.
        editor.set_active_well("A2", 1, 1, read_only=True)
        editor.set_active_well("A2", 1, 2, read_only=False)
        assert editor.read_only is False
        assert editor.status_group.isEnabled()
        assert not editor.notes_edit.isReadOnly()
        assert editor.info_label.text().startswith("Editing:")
        assert not editor.read_only_label.isVisibleTo(editor)

    def test_editing_an_open_day_still_writes(self, editor, manager):
        """The guard must not leak into normal editing."""
        editor.set_active_well("A2", 1, 2, read_only=False)
        editor.status_buttons[STATUS_DEAD_EMBRYO].setChecked(True)
        assert manager.get_well_data(2, 1, "A2")["status"] == STATUS_DEAD_EMBRYO

    def test_clearing_the_panel_hides_the_notice(self, editor):
        editor.set_active_well("A1", 1, 1, read_only=True)
        editor.set_active_well(None, None, None)
        assert not editor.read_only_label.isVisibleTo(editor)
        assert editor.info_label.text() == "Select a well to edit"


class TestOpeningAWellDoesNotClaimAuthorship:
    """Viewing a well must not reclassify it as operator-entered.

    Rows written when a day is finalized carry auto_filled=1, and reopen_day
    discards those while preserving hand-entered ones. Populating the editor
    emitted the child widgets' signals into _save_changes, which rewrote the row
    with auto_filled=0, so merely opening a well made a carried-forward
    observation survive a reopen cascade as though it had been scored by hand.
    """

    def _reopened_day_with_derived_rows(self, manager):
        manager.finalize_day(1)
        manager.finalize_day(2)
        manager.reopen_day(2)
        return {r["well_id"]: r["auto_filled"] for r in manager._conn.execute(
            "SELECT well_id, auto_filled FROM well_observations WHERE day=2")}

    def test_opening_preserves_the_derived_flag(self, editor, manager):
        before = self._reopened_day_with_derived_rows(manager)
        assert before["A2"] == 1, "fixture did not produce a derived row"

        editor.set_active_well("A2", 1, 2)

        after = {r["well_id"]: r["auto_filled"] for r in manager._conn.execute(
            "SELECT well_id, auto_filled FROM well_observations WHERE day=2")}
        assert after["A2"] == 1

    def test_an_actual_edit_still_claims_authorship(self, editor, manager):
        """The suppression must not stop a real edit from being recorded."""
        self._reopened_day_with_derived_rows(manager)
        editor.set_active_well("A2", 1, 2)
        editor.status_buttons[STATUS_DEAD_EMBRYO].setChecked(True)

        row = manager._conn.execute(
            "SELECT auto_filled FROM well_observations WHERE day=2 AND well_id='A2'"
        ).fetchone()
        assert row["auto_filled"] == 0
        assert manager.get_well_data(2, 1, "A2")["status"] == STATUS_DEAD_EMBRYO


class TestLoadingFlagIsAlwaysCleared:
    """A failed load must not leave the editor silently unable to save.

    _loading suppresses every write in _save_changes. If an exception escaped
    load_well_data() while the flag was set, the panel would keep looking and
    behaving normally while discarding the operator's every edit — a silent
    data-loss mode. The flag is therefore cleared in a finally block.
    """

    def test_flag_is_clear_after_a_normal_load(self, editor):
        editor.set_active_well("A1", 1, 1)
        assert editor._loading is False

    def test_flag_is_cleared_even_when_population_raises(self, editor, manager, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("simulated widget failure during population")

        # Fail partway through populating, after _loading has been set.
        monkeypatch.setattr(editor.notes_edit, "setText", boom)

        with pytest.raises(RuntimeError):
            editor.set_active_well("A1", 1, 1)

        assert editor._loading is False, "_loading stuck: editor would silently drop all edits"

    def test_editor_still_saves_after_a_failed_load(self, editor, manager, monkeypatch):
        """The real consequence: recovery must restore normal saving."""
        def boom(*args, **kwargs):
            raise RuntimeError("simulated failure")

        monkeypatch.setattr(editor.notes_edit, "setText", boom)
        with pytest.raises(RuntimeError):
            editor.set_active_well("A1", 1, 1)
        monkeypatch.undo()

        # A subsequent, healthy edit on an open day must still persist.
        editor.set_active_well("A2", 1, 2)
        editor.status_buttons[STATUS_DEAD_EMBRYO].setChecked(True)
        assert manager.get_well_data(2, 1, "A2")["status"] == STATUS_DEAD_EMBRYO
