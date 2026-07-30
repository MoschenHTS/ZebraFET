# experiment_view_widget.py
from typing import List, Dict
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QTabWidget, QVBoxLayout,
                             QLabel, QPushButton, QDialog, QTextEdit, QMessageBox,
                             QScrollArea, QLineEdit, QFormLayout, QDialogButtonBox)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeyEvent, QIcon

from src.core.project_manager import ProjectManager
from src.ui.components import PlateWidget
from src.ui.widgets.well_editor_widget import WellEditorWidget
from src.core.utils import resource_path, create_icon, create_themed_icon
from src.core.constants import (
    STATUS_LIVE_EMBRYO, STATUS_DEAD_EMBRYO, STATUS_LIVE_HATCHED,
    STATUS_DEAD_HATCHED, STATUS_ABSENT, LIVE_STATUSES, IRREVERSIBLE_STATUSES,
)

class ExperimentViewWidget(QWidget):
    """
    Main widget for the experiment data entry view, showing plates for each day
    and the well editor panel. Includes logic for managing day completion.
    """
    def __init__(self, manager: ProjectManager, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.manager = manager
        self.well_editor = WellEditorWidget(self.manager)
        self.well_editor.setMinimumWidth(200)
        self.plate_widgets: Dict = {}
        self.day_widgets: Dict = {}
        #: Suppresses the tab-change handler while tabs are being added or swapped.
        self._building = False
        self._num_plates = 1

        self._init_ui()
        self.well_editor.data_changed.connect(self._handle_well_update)
        self.day_tabs.currentChanged.connect(self._on_day_tab_changed)
        self._refresh_tabs()
        QTimer.singleShot(100, self._select_first_well)

    def _init_ui(self):
        """Initializes the main layout for the experiment view page."""
        outer_layout = QVBoxLayout(self)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setObjectName("ExperimentScrollArea")
        
        container = QWidget()
        main_layout = QHBoxLayout(container)
        
        self.day_tabs = QTabWidget()
        main_layout.addWidget(self.day_tabs, 4)
        main_layout.addWidget(self.well_editor, 1)
        
        scroll_area.setWidget(container)
        outer_layout.addWidget(scroll_area)

    def _refresh_tabs(self):
        """
        Creates one tab per day. The plates inside each are built on first visit.
        """
        self._building = True
        try:
            self.day_tabs.clear(); self.plate_widgets.clear(); self.day_widgets.clear()

            project_data = self.manager.get_project_info()
            num_days = project_data.get("num_days", 1)
            self._num_plates = project_data.get("num_plates", 1)
            completed_days = set(self.manager.get_completed_days())
            self._plate_rows, self._plate_cols = self.manager.get_plate_dimensions()

            for day_idx in range(1, num_days + 1):
                placeholder = QWidget()
                tab_index = self.day_tabs.addTab(placeholder, f"Day {day_idx}")
                self.day_widgets[day_idx] = {
                    "tab_index": tab_index,
                    "finalize_btn": None,
                    "widget": placeholder,
                    "built": False,
                }
                self._update_day_tab_ui(day_idx, is_complete=(day_idx in completed_days))
        finally:
            self._building = False

        initial_day = self.day_tabs.currentIndex() + 1
        if initial_day > 0:
            self._ensure_day_built(initial_day)
            self._propagate_data_for_day(initial_day)

    def _ensure_day_built(self, day_idx: int) -> None:
        """Create a day's plates the first time it is opened.

        Each plate is a full grid of well widgets with its own database read, so
        building every day up front costs the product of days and plates: about a
        second of frozen window on a six-plate, four-day study, and more as either
        grows. Only the day being looked at is needed.
        """
        info = self.day_widgets.get(day_idx)
        if info is None or info["built"]:
            return

        day_content = QWidget(); day_layout = QVBoxLayout(day_content)
        plate_tabs = QTabWidget(); day_layout.addWidget(plate_tabs)

        for plate_idx in range(1, self._num_plates + 1):
            plate_view = PlateWidget(plate_idx, day_idx, self.manager, rows=self._plate_rows, cols=self._plate_cols)
            plate_view.well_clicked.connect(self._handle_well_click)
            self.plate_widgets[(day_idx, plate_idx)] = plate_view
            plate_tabs.addTab(plate_view, f"Plate {plate_idx}")

        footer_layout = QHBoxLayout()
        review_btn = QPushButton("Review Day's Summary"); review_btn.setIcon(create_themed_icon("eye"))
        review_btn.clicked.connect(lambda _, d=day_idx: self._review_day(d))

        water_btn = QPushButton("Water Quality")
        water_btn.clicked.connect(lambda _, d=day_idx: self._edit_water_quality(d))

        finalize_btn = QPushButton()
        finalize_btn.clicked.connect(lambda _, d=day_idx: self._handle_finalize_button_click(d))

        footer_layout.addWidget(review_btn); footer_layout.addWidget(water_btn)
        footer_layout.addStretch(); footer_layout.addWidget(finalize_btn)
        day_layout.addLayout(footer_layout)

        tab_index = info["tab_index"]
        placeholder = info["widget"]
        # Swapping the tab moves the selection; restore it and suppress the
        # change signal so this does not re-enter through _on_day_tab_changed.
        self._building = True
        try:
            self.day_tabs.removeTab(tab_index)
            self.day_tabs.insertTab(tab_index, day_content, f"Day {day_idx}")
            self.day_tabs.setCurrentIndex(tab_index)
        finally:
            self._building = False
        if placeholder is not None:
            placeholder.deleteLater()

        info.update({"finalize_btn": finalize_btn, "widget": day_content, "built": True})
        self._update_day_tab_ui(day_idx, is_complete=self._is_day_complete(day_idx))

    def _update_day_tab_ui(self, day_idx: int, is_complete: bool):
        """Updates the UI of a day tab to reflect its completion status."""
        if day_idx not in self.day_widgets: return

        day_info = self.day_widgets[day_idx]
        tab_index = day_info["tab_index"]
        finalize_btn = day_info["finalize_btn"]

        if is_complete:
            self.day_tabs.setTabText(tab_index, f"Day {day_idx} (Completed)")
            self.day_tabs.setTabIcon(tab_index, create_themed_icon("check"))
        else:
            self.day_tabs.setTabText(tab_index, f"Day {day_idx}")
            self.day_tabs.setTabIcon(tab_index, QIcon())

        # The footer lives inside the day's content, which an unvisited day does
        # not have yet; it is styled when the day is built.
        if finalize_btn is None:
            return

        if is_complete:
            finalize_btn.setText("Reopen Day for Editing")
            finalize_btn.setObjectName("SecondaryButton")
            finalize_btn.setEnabled(True)
        else:
            finalize_btn.setText("Finalize Day")
            finalize_btn.setObjectName("PrimaryButton")
            prior_complete = day_idx == 1 or self._is_day_complete(day_idx - 1)
            finalize_btn.setEnabled(prior_complete)
            finalize_btn.setToolTip(
                "" if prior_complete
                else f"Finalize Day {day_idx - 1} first."
            )

        finalize_btn.style().unpolish(finalize_btn); finalize_btn.style().polish(finalize_btn)
    
    def _handle_finalize_button_click(self, day: int):
        """Handles both finalizing and reopening a day."""
        is_complete = self._is_day_complete(day)

        if is_complete:
            info = self.manager.get_project_info()
            num_days = info.get("num_days", 1)
            completed = self.manager.get_completed_days()
            later_complete = [d for d in range(day + 1, num_days + 1) if d in completed]
            cascade_msg = (
                f"\n\nNote: Days {', '.join(str(d) for d in later_complete)} will also be reopened."
                if later_complete else ""
            )
            reply = QMessageBox.question(
                self, "Confirm Reopening",
                f"Are you sure you want to reopen Day {day} for editing?{cascade_msg}",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                try:
                    self.manager.reopen_day(day)
                except Exception as e:
                    log.error(f"Could not reopen day {day}: {e}", exc_info=True)
                    QMessageBox.critical(self, "Day Not Reopened",
                        "The day could not be reopened. See Help > Open Log Folder.")
                    return
                for d in range(day, num_days + 1):
                    self._update_day_tab_ui(d, is_complete=False)
                self.refresh_view()
        else:
            # Sequential guard: require prior day to be finalized first
            if day > 1 and not self._is_day_complete(day - 1):
                QMessageBox.warning(
                    self, "Sequential Order Required",
                    f"Day {day - 1} must be finalized before finalizing Day {day}.",
                )
                return

            inconsistencies = self._check_inconsistencies(day)
            if inconsistencies:
                QMessageBox.warning(self, "Inconsistent Data Found",
                                    "The following inconsistencies must be corrected before finalizing:\n\n" + "\n".join(inconsistencies))
                return

            try:
                self.manager.finalize_day(day)
            except Exception as e:
                log.error(f"Could not finalize day {day}: {e}", exc_info=True)
                QMessageBox.critical(self, "Day Not Finalized",
                    "The day could not be finalized. See Help > Open Log Folder.")
                return
            self._update_day_tab_ui(day, is_complete=True)
            self.refresh_view()

            QMessageBox.information(self, "Day Finalized",
                                    f"Day {day} has been finalized.\nYou can now add photos in the 'Photo Documentation' page.")

    def _handle_well_click(self, well_id, plate_index, day_index):
        """Handles a click on a well, updating selections and the editor.

        A finalized day loads in read-only form: the lock prevents editing, not
        inspection, so the recorded lethal endpoints stay reachable without
        reopening the day.
        """
        self.well_editor.set_active_well(
            well_id, plate_index, day_index,
            read_only=self._is_day_complete(day_index),
        )
        current_plate_widget = self.plate_widgets.get((day_index, plate_index))
        if current_plate_widget:
            current_plate_widget.set_selected_well(well_id)
        self.setFocus()

    def _handle_well_update(self, well_id, plate_index, day_index, new_status):
        """Callback to update a single well's visual status."""
        plate_widget = self.plate_widgets.get((day_index, plate_index))
        if plate_widget:
            plate_widget.update_well_status(well_id, new_status)

    def _select_first_well(self):
        """ Selects the first well (A1) of the current plate on the current day. """
        current_day_idx = self.day_tabs.currentIndex() + 1
        self._ensure_day_built(current_day_idx)
        current_day_widget = self.day_tabs.currentWidget()
        if not current_day_widget: return
        plate_tabs = current_day_widget.findChild(QTabWidget)
        if plate_tabs:
            plate_idx = plate_tabs.currentIndex() + 1
            self._handle_well_click("A1", plate_idx, current_day_idx)

    def keyPressEvent(self, event: QKeyEvent):
        """ 
        Handles key press events for navigation and status hotkeys.
        This event handler captures keys at the widget level, ensuring consistent
        behavior regardless of which child widget has focus.
        """
        current_day_idx = self.day_tabs.currentIndex() + 1
        day_is_locked = self._is_day_complete(current_day_idx)
        key = event.key()

        status_map = {
            Qt.Key_1: STATUS_LIVE_EMBRYO, Qt.Key_2: STATUS_DEAD_EMBRYO,
            Qt.Key_3: STATUS_LIVE_HATCHED, Qt.Key_4: STATUS_DEAD_HATCHED,
            Qt.Key_5: STATUS_ABSENT,
        }
        # Only the status hotkeys are refused on a finalized day; arrow keys still
        # move between wells so the plate can be walked in read-only form.
        if day_is_locked and key in status_map:
            self._show_day_locked_message()
            event.accept()
            return

        if key in status_map:
            if self.well_editor.well_id:
                status_to_set = status_map[key]
                if status_to_set in self.well_editor.status_buttons:
                    btn = self.well_editor.status_buttons[status_to_set]
                    if btn.isChecked():
                        # Button already in this state — toggled won't fire; force save explicitly.
                        self.well_editor.force_save()
                    else:
                        btn.setChecked(True)  # toggled signal triggers _save_changes
                event.accept()
                return

        if not self.well_editor.well_id:
            super().keyPressEvent(event)
            return

        current_well = self.well_editor.well_id
        row = ord(current_well[0]) - ord('A'); col = int(current_well[1:]) - 1
        
        if key == Qt.Key_Up: row -= 1
        elif key == Qt.Key_Down: row += 1
        elif key == Qt.Key_Left: col -= 1
        elif key == Qt.Key_Right: col += 1
        else:
            super().keyPressEvent(event)
            return
            
        row = max(0, min(row, self._plate_rows - 1)); col = max(0, min(col, self._plate_cols - 1))
        new_well_id = f"{chr(ord('A') + row)}{col + 1}"
        
        if new_well_id != current_well:
            day_widget = self.day_tabs.currentWidget()
            if day_widget:
                plate_tabs = day_widget.findChild(QTabWidget)
                plate_index = plate_tabs.currentIndex() + 1 if plate_tabs else 1
                self._handle_well_click(new_well_id, plate_index, self.day_tabs.currentIndex() + 1)
        
        event.accept()

    def reload_well(self, day: int, plate: int, well_id: str) -> None:
        """Re-read one well from the database after an undo or redo.

        Both the plate and, when it is the well on screen, the editor panel have
        to follow: leaving the editor showing the undone values would let the
        next keystroke write them straight back.
        """
        plate_widget = self.plate_widgets.get((day, plate))
        if plate_widget is not None:
            plate_widget.refresh_well(well_id)
        if (self.well_editor.well_id == well_id
                and self.well_editor.plate_index == plate
                and self.well_editor.day_index == day):
            self.well_editor.load_well_data()

    def refresh_view(self):
        """ Public method to refresh all plates when layout or data changes. """
        for plate_widget in self.plate_widgets.values():
            plate_widget.reload_layout_from_manager()

    def _is_day_complete(self, day: int) -> bool:
        """Checks if a given day is marked as complete."""
        return day in self.manager.get_completed_days()

    def _show_day_locked_message(self):
        """Shows a standard message box for locked days."""
        QMessageBox.information(
            self,
            "Day Finalized",
            "This day is finalized. To make changes, please reopen the day for editing."
        )

    def _propagate_data_for_day(self, day: int):
        """
        Ensures well data for the given day exists by copying it from the previous day.
        This only runs if data for the current day doesn't already exist.
        """
        if day <= 1: return

        if not self.manager.has_well_data_for_day(day) and self.manager.has_well_data_for_day(day - 1):
            self.manager.propagate_day_data(from_day=day - 1, to_day=day)
            self.refresh_view()

    def _on_day_tab_changed(self, index: int):
        """Handler for when the user switches day tabs."""
        if self._building or index < 0:
            return
        day = index + 1
        self._ensure_day_built(day)
        self._propagate_data_for_day(day)
        
    def _review_day(self, day: int):
        """
        Opens a dialog with a summary for the selected day.
        - For Day 1, it shows initial non-standard observations.
        - For subsequent days, it shows only the changes from the previous day.
        """
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Review of Day {day}")
        dialog.setMinimumSize(420, 500)
        
        layout = QVBoxLayout(dialog)
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        layout.addWidget(text_edit)
        
        summary_lines = []

        if day == 1:
            summary_lines.append(f"<h1>Initial Observations for Day {day}</h1>")
            summary_lines.append(f"<p>This summary shows all wells not marked as '{STATUS_LIVE_EMBRYO}'.</p>")

            day_data = self.manager.get_well_observations_for_day(day)
            sorted_plates = sorted(day_data.items(), key=lambda item: int(item[0]))
            observations_found = False

            for plate_idx, wells in sorted_plates:
                plate_header_added = False
                sorted_wells = sorted(wells.items(), key=lambda item: (item[0][0], int(item[0][1:])))

                for well_id, data in sorted_wells:
                    status = data.get("status", "N/A")
                    
                    if status != STATUS_LIVE_EMBRYO:
                        observations_found = True
                        if not plate_header_added:
                            summary_lines.append(f"<h2>Plate {plate_idx}</h2>")
                            plate_header_added = True
                        
                        notes = data.get("notes", "").strip()
                        sublethal = data.get("sublethal_conditions", [])
                        summary_lines.append(f"<b>Well {well_id}:</b> {status}")
                        if sublethal:
                            summary_lines.append(f"&nbsp;&nbsp;- Conditions: <i>{', '.join(sublethal)}</i>")
                        if notes:
                            summary_lines.append(f"&nbsp;&nbsp;- Notes: <i>{notes}</i>")
            
            if not observations_found:
                summary_lines.append("<p><i>No notable observations recorded for this day.</i></p>")

        else:  # For Day > 1
            summary_lines.append(f"<h1>Changes on Day {day}</h1>")
            summary_lines.append(f"<p>This summary shows wells whose status changed between Day {day - 1} and Day {day}.</p>")
            
            prev_day_data = self.manager.get_well_observations_for_day(day - 1)
            curr_day_data = self.manager.get_well_observations_for_day(day)
            changes_found = False

            sorted_plates = sorted(curr_day_data.keys(), key=int)
            for plate_idx_str in sorted_plates:
                plate_header_added = False
                plate_wells = curr_day_data[plate_idx_str]
                sorted_well_ids = sorted(plate_wells.keys(), key=lambda item: (item[0], int(item[1:])))

                for well_id in sorted_well_ids:
                    prev_well_data = prev_day_data.get(plate_idx_str, {}).get(well_id, {})
                    curr_well_data = plate_wells.get(well_id, {})

                    prev_status = prev_well_data.get("status", "N/A")
                    curr_status = curr_well_data.get("status", "N/A")

                    if prev_status != curr_status:
                        changes_found = True
                        if not plate_header_added:
                            summary_lines.append(f"<h2>Plate {int(plate_idx_str)}</h2>")
                            plate_header_added = True
                        summary_lines.append(f"<b>Well {well_id}:</b> <i>{prev_status}</i> → <b>{curr_status}</b>")
            
            if not changes_found:
                summary_lines.append("<p><i>No status changes were recorded between the previous day and today.</i></p>")

        text_edit.setHtml("<br>".join(summary_lines))

        # Without a button bar this dialog could only be dismissed by the window
        # close box or an undiscoverable Escape.
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)

        dialog.exec()

    def _edit_water_quality(self, day: int):
        """Record the OECD TG 236 physicochemical parameters measured on *day*."""
        existing = self.manager.get_water_quality_log().get(day, {})

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Water Quality — Day {day}")
        dialog.setMinimumWidth(360)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"<b>Physicochemical measurements for Day {day}</b>"))
        form = QFormLayout()
        fields = {
            "temperature": QLineEdit(existing.get("temperature", "")),
            "dissolved_oxygen": QLineEdit(existing.get("dissolved_oxygen", "")),
            "ph": QLineEdit(existing.get("ph", "")),
            "conductivity": QLineEdit(existing.get("conductivity", "")),
            "notes": QLineEdit(existing.get("notes", "")),
        }
        fields["temperature"].setPlaceholderText("e.g. 26 (OECD: 26 ± 1 °C)")
        fields["dissolved_oxygen"].setPlaceholderText("e.g. 92 (OECD: ≥ 80% saturation)")
        form.addRow("Temperature (°C):", fields["temperature"])
        form.addRow("Dissolved O₂ (% sat.):", fields["dissolved_oxygen"])
        form.addRow("pH:", fields["ph"])
        form.addRow("Conductivity (µS/cm):", fields["conductivity"])
        form.addRow("Notes:", fields["notes"])
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            values = {name: field.text().strip() for name, field in fields.items()}
            try:
                if any(values.values()):
                    self.manager.save_water_quality(day, **values)
                else:
                    # Nothing was entered. Storing an all-blank row would add an empty
                    # line to the report's monitoring table and make the log read as
                    # populated, so clear the day instead.
                    self.manager.delete_water_quality(day)
            except Exception as e:
                log.error(f"Could not record water quality for day {day}: {e}", exc_info=True)
                QMessageBox.critical(
                    self, "Measurements Not Saved",
                    f"The water quality measurements for Day {day} could not be written "
                    "to the project. See Help > Open Log Folder for details."
                )

    def _check_inconsistencies(self, day: int) -> List[str]:
        """
        Checks for logical inconsistencies between the current day and the previous one.
        """
        if day == 1: return []
        inconsistencies = []
        prev_day_data = self.manager.get_well_observations_for_day(day - 1)
        curr_day_data = self.manager.get_well_observations_for_day(day)
        
        for plate_idx, wells in curr_day_data.items():
            for well_id, data in wells.items():
                prev_data = prev_day_data.get(plate_idx, {}).get(well_id)
                if not prev_data: continue

                prev_status = prev_data.get("status")
                curr_status = data.get("status")

                is_prev_dead = prev_status in IRREVERSIBLE_STATUSES
                is_curr_alive = curr_status in LIVE_STATUSES
                
                if is_prev_dead and is_curr_alive:
                    msg = f"Well {well_id} (Plate {plate_idx}): Inconsistent state (dead embryo cannot be alive)."
                    inconsistencies.append(msg)
                    plate_widget = self.plate_widgets.get((day, int(plate_idx)))
                    if plate_widget:
                        well_widget = plate_widget.well_widgets.get(well_id)
                        if well_widget: well_widget.set_inconsistent(True, msg)
        return inconsistencies