# well_editor_widget.py
import logging
from typing import Dict, List
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
                             QPushButton, QButtonGroup, QLineEdit, QCheckBox,
                             QLabel, QTextEdit, QScrollArea, QFrame, QMessageBox)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QKeyEvent

from src.core.project_manager import ProjectManager
from src.ui.commands import well_state
from src.core.constants import (
    STATUS_LIVE_EMBRYO, STATUS_DEAD_EMBRYO, STATUS_LIVE_HATCHED,
    STATUS_DEAD_HATCHED, STATUS_ABSENT,
    IRREVERSIBLE_STATUSES, HATCHED_STATUSES, DEAD_STATUSES, LIVE_STATUSES,
    LETHAL_ENDPOINTS, NON_LETHAL_ENDPOINTS,
)
from src.ui.typography import scaled_pt

log = logging.getLogger(__name__)

class CustomTextEdit(QTextEdit):
    """
    A custom QTextEdit that emits a signal when the Enter key is pressed
    without the Shift modifier, to signal the end of editing.
    """
    enter_pressed = Signal()

    def keyPressEvent(self, event: QKeyEvent):
        """
        Overrides the default key press event to capture the Enter key.

        Args:
            event (QKeyEvent): The key event.
        """
        if event.key() == Qt.Key_Return and not (event.modifiers() & Qt.ShiftModifier):
            self.enter_pressed.emit()
        else:
            super().keyPressEvent(event)

class WellEditorWidget(QWidget):
    """
    A widget that serves as the main editor panel for a single well.
    It displays the well's current data and allows the user to record its
    status, lethal/sublethal conditions, and notes for a specific day.
    """
    data_changed = Signal(str, int, int, str) # well_id, plate_index, day_index, new_status
    #: Carries the well's state either side of an edit, so it can be undone.
    #: Emitted only when the edit actually changed something.
    edit_committed = Signal(dict)
    interaction_occurred = Signal()

    # Status constants — sourced from constants.py
    STATUS_EMBRYO_ALIVE = STATUS_LIVE_EMBRYO
    STATUS_EMBRYO_DEAD  = STATUS_DEAD_EMBRYO
    STATUS_HATCHED_ALIVE = STATUS_LIVE_HATCHED
    STATUS_HATCHED_DEAD  = STATUS_DEAD_HATCHED
    STATUS_ABSENT        = STATUS_ABSENT  # re-export for backward compat

    IRREVERSIBLE_STATES = IRREVERSIBLE_STATUSES
    HATCHED_STATES      = HATCHED_STATUSES

    PREDEFINED_SUBLETHAL = NON_LETHAL_ENDPOINTS
    LETHAL_ENDPOINTS     = LETHAL_ENDPOINTS

    def __init__(self, manager: ProjectManager, parent: QWidget = None):
        """
        Initializes the WellEditorWidget.

        Args:
            manager (ProjectManager): The project data manager instance.
            parent (QWidget, optional): The parent widget. Defaults to None.
        """
        super().__init__(parent)
        self.manager = manager
        self.well_id = None
        self.plate_index = None
        self.day_index = None
        self.read_only = False
        self._loading = False

        self.status_buttons: Dict[str, QPushButton] = {}
        self.sublethal_checkboxes: List[QCheckBox] = []
        self.lethal_checkboxes: List[QCheckBox] = []
        self._shown_warnings: set = set()

        self._notes_save_timer = QTimer(self)
        self._notes_save_timer.setSingleShot(True)
        self._notes_save_timer.setInterval(400)
        self._notes_save_timer.timeout.connect(self._save_changes)

        self._init_ui()
        self.set_active_well(None, None, None)

    def _init_ui(self) -> None:
        """Initializes and lays out all UI components of the editor panel."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        scroll_area = QScrollArea(); scroll_area.setWidgetResizable(True); scroll_area.setFrameShape(QFrame.NoFrame)
        main_layout.addWidget(scroll_area)
        content_widget = QWidget(); scroll_area.setWidget(content_widget)
        editor_layout = QVBoxLayout(content_widget); editor_layout.setContentsMargins(15, 15, 15, 15); editor_layout.setSpacing(15)
        
        self.info_label = QLabel("Select a well to edit"); font = self.font(); font.setPointSizeF(scaled_pt(14)); font.setBold(True); self.info_label.setFont(font)
        editor_layout.addWidget(self.info_label)

        # Shown only while inspecting a finalized day; see set_active_well.
        self.read_only_label = QLabel()
        self.read_only_label.setWordWrap(True)
        self.read_only_label.setObjectName("ReadOnlyNotice")
        self.read_only_label.setVisible(False)
        editor_layout.addWidget(self.read_only_label)

        self.status_group = QGroupBox("Main Status"); status_layout = QVBoxLayout(self.status_group)
        self.status_button_group = QButtonGroup(self); self.status_button_group.setExclusive(True)
        statuses = [self.STATUS_EMBRYO_ALIVE, self.STATUS_EMBRYO_DEAD, self.STATUS_HATCHED_ALIVE, self.STATUS_HATCHED_DEAD, self.STATUS_ABSENT]
        for status in statuses:
            btn = QPushButton(status); btn.setCheckable(True); btn.setObjectName("StatusButton")
            self.status_button_group.addButton(btn); btn.toggled.connect(lambda checked, s=status: self._on_status_toggled(s, checked))
            status_layout.addWidget(btn); self.status_buttons[status] = btn
        editor_layout.addWidget(self.status_group)
        
        self.lethal_endpoints_group = QGroupBox("Lethal Endpoints"); lethal_layout = QVBoxLayout(self.lethal_endpoints_group)
        for endpoint in self.LETHAL_ENDPOINTS:
            cb = QCheckBox(endpoint); cb.setObjectName("SublethalCheckbox"); cb.stateChanged.connect(self._save_changes)
            lethal_layout.addWidget(cb); self.lethal_checkboxes.append(cb)
        self.lethal_endpoints_group.setVisible(False); editor_layout.addWidget(self.lethal_endpoints_group)
        
        sublethal_group = QGroupBox("Sublethal Conditions"); self.sublethal_main_layout = QVBoxLayout(sublethal_group)
        self.sublethal_layout = QVBoxLayout(); self.sublethal_main_layout.addLayout(self.sublethal_layout)
        add_new_layout = QHBoxLayout(); self.new_sublethal_input = QLineEdit(); self.new_sublethal_input.setPlaceholderText("Type new condition...")
        self.new_sublethal_input.returnPressed.connect(self._add_new_sublethal_condition); self.add_sublethal_btn = QPushButton("+ Add")
        self.add_sublethal_btn.clicked.connect(self._add_new_sublethal_condition); add_new_layout.addWidget(self.new_sublethal_input); add_new_layout.addWidget(self.add_sublethal_btn)
        self.sublethal_main_layout.addLayout(add_new_layout); editor_layout.addWidget(sublethal_group)
        
        self.info_group = QGroupBox("Additional Information"); info_layout = QVBoxLayout(self.info_group)
        self.notes_edit = CustomTextEdit(); self.notes_edit.setPlaceholderText("Enter any notes for this well on this day...")
        self.notes_edit.textChanged.connect(self._notes_save_timer.start); self.notes_edit.enter_pressed.connect(self.notes_edit.clearFocus)
        info_layout.addWidget(self.notes_edit); editor_layout.addWidget(self.info_group); editor_layout.addStretch()

    def _on_status_toggled(self, status: str, checked: bool) -> None:
        """
        Slot for when a status button is toggled. Shows/hides the lethal
        endpoints group and triggers a save.
        """
        if not checked: return
        is_dead = status == self.STATUS_EMBRYO_DEAD
        self.lethal_endpoints_group.setVisible(is_dead)
        self._save_changes()

    def set_active_well(self, well_id: str, plate_index: int, day_index: int,
                        read_only: bool = False) -> None:
        """
        Sets the user's context to a specific well and loads the relevant data.

        A finalized day is locked against editing but remains readable, so the
        recorded lethal endpoints — held nowhere else in the interface — can be
        reviewed without reopening the day and discarding its derived rows.

        Args:
            well_id (str): The ID of the well (e.g., "A1").
            plate_index (int): The index of the plate.
            day_index (int): The index of the day.
            read_only (bool): Display the well's data without allowing edits.
        """
        self.well_id = well_id; self.plate_index = plate_index; self.day_index = day_index
        self.read_only = read_only
        self._shown_warnings.clear()
        is_active = self.well_id is not None
        editable = is_active and not read_only

        # Disabled before load_well_data() populates the fields, because
        # _save_changes tests status_group.isEnabled() and would otherwise write
        # the loaded values straight back: blockSignals() on a QGroupBox does not
        # suppress its children's signals, so setChecked() during population does
        # reach the save handler.
        for group in [self.status_group, self.lethal_endpoints_group,
                      self.sublethal_main_layout.parentWidget()]:
            group.setEnabled(editable)
        # Notes stay enabled but read-only: a disabled QTextEdit cannot be
        # scrolled or selected, which would hide long notes and block copying.
        self.info_group.setEnabled(is_active)
        self.notes_edit.setReadOnly(read_only)

        for i in reversed(range(self.sublethal_layout.count())):
            widget = self.sublethal_layout.itemAt(i).widget()
            if widget: widget.setParent(None)
        self.sublethal_checkboxes.clear()

        if not is_active:
            self.info_label.setText("Select a well to edit")
            self.read_only_label.setVisible(False)
            self.lethal_endpoints_group.setVisible(False)
            self.notes_edit.clear()
            return

        conc_id = self.manager.get_plate_layout(self.plate_index).get(self.well_id, "N/A")
        verb = "Viewing" if read_only else "Editing"
        self.info_label.setText(f"{verb}: Well {well_id} (Group: {conc_id})")
        self.read_only_label.setText(
            f"Day {day_index} is finalized. Reopen the day to make changes."
            if read_only else ""
        )
        self.read_only_label.setVisible(read_only)
        self.load_well_data()

    def load_well_data(self) -> None:
        """
        Fetches data for the currently active well from the ProjectManager
        and populates the UI fields with it.
        """
        if not self.well_id: return
        well_data = self.manager.get_well_data(self.day_index, self.plate_index, self.well_id)

        # blockSignals() on a QGroupBox does not reach its children, so the
        # setChecked() calls below emit and land in _save_changes. Writing there
        # would rewrite the row with auto_filled=0, reclassifying a carried-forward
        # observation as operator-entered merely because the well was opened, and
        # reopen_day preserves auto_filled=0 rows while discarding derived ones.
        # try/finally is essential, not defensive: _loading suppresses every
        # save, so leaving it set after an exception would leave the editor
        # looking functional while silently discarding the operator's edits.
        self._loading = True
        self.status_group.blockSignals(True)
        self.lethal_endpoints_group.blockSignals(True)
        self.info_group.blockSignals(True)
        try:
            current_status = well_data.get("status", self.STATUS_EMBRYO_ALIVE) # CORRECTED KEY
            if current_status in self.status_buttons:
                self.status_buttons[current_status].setChecked(True)
            self.lethal_endpoints_group.setVisible(current_status == self.STATUS_EMBRYO_DEAD)

            current_lethal = well_data.get("lethal_conditions", [])
            for cb in self.lethal_checkboxes:
                cb.setChecked(cb.text() in current_lethal)

            current_sublethal = well_data.get("sublethal_conditions", [])
            all_possible_conditions = sorted(list(set(self.PREDEFINED_SUBLETHAL + current_sublethal)))
            for i in reversed(range(self.sublethal_layout.count())):
                widget = self.sublethal_layout.itemAt(i).widget()
                if widget: widget.setParent(None)
            self.sublethal_checkboxes.clear()
            for condition in all_possible_conditions:
                self._add_sublethal_checkbox(condition, condition in current_sublethal)

            self.notes_edit.setText(well_data.get("notes", ""))
        finally:
            self.status_group.blockSignals(False)
            self.lethal_endpoints_group.blockSignals(False)
            self.info_group.blockSignals(False)
            self._loading = False

    def _add_sublethal_checkbox(self, text: str, checked: bool = False) -> None:
        """
        Dynamically adds a new checkbox for a sublethal condition to the UI.
        """
        if any(cb.text() == text for cb in self.sublethal_checkboxes): return
        cb = QCheckBox(text)
        cb.setObjectName("SublethalCheckbox"); cb.setChecked(checked)
        cb.stateChanged.connect(self._save_changes); self.sublethal_layout.addWidget(cb)
        self.sublethal_checkboxes.append(cb)

    def _add_new_sublethal_condition(self) -> None:
        """
        Handles the creation of a custom sublethal condition from the user input field.
        """
        new_condition_text = self.new_sublethal_input.text().strip()
        if new_condition_text:
            self._add_sublethal_checkbox(new_condition_text, checked=True)
            self.new_sublethal_input.clear(); self.interaction_occurred.emit()

    def force_save(self) -> None:
        """
        Force-save the current editor state.  Call this when a hotkey re-confirms
        the already-selected status so that the save runs even though the button
        toggled signal does not fire (Qt suppresses it when checked state does not
        change).
        """
        self._save_changes()

    def _save_changes(self) -> None:
        """
        Gathers all data from the UI fields, persists them via ProjectManager,
        and propagates terminal statuses to subsequent days.
        """
        # read_only is checked alongside the enabled state so the guarantee does
        # not rest on a widget's enabled flag, which styling changes could alter.
        # _loading suppresses the writes that populating the panel would otherwise
        # trigger; see load_well_data.
        if (not self.well_id or self.read_only or self._loading
                or not self.status_group.isEnabled()):
            return
        selected_status_button = self.status_button_group.checkedButton()
        if not selected_status_button: return

        status = selected_status_button.text()

        # Validate against previous day's status
        if self.day_index > 1:
            prev_data = self.manager.get_well_data(self.day_index - 1, self.plate_index, self.well_id)
            prev_status = prev_data.get("status", STATUS_LIVE_EMBRYO)
            impossible_msg = None
            if prev_status in DEAD_STATUSES and status in LIVE_STATUSES:
                impossible_msg = (
                    f"Impossible state transition for well {self.well_id}:\n\n"
                    f"  Day {self.day_index - 1}: {prev_status}\n"
                    f"  Day {self.day_index}: {status}\n\n"
                    f"A dead embryo cannot become alive. Save anyway?"
                )
            elif prev_status in HATCHED_STATUSES and status == STATUS_LIVE_EMBRYO:
                impossible_msg = (
                    f"Impossible state transition for well {self.well_id}:\n\n"
                    f"  Day {self.day_index - 1}: {prev_status}\n"
                    f"  Day {self.day_index}: {status}\n\n"
                    f"A hatched embryo cannot revert to an unhatched state. Save anyway?"
                )
            if impossible_msg:
                warning_key = (self.well_id, self.plate_index, self.day_index, status)
                if warning_key not in self._shown_warnings:
                    reply = QMessageBox.warning(
                        self, "Impossible State Transition", impossible_msg,
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    if reply == QMessageBox.StandardButton.No:
                        self.load_well_data()
                        return
                    self._shown_warnings.add(warning_key)

        sublethal_conditions = [cb.text() for cb in self.sublethal_checkboxes if cb.isChecked()]
        notes = self.notes_edit.toPlainText()
        lethal_conditions = [cb.text() for cb in self.lethal_checkboxes if cb.isChecked()] if status == self.STATUS_EMBRYO_DEAD else []

        # Read the prior state before overwriting it, so the edit can be undone.
        # Cheap next to the write, and the alternative — reconstructing it after
        # the fact — cannot be done at all.
        before = self.manager.get_well_data(self.day_index, self.plate_index, self.well_id)
        after = {
            "status": status,
            "sublethal_conditions": sublethal_conditions,
            "lethal_conditions": lethal_conditions,
            "notes": notes,
        }

        # Persist to DB
        try:
            self.manager.save_well_data(
                day=self.day_index,
                plate_index=self.plate_index,
                well_id=self.well_id,
                status=status,
                sublethal_conditions=sublethal_conditions,
                lethal_conditions=lethal_conditions,
                notes=notes,
                auto_filled=0,
            )
        except Exception as e:
            log.error(f"Could not save well data: {e}", exc_info=True)
            QMessageBox.warning(
                self, "Not Saved",
                "This change could not be written to the project. "
                "See Help > Open Log Folder for details."
            )
            return

        if well_state(before) != well_state(after):
            # An edit that changed nothing — reopening a well, or re-pressing the
            # status it already had — must not occupy a slot on the undo stack.
            self.edit_committed.emit({
                "day": self.day_index, "plate": self.plate_index,
                "well": self.well_id, "before": before, "after": after,
            })

        self.data_changed.emit(self.well_id, self.plate_index, self.day_index, status)
        self.interaction_occurred.emit()


    def handle_key_press(self, event: QKeyEvent) -> None:
        """
        Handles global key presses for setting the main status via number keys.
        """
        if not self.well_id or self.read_only: return
        key_map = {Qt.Key_1: self.STATUS_EMBRYO_ALIVE, Qt.Key_2: self.STATUS_EMBRYO_DEAD,
                   Qt.Key_3: self.STATUS_HATCHED_ALIVE, Qt.Key_4: self.STATUS_HATCHED_DEAD,
                   Qt.Key_5: self.STATUS_ABSENT}
        if event.key() in key_map:
            status_to_set = key_map[event.key()]
            if status_to_set in self.status_buttons:
                self.status_buttons[status_to_set].setChecked(True)