# concentration_planner_page.py
import math
from typing import Dict, Any, List
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QTableWidget, QHeaderView, QTableWidgetItem,
                             QGridLayout, QComboBox, QDoubleSpinBox, QMessageBox,
                             QCheckBox, QGroupBox, QColorDialog, QScrollArea, QSpinBox)
from PySide6.QtCore import Qt, Signal, QLocale
from PySide6.QtGui import QColor, QIntValidator, QDoubleValidator

from src.ui.components import FloatingLabelLineEdit
from src.core.constants import CONCENTRATION_TYPE_MAP, CONCENTRATION_TYPE_REVERSE

class ConcentrationPlannerPage(QWidget):
    """
    A widget for planning the concentration groups for an experiment.
    It allows defining control, substrate, and solvent groups, calculating
    serial dilutions, and determining project requirements like the number
    of embryos and plates needed.
    """
    project_finalized = Signal(dict)
    project_settings_changed = Signal()

    # Centralized Constants
    ROW_HEIGHT = 50
    DEFAULT_WELLS = 20
    DEFAULT_REPLICATES = 3
    COLOR_POOL = ["#3498db", "#e74c3c", "#2ecc71", "#f1c40f", "#9b59b6", "#e67e22",
                  "#1abc9c", "#34495e", "#c0392b", "#2980b9", "#27ae60", "#f39c12"]

    # Type Mapping for UI Display and Internal Data
    TYPE_MAP = CONCENTRATION_TYPE_MAP
    REVERSE_TYPE_MAP = CONCENTRATION_TYPE_REVERSE


    def __init__(self, data: dict, is_editing: bool = False, parent: QWidget = None, manager=None):
        """
        Initializes the ConcentrationPlannerPage.

        Args:
            data (dict): The initial project data (used for new-project creation flow).
            is_editing (bool): True if editing an existing project, False for a new one.
            parent (QWidget, optional): The parent widget. Defaults to None.
            manager: ProjectManager instance (required when is_editing=True).
        """
        super().__init__(parent)
        self.data = data
        self.is_editing = is_editing
        self._manager = manager
        self._row_connections = {}
        # Derive plate size from context
        if is_editing and manager is not None:
            rows, cols = manager.get_plate_dimensions()
            self._plate_size = rows * cols
        else:
            from src.core.constants import PLATE_FORMATS, DEFAULT_PLATE_FORMAT
            fmt = data.get("plate_format", DEFAULT_PLATE_FORMAT)
            rows, cols = PLATE_FORMATS.get(fmt, PLATE_FORMATS[DEFAULT_PLATE_FORMAT])
            self._plate_size = rows * cols
        self._init_ui()
        if self.is_editing and "concentration_settings" in self.data:
            self._populate_from_data(self.data["concentration_settings"])
        elif not self.is_editing:
            self._update_table_structure()

    def _init_ui(self) -> None:
        """Initializes the user interface of the page."""
        main_layout = QHBoxLayout(self)
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)

        title_text = "Edit Concentration Plan" if self.is_editing else f"Step 2: Plan Concentrations for '{self.data['project_name']}'"
        title_label = QLabel(title_text)
        font = self.font()
        font.setPointSize(14)
        font.setBold(True)
        title_label.setFont(font)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["ID", "Type", "Conc. (unit)", "Wells/Rep", "Per Plate", "Color"])
        _header = self.table.horizontalHeader()
        _header.setSectionResizeMode(QHeaderView.Interactive)
        _header.setMinimumSectionSize(60)
        _header.resizeSection(0, 45)
        _header.resizeSection(1, 100)
        _header.resizeSection(2, 110)
        _header.resizeSection(3, 85)
        _header.resizeSection(4, 80)
        _header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(self.ROW_HEIGHT)

        left_layout.addWidget(title_label)
        left_layout.addWidget(self.table)

        right_scroll_area = QScrollArea()
        right_scroll_area.setMinimumWidth(260)
        right_scroll_area.setMaximumWidth(320)
        right_scroll_area.setWidgetResizable(True)
        right_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right_panel_content = QWidget()
        right_layout = QVBoxLayout(right_panel_content)

        group_box = QGroupBox("Define Groups")
        grid = QGridLayout(group_box)
        grid.setSpacing(15)
        self.controls_spin = FloatingLabelLineEdit("Negative Controls (Co)")
        self.controls_spin.setValidator(QIntValidator(1, 10, self))
        self.controls_spin.setText("1")
        self.concentrations_spin = FloatingLabelLineEdit("Concentrations (C)")
        self.concentrations_spin.setValidator(QIntValidator(1, 20, self))
        self.concentrations_spin.setText("5")
        self.solvents_spin = FloatingLabelLineEdit("Solvent Controls (SC)")
        self.solvents_spin.setValidator(QIntValidator(0, 10, self))
        self.solvents_spin.setText("0")
        self.positive_controls_spin = FloatingLabelLineEdit("Positive Controls (PC)")
        self.positive_controls_spin.setValidator(QIntValidator(0, 10, self))
        self.positive_controls_spin.setText("0")

        grid.addWidget(self.controls_spin, 0, 0, 1, 2)
        grid.addWidget(self.concentrations_spin, 1, 0, 1, 2)
        grid.addWidget(self.solvents_spin, 2, 0, 1, 2)
        grid.addWidget(self.positive_controls_spin, 3, 0, 1, 2)
        self.controls_spin.line_edit.textChanged.connect(self._update_table_structure)
        self.concentrations_spin.line_edit.textChanged.connect(self._update_table_structure)
        self.solvents_spin.line_edit.textChanged.connect(self._update_table_structure)
        self.positive_controls_spin.line_edit.textChanged.connect(self._update_table_structure)

        calc_box = QGroupBox("Serial Dilution Calculator")
        grid_calc = QGridLayout(calc_box)
        grid_calc.setSpacing(15)
        locale = QLocale(QLocale.English)
        self.factor_spin = FloatingLabelLineEdit("Dilution Factor")
        factor_validator = QDoubleValidator(1.01, 100.0, 2, self)
        factor_validator.setLocale(locale)
        factor_validator.setNotation(QDoubleValidator.StandardNotation)
        self.factor_spin.setValidator(factor_validator)
        self.factor_spin.setText("2.2")
        self.highest_conc_spin = FloatingLabelLineEdit("Highest Concentration")
        conc_validator = QDoubleValidator(0.0001, 10000.0, 4, self)
        conc_validator.setLocale(locale)
        conc_validator.setNotation(QDoubleValidator.StandardNotation)
        self.highest_conc_spin.setValidator(conc_validator)
        self.highest_conc_spin.setText("100.0")
        generate_btn = QPushButton("Generate Series")
        generate_btn.clicked.connect(self._generate_series)
        grid_calc.addWidget(self.factor_spin, 0, 0, 1, 2)
        grid_calc.addWidget(self.highest_conc_spin, 1, 0, 1, 2)
        grid_calc.addWidget(generate_btn, 2, 0, 1, 2)

        self.req_box = QGroupBox("Project Requirements")
        grid_req = QGridLayout(self.req_box)
        grid_req.setSpacing(15)
        self.replicates_spin = FloatingLabelLineEdit("Replicates per Group")
        self.replicates_spin.setValidator(QIntValidator(1, 100, self))
        self.replicates_spin.setText(str(self.DEFAULT_REPLICATES))
        self.replicates_spin.line_edit.textChanged.connect(self._calculate_requirements)
        self.total_embryos_label = QLabel("Total Embryos: 0")
        grid_req.addWidget(self.replicates_spin, 0, 0, 1, 2)
        grid_req.addWidget(self.total_embryos_label, 1, 0, 1, 2)

        self.action_button = QPushButton()
        self.action_button.setText("Save Changes" if self.is_editing else "Finish and Create Project")
        self.action_button.clicked.connect(self._save_settings if self.is_editing else self._finalize_project)
        right_layout.addWidget(group_box)
        right_layout.addWidget(calc_box)
        right_layout.addWidget(self.req_box)
        right_layout.addStretch()
        right_layout.addWidget(self.action_button)
        right_panel_content.setLayout(right_layout)
        right_scroll_area.setWidget(right_panel_content)
        main_layout.addWidget(left_panel, 1)
        main_layout.addWidget(right_scroll_area)

    def _safe_get_int(self, widget: FloatingLabelLineEdit, default: int = 0) -> int:
        """
        Safely gets an integer from a FloatingLabelLineEdit widget.

        Args:
            widget (FloatingLabelLineEdit): The widget to get the text from.
            default (int): The default value to return on failure.

        Returns:
            int: The integer value or the default.
        """
        try:
            return int(widget.text()) if widget.text() else default
        except (ValueError, TypeError):
            return default

    def _safe_get_float(self, widget: FloatingLabelLineEdit, default: float = 0.0) -> float:
        """
        Safely gets a float from a FloatingLabelLineEdit widget.

        Args:
            widget (FloatingLabelLineEdit): The widget to get the text from.
            default (float): The default value to return on failure.

        Returns:
            float: The float value or the default.
        """
        try:
            return float(widget.text()) if widget.text() else default
        except (ValueError, TypeError):
            return default

    def _update_table_structure(self) -> None:
        """
        Updates the table rows based on the number of groups
        defined in the input spins, adding or removing rows as needed.
        """
        self.table.blockSignals(True)

        num_controls = self._safe_get_int(self.controls_spin, 1)
        num_concentrations = self._safe_get_int(self.concentrations_spin, 0)
        num_solvents = self._safe_get_int(self.solvents_spin, 0)
        num_positive = self._safe_get_int(self.positive_controls_spin, 0)
        total_rows_needed = num_controls + num_concentrations + num_solvents + num_positive

        current_rows = self.table.rowCount()

        for i in range(total_rows_needed, current_rows):
            self._disconnect_row_signals(i)

        if total_rows_needed > current_rows:
            self.table.setRowCount(total_rows_needed)
            for i in range(current_rows, total_rows_needed):
                self._populate_row(i, "", "")
        elif total_rows_needed < current_rows:
            self.table.setRowCount(total_rows_needed)

        row_cursor = 0
        for i in range(num_controls):
            self.table.item(row_cursor, 0).setText(f"Co{i+1}")
            type_item = QTableWidgetItem(self.TYPE_MAP["Control"])
            type_item.setFlags(type_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row_cursor, 1, type_item)
            row_cursor += 1
        for i in range(num_concentrations):
            self.table.item(row_cursor, 0).setText(f"C{i+1}")
            type_item = QTableWidgetItem(self.TYPE_MAP["Substrate"])
            type_item.setFlags(type_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row_cursor, 1, type_item)
            row_cursor += 1
        for i in range(num_solvents):
            self.table.item(row_cursor, 0).setText(f"SC{i+1}")
            type_item = QTableWidgetItem(self.TYPE_MAP["Solvent Control"])
            type_item.setFlags(type_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row_cursor, 1, type_item)
            row_cursor += 1
        for i in range(num_positive):
            self.table.item(row_cursor, 0).setText(f"PC{i+1}")
            type_item = QTableWidgetItem(self.TYPE_MAP["Positive Control"])
            type_item.setFlags(type_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row_cursor, 1, type_item)
            row_cursor += 1

        self.table.blockSignals(False)
        self._calculate_requirements()

    def _disconnect_row_signals(self, row: int) -> None:
        """Disconnect all managed signals for a specific row."""
        if row in self._row_connections:
            for connection in self._row_connections[row]:
                try:
                    self.disconnect(connection)
                except (TypeError, RuntimeError):
                    pass
            del self._row_connections[row]

    def _populate_row(self, row: int, item_id: str, item_type: str, data: Dict = None) -> None:
        """
        Populates a single row in the table with the necessary widgets and data.

        Args:
            row (int): The row index to populate.
            item_id (str): The ID of the concentration group (e.g., "C1").
            item_type (str): The internal type of group ("Control", "Substrate", etc.).
            data (Dict, optional): Existing data to fill the row with. Defaults to None.
        """
        self._disconnect_row_signals(row)
        self._row_connections[row] = []

        self.table.setRowHeight(row, self.ROW_HEIGHT)

        # ID Item 
        id_item = QTableWidgetItem(item_id)
        self.table.setItem(row, 0, id_item)

        # Type Item (Display only) 
        display_type = self.TYPE_MAP.get(item_type, item_type)
        type_item = QTableWidgetItem(display_type)
        type_item.setFlags(type_item.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(row, 1, type_item)

        # Concentration SpinBox
        conc_spinbox = QDoubleSpinBox()
        conc_spinbox.setRange(0, 10000); conc_spinbox.setDecimals(2); conc_spinbox.setMinimumWidth(90)
        if item_type == "Control":
            conc_spinbox.setValue(0.0); conc_spinbox.setEnabled(False)
        self.table.setCellWidget(row, 2, conc_spinbox)

        # Wells SpinBox
        wells_spinbox = QSpinBox()
        wells_spinbox.setRange(1, self._plate_size)
        wells_spinbox.setValue(self.DEFAULT_WELLS)
        wells_spinbox.setMinimumWidth(70)
        self.table.setCellWidget(row, 3, wells_spinbox)

        # Per Plate CheckBox 
        per_plate_widget = QWidget(); per_plate_layout = QHBoxLayout(per_plate_widget)
        per_plate_layout.setAlignment(Qt.AlignCenter); per_plate_layout.setContentsMargins(0, 0, 0, 0)
        per_plate_cb = QCheckBox(); per_plate_layout.addWidget(per_plate_cb)
        self.table.setCellWidget(row, 4, per_plate_widget)

        # Color Button 
        color_button = QPushButton()
        color = QColor(data.get("color")) if data and data.get("color") else QColor(self.COLOR_POOL[row % len(self.COLOR_POOL)])
        color_button.setStyleSheet(f"background-color: {color.name()}; border-radius: 5px;")
        color_button.setProperty("row", row)
        self.table.setCellWidget(row, 5, color_button)

        if data:
            conc_spinbox.setValue(data.get("value", 0.0))
            wells_spinbox.setValue(data.get("wells", self.DEFAULT_WELLS))
            per_plate_cb.setChecked(data.get("per_plate", False))

        # Connect signals and store connection objects 
        self._row_connections[row].append(
            conc_spinbox.valueChanged.connect(self._calculate_requirements)
        )
        self._row_connections[row].append(
            wells_spinbox.valueChanged.connect(self._calculate_requirements)
        )
        self._row_connections[row].append(
            per_plate_cb.toggled.connect(self._calculate_requirements)
        )
        self._row_connections[row].append(
            color_button.clicked.connect(self._open_color_picker)
        )

    def _open_color_picker(self) -> None:
        """Opens a color dialog to let the user pick a new color for a group."""
        button = self.sender()
        row = button.property("row")
        style = button.styleSheet()
        current_color_hex = style.split("background-color: ")[1].split(";")[0]
        current_color = QColor(current_color_hex)
        new_color = QColorDialog.getColor(current_color, self)
        if new_color.isValid():
            button.setStyleSheet(f"background-color: {new_color.name()}; border-radius: 5px;")
            self._calculate_requirements()

    def _generate_series(self) -> None:
        """
        Calculates and populates the concentration values for "Concentration"
        groups based on a serial dilution from a highest concentration.
        """
        num_concentrations = self._safe_get_int(self.concentrations_spin, 0)
        if num_concentrations == 0:
            return

        factor = self._safe_get_float(self.factor_spin, 1.0)
        if factor <= 1.0:
            QMessageBox.warning(self, "Invalid Factor", "Dilution factor must be greater than 1.")
            return

        max_conc_in_table = 0
        for row in range(self.table.rowCount()):
            type_item = self.table.item(row, 1)
            if type_item and type_item.text() == self.TYPE_MAP["Substrate"]:
                value = self.table.cellWidget(row, 2).value()
                if value > max_conc_in_table:
                    max_conc_in_table = value

        highest_conc_val = max_conc_in_table if max_conc_in_table > 0 else self._safe_get_float(self.highest_conc_spin, 0.0)
        if highest_conc_val <= 0:
            QMessageBox.warning(self, "Invalid Concentration", "Highest concentration must be greater than 0.")
            return

        concentrations = [highest_conc_val / (factor ** i) for i in range(num_concentrations)]
        concentrations.reverse()

        substrate_rows = []
        for row in range(self.table.rowCount()):
            type_item = self.table.item(row, 1)
            if type_item and type_item.text() == self.TYPE_MAP["Substrate"]:
                substrate_rows.append(row)

        for i, row_index in enumerate(substrate_rows):
            if i < len(concentrations):
                self.table.cellWidget(row_index, 2).setValue(concentrations[i])

    def _calculate_requirements(self) -> None:
        """
        Calculates the total number of embryos and 96-well plates required
        based on the current configuration in the table.
        """
        replicates = self._safe_get_int(self.replicates_spin, 1)
        per_plate_wells = 0
        per_replicate_wells = 0
        for row in range(self.table.rowCount()):
            wells_widget = self.table.cellWidget(row, 3)
            per_plate_widget = self.table.cellWidget(row, 4).findChild(QCheckBox)
            if wells_widget and per_plate_widget:
                if per_plate_widget.isChecked():
                    per_plate_wells += wells_widget.value()
                else:
                    per_replicate_wells += wells_widget.value() * replicates

        available_wells_per_plate = self._plate_size - per_plate_wells
        min_plates = 1
        if available_wells_per_plate > 0 and per_replicate_wells > 0:
            min_plates = math.ceil(per_replicate_wells / available_wells_per_plate)
        elif per_plate_wells > 0 and per_replicate_wells == 0:
            min_plates = 1
        elif available_wells_per_plate <= 0 and per_replicate_wells > 0:
            min_plates = -1

        total_embryos = per_replicate_wells + (per_plate_wells * (min_plates if min_plates > 0 else 1))

        if min_plates != -1:
            self.req_box.setTitle(f"Project Requirements (Min Plates: {min_plates})")
            self.total_embryos_label.setText(f"Total Embryos: {total_embryos}")
        else:
            self.req_box.setTitle("Project Requirements (Error!)")
            self.total_embryos_label.setText("Too many 'per plate' wells")

    def _get_concentration_data(self) -> List[Dict[str, Any]]:
        """
        Gathers all concentration data from the table into a list of dictionaries.

        Returns:
            List[Dict[str, Any]]: A list where each dictionary represents a
                                  concentration group's settings.
        """
        replicates = self._safe_get_int(self.replicates_spin, 1)
        concentrations = []
        for row in range(self.table.rowCount()):
            color_button = self.table.cellWidget(row, 5)
            style = color_button.styleSheet()
            color_hex = style.split("background-color: ")[1].split(";")[0]

            display_type = self.table.item(row, 1).text()
            internal_type = self.REVERSE_TYPE_MAP.get(display_type, display_type)

            concentrations.append({
                "id": self.table.item(row, 0).text(),
                "type": internal_type,
                "value": self.table.cellWidget(row, 2).value(),
                "replicates": replicates,
                "wells": self.table.cellWidget(row, 3).value(),
                "per_plate": self.table.cellWidget(row, 4).findChild(QCheckBox).isChecked(),
                "color": color_hex
            })
        return concentrations

    def _save_settings(self) -> None:
        """
        Saves the current concentration settings via the ProjectManager (when editing)
        and emits a signal indicating that changes were made.
        """
        if not self.is_editing:
            return

        concentrations = self._get_concentration_data()
        conc_settings = self.data.get("concentration_settings", {})
        required_embryos = conc_settings.get("required_embryos", 20)
        required_plates = conc_settings.get("required_plates", 1)

        if self._manager is not None:
            self._manager.set_concentrations(concentrations, required_embryos, required_plates)
        else:
            self.data["concentration_settings"] = {"concentrations": concentrations}

        self.project_settings_changed.emit()

    def _finalize_project(self) -> None:
        """
        Finalizes the project data with the current concentration settings
        and emits a signal that the project is ready to be created.
        """
        final_project_data = self.data.copy()
        final_project_data["concentration_settings"] = {"concentrations": self._get_concentration_data()}
        self.project_finalized.emit(final_project_data)

    def _populate_from_data(self, settings: Dict) -> None:
        """
        Populates the entire page with data from an existing project's settings.

        Args:
            settings (Dict): The concentration_settings dictionary from a project file.
        """
        concentrations = settings.get("concentrations", [])
        counts = {"Control": 0, "Substrate": 0, "Solvent Control": 0, "Positive Control": 0}
        replicates = self.DEFAULT_REPLICATES
        if concentrations:
            replicates = concentrations[0].get('replicates', self.DEFAULT_REPLICATES)
            for conc in concentrations:
                # Use internal type for counting
                internal_type = conc.get('type', '')
                if internal_type in counts:
                    counts[internal_type] += 1

        self.controls_spin.setText(str(counts["Control"]))
        self.concentrations_spin.setText(str(counts["Substrate"]))
        self.solvents_spin.setText(str(counts["Solvent Control"]))
        self.positive_controls_spin.setText(str(counts["Positive Control"]))
        self.replicates_spin.setText(str(replicates))

        self.table.setRowCount(len(concentrations))
        for i, item in enumerate(concentrations):
            self._populate_row(i, item['id'], item['type'], data=item)

        self._calculate_requirements()