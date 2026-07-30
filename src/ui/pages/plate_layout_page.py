# plate_layout_page.py
import logging
from typing import Dict, Optional, List
import copy
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFrame,
                             QMessageBox, QTabWidget, QGridLayout, QGroupBox, QScrollArea)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QMouseEvent

from src.core.project_manager import ProjectManager
from src.ui.commands import PlateLayoutCommand
from src.ui.components import PlateWidget

log = logging.getLogger(__name__)

class ConcentrationWidget(QFrame):
    """
    A custom, checkable widget to display a concentration swatch and its ID.
    Acts as a 'brush' for painting wells on the plate layout.
    """
    clicked = Signal()

    def __init__(self, concentration_data: Dict, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.concentration_data = concentration_data
        self.setAutoFillBackground(True); self.setFrameShape(QFrame.StyledPanel)
        # A minimum rather than a fixed size, so the group ID is not clipped when
        # the system font is larger than the one this was measured against.
        self.setObjectName("ConcentrationWidget"); self.setMinimumSize(120, 50)
        layout = QVBoxLayout(self); layout.setContentsMargins(5, 5, 5, 5); layout.setSpacing(2)
        self.color_swatch = QFrame(self); self.color_swatch.setFixedHeight(20)
        self.color_swatch.setStyleSheet(f"background-color: {self.concentration_data['color']}; border-radius: 5px;")
        self.id_label = QLabel(self.concentration_data['id']); self.id_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.color_swatch); layout.addWidget(self.id_label)
        self._is_checked = False
    
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self.isChecked():
            self.setChecked(True)
            self.clicked.emit()
        super().mousePressEvent(event)

    def isChecked(self) -> bool:
        return self._is_checked

    def setChecked(self, checked: bool) -> None:
        self._is_checked = checked
        self.setProperty("checked", "true" if checked else "false")
        self.style().unpolish(self); self.style().polish(self)

class PlateLayoutPage(QWidget):
    """
    A page widget for assigning concentrations to wells across multiple plates.
    It provides a visual interface for 'painting' wells with selected
    concentration groups and provides feedback on assignment counts.
    """
    layout_saved = Signal()
    #: Text for the main window status bar, for actions that otherwise succeed silently.
    status_message = Signal(str)
    #: Emitted with a QUndoCommand for a whole-plate change, for MainWindow to push.
    layout_command = Signal(object)

    def __init__(self, manager: ProjectManager, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.manager = manager; self.current_brush_id = None; self.plate_widgets: Dict[int, PlateWidget] = {}
        self.is_painting_single_well = False; self.concentration_widgets: List[ConcentrationWidget] = []
        self.temp_layout: Dict[str, Dict[str, str]] = {}
        self._init_ui()

    def enter_page(self) -> None:
        """
        Called by the MainWindow when this page becomes active. It loads the
        current project's plate layout into a temporary buffer for editing.
        """
        self.temp_layout = copy.deepcopy(self.manager.get_all_plate_layouts())
        self._update_all_plate_views()
        self.load_counters()

    def _init_ui(self) -> None:
        """Initializes and lays out all UI components of the page."""
        main_layout = QHBoxLayout(self); main_layout.setContentsMargins(10, 10, 10, 10)
        left_panel = QFrame(); left_panel.setMinimumWidth(250); left_panel.setMaximumWidth(290)
        left_panel_layout = QVBoxLayout(left_panel)
        fmt = self.manager.get_plate_format()
        left_panel_layout.addWidget(QLabel("Plate Format"))
        left_panel_layout.addWidget(QLabel(fmt))
        self._create_concentration_panel(left_panel_layout)
        counters_group = QGroupBox("Well Assignment Counters")
        self.counters_layout = QVBoxLayout(counters_group)
        left_panel_layout.addWidget(counters_group); left_panel_layout.addStretch(); main_layout.addWidget(left_panel)
        right_panel = QWidget()
        right_panel_layout = QVBoxLayout(right_panel)
        right_panel_layout.setContentsMargins(0, 0, 0, 0)

        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        right_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        inner_widget = QWidget()
        inner_layout = QVBoxLayout(inner_widget)
        self.plate_tabs = QTabWidget(); self.plate_tabs.currentChanged.connect(self.load_counters)
        rows, cols = self.manager.get_plate_dimensions()
        num_plates = self.manager.get_project_info().get("num_plates", 1)
        for i in range(1, num_plates + 1):
            plate_view = PlateWidget(i, 1, self.manager, rows=rows, cols=cols)
            plate_view.well_mouse_press.connect(self.start_painting_single_well)
            plate_view.well_mouse_release.connect(self.stop_painting_single_well)
            plate_view.well_mouse_enter.connect(self.paint_well_single)
            plate_view.wells_selected_for_painting.connect(self.paint_wells_multiple)
            self.plate_tabs.addTab(plate_view, f"Plate {i}"); self.plate_widgets[i] = plate_view
        inner_layout.addWidget(self.plate_tabs)
        self._create_action_buttons(inner_layout)
        right_scroll.setWidget(inner_widget)
        right_panel_layout.addWidget(right_scroll)
        main_layout.addWidget(right_panel, 1)

    def _create_concentration_panel(self, parent_layout: QVBoxLayout) -> None:
        """Creates the left-side panel with selectable concentration 'brushes'."""
        conc_label = QLabel("Select Concentration:"); parent_layout.addWidget(conc_label)
        conc_grid_layout = QGridLayout(); conc_grid_layout.setSpacing(10)
        concentrations = self.manager.get_concentrations()
        for conc in concentrations:
            widget = ConcentrationWidget(conc)
            widget.clicked.connect(lambda w=widget: self._handle_concentration_selected(w)); self.concentration_widgets.append(widget)
        row, col = 0, 0
        for widget in self.concentration_widgets:
            conc_grid_layout.addWidget(widget, row, col); col += 1
            if col > 1: col = 0; row += 1
        parent_layout.addLayout(conc_grid_layout)
        self.unassign_btn = QPushButton("Unassign Well"); self.unassign_btn.setCheckable(True)
        self.unassign_btn.setObjectName("UnassignButton"); self.unassign_btn.clicked.connect(self._handle_unassign_selected)
        parent_layout.addWidget(self.unassign_btn)
        if self.concentration_widgets: self._handle_concentration_selected(self.concentration_widgets[0])

    def _handle_concentration_selected(self, selected_widget: ConcentrationWidget) -> None:
        """Sets the currently active concentration 'brush'."""
        self.current_brush_id = selected_widget.concentration_data['id']
        for widget in self.concentration_widgets:
            if widget is not selected_widget: widget.setChecked(False)
        self.unassign_btn.setChecked(False)

    def _handle_unassign_selected(self) -> None:
        """Activates the 'unassign' mode, clearing the current brush."""
        self.current_brush_id = None
        for widget in self.concentration_widgets: widget.setChecked(False)
        self.unassign_btn.setChecked(True)

    def _create_action_buttons(self, parent_layout: QVBoxLayout) -> None:
        """Creates the action buttons (Clear, Duplicate, Save) for the page."""
        button_container = QWidget(); button_layout = QHBoxLayout(button_container)
        button_layout.setContentsMargins(0, 10, 0, 0); button_layout.addStretch()
        clear_btn = QPushButton("Clear Plate"); clear_btn.clicked.connect(self._clear_current_plate); button_layout.addWidget(clear_btn)
        duplicate_btn = QPushButton("Duplicate Plate"); duplicate_btn.clicked.connect(self._duplicate_current_plate); button_layout.addWidget(duplicate_btn)
        save_btn = QPushButton("Save Layout"); save_btn.setObjectName("PrimaryButton"); save_btn.clicked.connect(self._save_layout)
        button_layout.addWidget(save_btn); parent_layout.addWidget(button_container)

    def _update_all_plate_views(self) -> None:
        """Refreshes all PlateWidgets to reflect the current temp_layout data."""
        for plate_idx, plate_widget in self.plate_widgets.items():
            layout_data = self.temp_layout.get(str(plate_idx), {})
            plate_widget.load_layout_from_data(layout_data)

    def apply_brush_to_wells(self, well_ids: List[str], plate_index: int) -> None:
        """
        Applies the currently selected concentration (or unassigns) to a
        list of well IDs for a specific plate.
        """
        plate_specific_layout = self.temp_layout.setdefault(str(plate_index), {})
        plate_widget = self.plate_widgets.get(plate_index)
        for well_id in well_ids:
            if self.current_brush_id is None:
                if well_id in plate_specific_layout:
                    del plate_specific_layout[well_id]
            else:
                plate_specific_layout[well_id] = self.current_brush_id
            if plate_widget:
                plate_widget.set_well_concentration(well_id, self.current_brush_id)
        self.load_counters()

    def load_counters(self, *args) -> None:
        """
        Calculates and displays the number of assigned vs. planned wells for each
        concentration group, with color-coded status feedback.
        Reuses existing labels to avoid widget churn on every tab change.
        """
        if not hasattr(self, '_counter_labels'):
            self._counter_labels: dict = {}

        counters = self.manager.get_assignment_counters()
        # get_assignment_counters() derives 'assigned' from the saved database
        # layout, but this page displays temp_layout — the live, possibly
        # unsaved editing buffer — so the assigned count must be tallied from
        # temp_layout alone. temp_layout starts as a copy of that same saved
        # layout (see enter_page()), so adding this tally on top of the
        # DB-derived count double-counted every already-saved well: an
        # untouched 60/60 layout displayed as 120/60.
        for counts in counters.values():
            counts['assigned'] = 0
        for plate_data in self.temp_layout.values():
            for conc_id in plate_data.values():
                if conc_id in counters: counters[conc_id]['assigned'] += 1

        for conc_id, counts in sorted(counters.items()):
            assigned, planned = counts['assigned'], counts['planned']
            # The two counts carry the state on their own — equal is complete,
            # second larger is short, first larger is over plan — so the color
            # below stays a redundant cue rather than the only signal.
            if assigned > planned:
                status = "error"
            elif assigned < planned:
                status = "warning"
            else:
                status = "ok"
            text = f"{conc_id}: {assigned} / {planned}"

            if conc_id in self._counter_labels:
                label = self._counter_labels[conc_id]
                label.setText(text)
            else:
                label = QLabel(text)
                label.setObjectName("CounterLabel")
                self.counters_layout.addWidget(label)
                self._counter_labels[conc_id] = label

            if label.property("status") != status:
                label.setProperty("status", status)
                label.style().unpolish(label)
                label.style().polish(label)

        # Remove labels for groups that no longer exist
        for conc_id in list(self._counter_labels):
            if conc_id not in counters:
                self._counter_labels.pop(conc_id).deleteLater()

    def apply_layout_snapshot(self, layout: Dict[str, Dict[str, str]]) -> None:
        """Replace the editing buffer wholesale and redraw. Used by undo/redo."""
        self.temp_layout = copy.deepcopy(layout)
        self._update_all_plate_views()
        self.load_counters()

    def _emit_layout_command(self, before: Dict[str, Dict[str, str]], text: str) -> None:
        self.layout_command.emit(
            PlateLayoutCommand(self, before, copy.deepcopy(self.temp_layout), text)
        )

    def _clear_current_plate(self) -> None:
        """Clears all assignments from the currently visible plate."""
        current_plate_index = self.plate_tabs.currentIndex() + 1
        before = copy.deepcopy(self.temp_layout)
        self.temp_layout[str(current_plate_index)] = {}
        self._update_all_plate_views(); self.load_counters()
        self._emit_layout_command(before, f"clear plate {current_plate_index}")

    def _duplicate_current_plate(self) -> None:
        """Copies the layout of the current plate to the next one."""
        current_plate_index = self.plate_tabs.currentIndex() + 1
        dest_index = current_plate_index + 1
        if dest_index > self.plate_tabs.count():
            QMessageBox.warning(self, "Duplicate Error", "This is the last plate."); return
        before = copy.deepcopy(self.temp_layout)
        source_layout = self.temp_layout.get(str(current_plate_index), {})
        self.temp_layout[str(dest_index)] = copy.deepcopy(source_layout)
        self._update_all_plate_views(); self.load_counters()
        self._emit_layout_command(before, f"duplicate plate {current_plate_index}")

    def _save_layout(self) -> None:
        """Commit the working layout to the database."""
        try:
            self.manager.commit_plate_layout(self.temp_layout)
        except Exception as e:
            log.error(f"Could not save the plate layout: {e}", exc_info=True)
            QMessageBox.critical(
                self, "Layout Not Saved",
                "The plate layout could not be saved.\n\n"
                "Your changes are still on screen. See Help > Open Log Folder for details."
            )
            return
        self.layout_saved.emit()
        assigned = sum(len(wells) for wells in self.temp_layout.values())
        self.status_message.emit(f"Plate layout saved — {assigned} wells assigned.")

    def start_painting_single_well(self, well_id: str, plate_index: int) -> None:
        """Initiates click-and-drag painting mode."""
        self.is_painting_single_well = True; self.paint_well_single(well_id, plate_index)

    def stop_painting_single_well(self, well_id: str, plate_index: int) -> None:
        """Stops click-and-drag painting mode."""
        self.is_painting_single_well = False

    def paint_well_single(self, well_id: str, plate_index: int) -> None:
        """Applies the brush to a single well during a drag operation."""
        if not self.is_painting_single_well: return
        self.apply_brush_to_wells([well_id], plate_index)

    def paint_wells_multiple(self, well_ids: List[str], plate_index: int) -> None:
        """Applies the brush to multiple wells selected via rubber band."""
        self.apply_brush_to_wells(well_ids, plate_index)
