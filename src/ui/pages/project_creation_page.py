# project_creation_page.py
import math
from collections import Counter
from typing import Dict, Any, List, Tuple
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             QFrame, QTableWidget, QHeaderView, QTableWidgetItem,
                             QGridLayout, QComboBox, QDoubleSpinBox, QMessageBox,
                             QGroupBox, QColorDialog, QCheckBox, QScrollArea, QSpinBox,
                             QSizePolicy, QTextEdit)
from PySide6.QtCore import Qt, Signal, QLocale, QPropertyAnimation, QEasingCurve, QDate
from PySide6.QtGui import QColor, QIntValidator, QBrush, QIcon

from src.ui.components import FloatingLabelLineEdit, FlexibleDoubleValidator, PlateFormatSelector
from src.core.utils import resource_path

class ProjectCreationPage(QWidget):
    """
    A comprehensive page for creating a new scientific project from scratch.
    It combines metadata entry, concentration planning, serial dilution
    calculation, and requirement analysis into a single, unified interface.
    """
    project_created = Signal(dict)

    # Centralized Constants
    ROW_HEIGHT = 50
    DEFAULT_WELLS = 20
    DEFAULT_REPLICATES = 3
    COLOR_POOL = {
        "control": ["#0078D4", "#005A9E", "#3399FF"],
        "solvent": ["#28A745", "#1E7E34", "#66BB6A"],
        "positive_control": ["#D946EF", "#B91C1C"],
        "substrate": ["#E67E22", "#D35400", "#F39C12", "#8E44AD",
                      "#9B59B6", "#16A085", "#1ABC9C", "#C0392B",
                      "#E74C3C", "#F1C40F"]
    }

    def __init__(self, parent: QWidget = None):
        """
        Initializes the ProjectCreationPage widget.

        Args:
            parent (QWidget, optional): The parent widget. Defaults to None.
        """
        super().__init__(parent)
        self.min_plates_required = 1
        self._last_calc_state = None
        self._row_connections = {}
        self._plate_size = 96   # updated when format selector changes
        self._init_ui()
        self._update_table_structure()

    def _init_ui(self) -> None:
        """
        Initializes and lays out all the user interface components for the page.
        """
        main_layout = QHBoxLayout(self)
        left_panel = QWidget(); left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(20, 20, 20, 20); left_layout.setSpacing(20)
        conc_group = QGroupBox("Concentration Plan"); conc_layout = QVBoxLayout(conc_group)
        self.table = QTableWidget(); self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["ID", "Type", "Conc.", "Wells/Rep", "Per Plate", "Color"])
        _header = self.table.horizontalHeader()
        _header.setSectionResizeMode(QHeaderView.Interactive)
        _header.setMinimumSectionSize(60)
        _header.resizeSection(0, 45)
        _header.resizeSection(1, 90)
        _header.resizeSection(2, 100)
        _header.resizeSection(3, 85)
        _header.resizeSection(4, 80)
        _header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table.verticalHeader().setVisible(False); self.table.verticalHeader().setDefaultSectionSize(self.ROW_HEIGHT)
        conc_layout.addWidget(self.table); left_layout.addWidget(conc_group)

        right_scroll_area = QScrollArea()
        right_scroll_area.setMinimumWidth(400)
        right_scroll_area.setMaximumWidth(550)
        right_scroll_area.setWidgetResizable(True); right_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right_panel_content = QWidget(); right_layout = QVBoxLayout(right_panel_content)

        # Metadata Group 
        meta_group = QGroupBox("Project Metadata"); meta_layout = QVBoxLayout(meta_group)
        self.name_input = FloatingLabelLineEdit("Project Name"); self.name_input.setObjectName("name_input")
        self.name_error_label = QLabel(); self.name_error_label.setObjectName("ErrorLabel")
        self.user_input = FloatingLabelLineEdit("Main Researcher"); self.user_input.setObjectName("user_input")
        self.user_error_label = QLabel(); self.user_error_label.setObjectName("ErrorLabel")
        self.substance_input = FloatingLabelLineEdit("Substance"); self.substance_input.setObjectName("substance_input")
        self.substance_error_label = QLabel(); self.substance_error_label.setObjectName("ErrorLabel")
        self.conc_unit_input = FloatingLabelLineEdit("Concentration Unit (e.g., mg/L)")
        
        meta_layout.addWidget(self.name_input); meta_layout.addWidget(self.name_error_label)
        meta_layout.addWidget(self.user_input); meta_layout.addWidget(self.user_error_label)
        meta_layout.addWidget(self.substance_input); meta_layout.addWidget(self.substance_error_label)
        meta_layout.addWidget(self.conc_unit_input)

        # Substance Details Panel (Collapsible) 
        self.toggle_substance_btn = self._create_toggle_button("Substance Details")
        self.substance_details_panel = self._create_substance_details_panel()
        self.toggle_substance_btn.clicked.connect(lambda: self._toggle_panel(self.toggle_substance_btn, self.substance_details_panel))
        meta_layout.addWidget(self.toggle_substance_btn, 0, Qt.AlignLeft)
        meta_layout.addWidget(self.substance_details_panel)

        # Test Conditions Panel (Collapsible) 
        self.toggle_conditions_btn = self._create_toggle_button("Water Quality & Conditions")
        self.conditions_panel = self._create_conditions_panel()
        self.toggle_conditions_btn.clicked.connect(lambda: self._toggle_panel(self.toggle_conditions_btn, self.conditions_panel))
        meta_layout.addWidget(self.toggle_conditions_btn, 0, Qt.AlignLeft)
        meta_layout.addWidget(self.conditions_panel)

        self.days_input = FloatingLabelLineEdit("Number of Days"); self.days_input.setObjectName("days_input"); self.days_input.setValidator(QIntValidator(1, 365, self)); self.days_input.setText("4")
        self.days_error_label = QLabel(); self.days_error_label.setObjectName("ErrorLabel")
        meta_layout.addWidget(self.days_input); meta_layout.addWidget(self.days_error_label)

        # Groups Definition 
        group_box = QGroupBox("Define Groups"); grid = QGridLayout(group_box); grid.setSpacing(15)
        self.controls_spin = FloatingLabelLineEdit("Controls (Co)"); self.controls_spin.setValidator(QIntValidator(1, 10, self)); self.controls_spin.setText("1")
        self.concentrations_spin = FloatingLabelLineEdit("Concentrations (C)"); self.concentrations_spin.setValidator(QIntValidator(1, 20, self)); self.concentrations_spin.setText("5")
        self.solvents_spin = FloatingLabelLineEdit("Solvent Controls (SC)"); self.solvents_spin.setValidator(QIntValidator(0, 10, self)); self.solvents_spin.setText("1")
        self.positive_controls_spin = FloatingLabelLineEdit("Positive Controls (PC)"); self.positive_controls_spin.setValidator(QIntValidator(0, 10, self)); self.positive_controls_spin.setText("1")

        grid.addWidget(self.controls_spin, 0, 0, 1, 2); grid.addWidget(self.concentrations_spin, 1, 0, 1, 2)
        grid.addWidget(self.solvents_spin, 2, 0, 1, 2); grid.addWidget(self.positive_controls_spin, 3, 0, 1, 2)
        self.controls_spin.line_edit.textChanged.connect(self._update_table_structure)
        self.concentrations_spin.line_edit.textChanged.connect(self._update_table_structure)
        self.solvents_spin.line_edit.textChanged.connect(self._update_table_structure)
        self.positive_controls_spin.line_edit.textChanged.connect(self._update_table_structure)
        self.positive_controls_spin.line_edit.textChanged.connect(self._on_positive_controls_changed)

        # Serial Dilution Calculator 
        calc_box = QGroupBox("Serial Dilution Calculator"); grid_calc = QGridLayout(calc_box); grid_calc.setSpacing(15)
        self.factor_spin = FloatingLabelLineEdit("Dilution Factor"); self.factor_spin.setObjectName("factor_spin"); self.factor_spin.setValidator(FlexibleDoubleValidator(1.01, 100.0, 2, self)); self.factor_spin.setText("2.2")
        self.highest_conc_spin = FloatingLabelLineEdit("Highest Concentration"); self.highest_conc_spin.setObjectName("highest_conc_spin"); self.highest_conc_spin.setValidator(FlexibleDoubleValidator(0.0001, 10000.0, 4, self)); self.highest_conc_spin.setText("100.0")
        generate_btn = QPushButton("Generate Series"); generate_btn.clicked.connect(self._generate_series)
        grid_calc.addWidget(self.factor_spin, 0, 0, 1, 2); grid_calc.addWidget(self.highest_conc_spin, 1, 0, 1, 2); grid_calc.addWidget(generate_btn, 2, 0, 1, 2)

        # Project Requirements
        self.req_box = QGroupBox("Project Requirements"); grid_req = QGridLayout(self.req_box); grid_req.setSpacing(10)
        format_label = QLabel("Plate Format")
        self.plate_format_selector = PlateFormatSelector()
        self.plate_format_selector.format_changed.connect(self._on_format_changed)
        self.replicates_spin = FloatingLabelLineEdit("Replicates per Group"); self.replicates_spin.setObjectName("replicates_spin"); self.replicates_spin.setValidator(QIntValidator(1, 100, self)); self.replicates_spin.setText(str(self.DEFAULT_REPLICATES))
        self.replicates_spin.line_edit.textChanged.connect(self._calculate_requirements)
        self.replicates_error_label = QLabel(); self.replicates_error_label.setObjectName("ErrorLabel")
        self.num_plates_spin = FloatingLabelLineEdit("Number of Plates"); self.num_plates_spin.setObjectName("num_plates_spin"); self.num_plates_spin.setValidator(QIntValidator(1, 100, self)); self.num_plates_spin.setText("1")
        self.num_plates_spin.line_edit.textChanged.connect(self._update_badge_status)
        self.num_plates_error_label = QLabel(); self.num_plates_error_label.setObjectName("ErrorLabel")
        self.plate_badge = QLabel("✓ OK"); self.plate_badge.setObjectName("PlateBadge"); self.plate_badge.setProperty("status", "ok")
        plates_layout = QHBoxLayout(); plates_layout.setSpacing(10); plates_layout.addWidget(self.num_plates_spin, 1); plates_layout.addWidget(self.plate_badge, 0)
        self.total_embryos_label = QLabel("Total Embryos: 0")
        grid_req.addWidget(format_label, 0, 0, 1, 2)
        grid_req.addWidget(self.plate_format_selector, 1, 0, 1, 2)
        grid_req.addWidget(self.replicates_spin, 2, 0, 1, 2); grid_req.addWidget(self.replicates_error_label, 3, 0, 1, 2)
        grid_req.addLayout(plates_layout, 4, 0, 1, 2); grid_req.addWidget(self.num_plates_error_label, 5, 0, 1, 2)
        grid_req.addWidget(self.total_embryos_label, 6, 0, 1, 2)
        
        finish_btn = QPushButton("Create Project"); finish_btn.setObjectName("PrimaryButton"); finish_btn.clicked.connect(self._finalize_project)
        
        right_layout.addWidget(meta_group); right_layout.addWidget(group_box); right_layout.addWidget(calc_box)
        right_layout.addWidget(self.req_box); right_layout.addStretch(); right_layout.addWidget(finish_btn)
        right_panel_content.setLayout(right_layout); right_scroll_area.setWidget(right_panel_content)
        
        main_layout.addWidget(left_panel, 2)
        main_layout.addWidget(right_scroll_area, 3)
        
        self._on_positive_controls_changed()

    def _create_toggle_button(self, text: str) -> QPushButton:
        """Helper to create a consistent toggle button for collapsible panels."""
        button = QPushButton(text)
        button.setObjectName("LinkButton")
        button.setCheckable(True)
        button.setIcon(QIcon(resource_path("resources/icons/chevron-down-dark.svg")))
        return button

    def _create_substance_details_panel(self) -> QFrame:
        """Creates the collapsible panel for advanced substance details."""
        panel = QFrame()
        layout = QGridLayout(panel)
        layout.setContentsMargins(10, 10, 0, 10)
        self.cas_input = FloatingLabelLineEdit("CAS Number")
        self.mw_input = FloatingLabelLineEdit("Molecular Weight")
        self.purity_input = FloatingLabelLineEdit("Purity (%)")
        self.purity_input.setValidator(FlexibleDoubleValidator(0, 100.0, 2, self))
        self.supplier_input = FloatingLabelLineEdit("Supplier/Manufacturer")
        self.appearance_input = FloatingLabelLineEdit("Physical Appearance")
        self.solubility_input = FloatingLabelLineEdit("Water Solubility")
        self.iupac_input = FloatingLabelLineEdit("IUPAC Name")
        self.solvent_used_input = FloatingLabelLineEdit("Solvent Used (if any)")
        self.positive_control_substance_input = FloatingLabelLineEdit("Positive Control Substance")
        
        layout.addWidget(self.appearance_input, 0, 0); layout.addWidget(self.solubility_input, 0, 1)
        layout.addWidget(self.iupac_input, 1, 0); layout.addWidget(self.cas_input, 1, 1)
        layout.addWidget(self.mw_input, 2, 0); layout.addWidget(self.purity_input, 2, 1)
        layout.addWidget(self.supplier_input, 3, 0); layout.addWidget(self.solvent_used_input, 3, 1)
        layout.addWidget(self.positive_control_substance_input, 4, 0, 1, 2)
        
        panel.setMaximumHeight(0)
        return panel

    def _create_conditions_panel(self) -> QFrame:
        """Creates the collapsible panel for water quality and test conditions."""
        panel = QFrame()
        layout = QGridLayout(panel)
        layout.setContentsMargins(10, 10, 0, 10)
        self.water_type_combo = QComboBox(); self.water_type_combo.addItems(["Reconstituted (ISO)", "Deionized", "Natural Surface Water"])
        self.ph_input = FloatingLabelLineEdit("pH")
        self.hardness_input = FloatingLabelLineEdit("Hardness (mg/L CaCO₃)")
        self.conductivity_input = FloatingLabelLineEdit("Conductivity (µS/cm)")
        self.temperature_input = FloatingLabelLineEdit("Temperature (°C)")
        self.oxygen_input = FloatingLabelLineEdit("Dissolved Oxygen")
        self.photoperiod_input = FloatingLabelLineEdit("Photoperiod (L:D)")
        layout.addWidget(QLabel("Water Type:"), 0, 0, alignment=Qt.AlignRight); layout.addWidget(self.water_type_combo, 0, 1)
        layout.addWidget(self.ph_input, 1, 0); layout.addWidget(self.hardness_input, 1, 1)
        layout.addWidget(self.conductivity_input, 2, 0); layout.addWidget(self.temperature_input, 2, 1)
        layout.addWidget(self.oxygen_input, 3, 0); layout.addWidget(self.photoperiod_input, 3, 1)
        panel.setMaximumHeight(0)
        return panel

    def _toggle_panel(self, button: QPushButton, panel: QFrame) -> None:
        """Generic method to expand/collapse a panel with animation."""
        is_expanding = button.isChecked()
        button.setIcon(QIcon(resource_path(
            "resources/icons/chevron-up-dark.svg" if is_expanding else "resources/icons/chevron-down-dark.svg"
        )))
        start_height = panel.height()
        end_height = panel.sizeHint().height() if is_expanding else 0
        self.animation = QPropertyAnimation(panel, b"maximumHeight")
        self.animation.setDuration(300)
        self.animation.setStartValue(start_height)
        self.animation.setEndValue(end_height)
        self.animation.setEasingCurve(QEasingCurve.InOutCubic)
        self.animation.start()
        
    def _on_positive_controls_changed(self):
        """Shows or hides the positive control substance input based on count."""
        num_positive = self._safe_get_int(self.positive_controls_spin, 0)
        is_visible = num_positive > 0
        self.positive_control_substance_input.setVisible(is_visible)

    def _safe_get_int(self, widget: QWidget, default: int = 0) -> int:
        """Safely gets an integer from a widget's text."""
        try: return int(widget.text()) if widget.text() else default
        except (ValueError, TypeError): return default

    def _safe_get_float(self, widget: QWidget, default: float = 0.0) -> float:
        """Safely gets a float from a widget's text."""
        try:
            text = widget.text().replace(',', '.', 1)
            return float(text) if text else default
        except (ValueError, TypeError): return default

    def _update_table_structure(self) -> None:
        """
        Efficiently updates the table to match the required number of rows,
        adding or removing only as needed, and re-assigns IDs and types.
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
            self.table.cellWidget(row_cursor, 1).setText("Control")
            self.table.cellWidget(row_cursor, 2).setEnabled(False)
            row_cursor += 1
        for i in range(num_concentrations):
            self.table.item(row_cursor, 0).setText(f"C{i+1}")
            self.table.cellWidget(row_cursor, 1).setText("Substrate")
            row_cursor += 1
        for i in range(num_solvents):
            self.table.item(row_cursor, 0).setText(f"SC{i+1}")
            self.table.cellWidget(row_cursor, 1).setText("Solvent Control")
            row_cursor += 1
        for i in range(num_positive):
            self.table.item(row_cursor, 0).setText(f"PC{i+1}")
            self.table.cellWidget(row_cursor, 1).setText("Positive Control")
            row_cursor += 1
        
        self.table.blockSignals(False); self._calculate_requirements(); self._validate_table_ids()

    def _disconnect_row_signals(self, row: int) -> None:
        """Disconnect all managed signals for a specific row."""
        if row in self._row_connections:
            for connection in self._row_connections[row]:
                try: self.disconnect(connection)
                except (TypeError, RuntimeError): pass
            del self._row_connections[row]

    def _populate_row(self, row: int, item_id: str, item_type: str, data: Dict = None) -> None:
        """
        Populates a single table row with widgets, data, and signal connections,
        assigning colors based on the group type.
        """
        self._disconnect_row_signals(row)
        self._row_connections[row] = []

        self.table.setRowHeight(row, self.ROW_HEIGHT)
        if not self.table.item(row, 0): self.table.setItem(row, 0, QTableWidgetItem(item_id))
        else: self.table.item(row, 0).setText(item_id)
        
        type_label = QLabel(item_type); type_label.setAlignment(Qt.AlignCenter)
        self.table.setCellWidget(row, 1, type_label)

        conc_spinbox = QDoubleSpinBox(); conc_spinbox.setRange(0, 10000); conc_spinbox.setDecimals(2)
        conc_spinbox.setMinimumWidth(90); conc_spinbox.setEnabled(True)
        if item_type == "Control":
            conc_spinbox.setValue(0.0); conc_spinbox.setEnabled(False)
        self.table.setCellWidget(row, 2, conc_spinbox)

        wells_spinbox = QSpinBox(); wells_spinbox.setRange(1, self._plate_size); wells_spinbox.setValue(self.DEFAULT_WELLS); wells_spinbox.setMinimumWidth(70); self.table.setCellWidget(row, 3, wells_spinbox)
        per_plate_widget = QWidget(); per_plate_layout = QHBoxLayout(per_plate_widget); per_plate_layout.setAlignment(Qt.AlignCenter); per_plate_layout.setContentsMargins(0,0,0,0); per_plate_cb = QCheckBox(); per_plate_layout.addWidget(per_plate_cb); self.table.setCellWidget(row, 4, per_plate_widget)
        color_button = QPushButton()
        
        color_index = 0
        actual_type = self.table.cellWidget(row, 1).text() if self.table.cellWidget(row, 1) else item_type
        
        if actual_type == "Control":
            color_index = row
            color = QColor(self.COLOR_POOL["control"][color_index % len(self.COLOR_POOL["control"])])
        elif actual_type == "Solvent Control":
            color_index = row - self._safe_get_int(self.controls_spin) - self._safe_get_int(self.concentrations_spin)
            color = QColor(self.COLOR_POOL["solvent"][color_index % len(self.COLOR_POOL["solvent"])])
        elif actual_type == "Positive Control":
            color_index = row - self._safe_get_int(self.controls_spin) - self._safe_get_int(self.concentrations_spin) - self._safe_get_int(self.solvents_spin)
            color = QColor(self.COLOR_POOL["positive_control"][color_index % len(self.COLOR_POOL["positive_control"])])
        else: # Substrate
            color_index = row - self._safe_get_int(self.controls_spin)
            color = QColor(self.COLOR_POOL["substrate"][color_index % len(self.COLOR_POOL["substrate"])])
            
        color_button.setStyleSheet(f"background-color: {color.name()}; border-radius: 5px;"); color_button.setProperty("row", row); self.table.setCellWidget(row, 5, color_button)
        if data:
            type_label.setText(data.get("type", item_type))
            conc_spinbox.setValue(data.get("value", 0.0))
            wells_spinbox.setValue(data.get("wells", self.DEFAULT_WELLS))
            per_plate_cb.setChecked(data.get("per_plate", False))

        self._row_connections[row].extend([
            conc_spinbox.valueChanged.connect(self._calculate_requirements),
            wells_spinbox.valueChanged.connect(self._calculate_requirements),
            per_plate_cb.toggled.connect(self._calculate_requirements),
            color_button.clicked.connect(self._open_color_picker)
        ])

    def _open_color_picker(self) -> None:
        """Opens a color dialog to change the color of a group label."""
        button = self.sender(); row = button.property("row"); style = button.styleSheet(); current_color_hex = style.split("background-color: ")[1].split(";")[0]; current_color = QColor(current_color_hex); new_color = QColorDialog.getColor(current_color, self)
        if new_color.isValid(): button.setStyleSheet(f"background-color: {new_color.name()}; border-radius: 5px;")

    def _generate_series(self) -> None:
        """Calculates and applies a serial dilution to the substrate groups."""
        highest_conc = self._safe_get_float(self.highest_conc_spin, 0.0)
        if highest_conc <= 0: self._set_field_validity(self.highest_conc_spin, False, "Highest concentration must be > 0."); return
        self._set_field_validity(self.highest_conc_spin, True)
        factor = self._safe_get_float(self.factor_spin, 1.0)
        if factor <= 1.0: self._set_field_validity(self.factor_spin, False, "Dilution factor must be > 1."); return
        self._set_field_validity(self.factor_spin, True)
        num_concentrations = self._safe_get_int(self.concentrations_spin, 0)
        concentrations = [round(highest_conc / (factor ** i), 4) for i in range(num_concentrations)]; concentrations.reverse()
        substrate_rows = [row for row in range(self.table.rowCount()) if self.table.cellWidget(row, 1).text() == "Substrate"]
        for i, row_index in enumerate(substrate_rows):
            if i < len(concentrations): self.table.cellWidget(row_index, 2).setValue(concentrations[i])

    def _on_format_changed(self, fmt: str) -> None:
        """Update plate size and spinbox ranges when the format selector changes."""
        from src.core.constants import PLATE_FORMATS
        rows, cols = PLATE_FORMATS[fmt]
        self._plate_size = rows * cols
        self._update_all_wells_spinboxes()
        self._last_calc_state = None
        self._calculate_requirements()

    def _update_all_wells_spinboxes(self) -> None:
        """Clamp all wells spinboxes to the current plate size."""
        for row in range(self.table.rowCount()):
            spinbox = self.table.cellWidget(row, 3)
            if spinbox:
                spinbox.setRange(1, self._plate_size)
                if spinbox.value() > self._plate_size:
                    spinbox.setValue(self._plate_size)

    def _get_current_calc_state(self) -> Tuple:
        """
        Creates a tuple representing the current state of table values
        relevant to requirement calculations, for caching.
        """
        state = [self._safe_get_int(self.replicates_spin, self.DEFAULT_REPLICATES)]
        for row in range(self.table.rowCount()): state.append(self.table.cellWidget(row, 3).value()); state.append(self.table.cellWidget(row, 4).findChild(QCheckBox).isChecked())
        return tuple(state)

    def _calculate_requirements(self) -> None:
        """
        Calculates the required embryos and plates, caching the result to
        avoid redundant computations. Updates the UI accordingly.
        """
        current_state = self._get_current_calc_state()
        if current_state == self._last_calc_state: return
        self._last_calc_state = current_state
        replicates = self._safe_get_int(self.replicates_spin, 1); per_plate_wells = 0; per_replicate_wells = 0
        for row in range(self.table.rowCount()):
            wells_widget = self.table.cellWidget(row, 3); per_plate_widget = self.table.cellWidget(row, 4).findChild(QCheckBox)
            if wells_widget and per_plate_widget:
                if per_plate_widget.isChecked(): per_plate_wells += wells_widget.value()
                else: per_replicate_wells += wells_widget.value() * replicates
        available_wells_per_plate = self._plate_size - per_plate_wells; min_plates = 1
        if available_wells_per_plate > 0 and per_replicate_wells > 0: min_plates = math.ceil(per_replicate_wells / available_wells_per_plate)
        elif per_plate_wells > 0 and per_replicate_wells == 0: min_plates = 1
        elif available_wells_per_plate <= 0 and per_replicate_wells > 0: min_plates = -1
        self.min_plates_required = min_plates
        total_embryos = per_replicate_wells + (per_plate_wells * (min_plates if min_plates > 0 else 1))
        self.req_box.setTitle("Project Requirements")
        if min_plates != -1: self.num_plates_spin.line_edit.setPlaceholderText(f"Number of Plates (Min: {min_plates})"); self.total_embryos_label.setText(f"Total Embryos: {total_embryos}")
        else: self.num_plates_spin.line_edit.setPlaceholderText("Number of Plates (Error)"); self.total_embryos_label.setText("Too many 'per plate' wells")
        self._update_badge_status()

    def _update_badge_status(self) -> None:
        """Updates the visual status of the plate requirement badge."""
        current_plates = self._safe_get_int(self.num_plates_spin, 0); status = "hidden"; text = ""
        if self.min_plates_required == -1: status = "error"; text = "Error"
        elif current_plates < self.min_plates_required: status = "warning"; text = f"LOW (Min: {self.min_plates_required})"
        elif current_plates >= self.min_plates_required: status = "ok"; text = "✓ OK"
        self.plate_badge.setText(text); self.plate_badge.setProperty("status", status); self.plate_badge.setVisible(status != "hidden")
        self.plate_badge.style().unpolish(self.plate_badge); self.plate_badge.style().polish(self.plate_badge)

    def _validate_table_ids(self) -> None:
        """Checks for and visually flags duplicate IDs in the table."""
        ids = [self.table.item(row, 0).text() for row in range(self.table.rowCount())]; id_counts = Counter(ids)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if id_counts[item.text()] > 1: item.setBackground(QBrush(QColor("#9b212a")))
            else: item.setBackground(QBrush(Qt.transparent))

    def _set_field_validity(self, widget: QWidget, is_valid: bool, message: str = "") -> None:
        """
        Sets the visual validity state (border and error message) for a given widget.
        """
        error_label = getattr(self, f"{widget.objectName()}_error_label", None)
        widget.setProperty("invalid", not is_valid)
        if error_label: error_label.setText(message)
        widget.style().unpolish(widget); widget.style().polish(widget)

    def _validate_form(self) -> bool:
        """
        Validates all critical input fields before project finalization.
        Returns True if all fields are valid, False otherwise.
        """
        is_valid = True
        if not self.name_input.text().strip():
            is_valid = False
            self._set_field_validity(self.name_input, False, "Project Name is required.")
        else:
            self._set_field_validity(self.name_input, True)

        if not self.user_input.text().strip():
            is_valid = False
            self._set_field_validity(self.user_input, False, "Main Researcher is required.")
        else:
            self._set_field_validity(self.user_input, True)

        if not self.substance_input.text().strip():
            is_valid = False
            self._set_field_validity(self.substance_input, False, "Substance is required.")
        else:
            self._set_field_validity(self.substance_input, True)
            
        return is_valid

    def _finalize_project(self) -> None:
        """
        Gathers all data, validates it, and emits the `project_created`
        signal if validation passes.
        """
        if not self._validate_form():
            QMessageBox.warning(self, "Missing Information", "Please fill in all required fields before creating the project.")
            return
        
        substance_details = {
            "cas_number": self.cas_input.text(), "molecular_weight": self.mw_input.text(),
            "purity": self.purity_input.text(), "supplier": self.supplier_input.text(),
            "physical_appearance": self.appearance_input.text(), "water_solubility": self.solubility_input.text(),
            "iupac_name": self.iupac_input.text(), "solvent_used": self.solvent_used_input.text(),
            "positive_control_substance": self.positive_control_substance_input.text()
        }
        test_conditions = {
            "water_type": self.water_type_combo.currentText(), "ph": self.ph_input.text(),
            "hardness": self.hardness_input.text(), "conductivity": self.conductivity_input.text(),
            "temperature": self.temperature_input.text(), "dissolved_oxygen": self.oxygen_input.text(),
            "photoperiod": self.photoperiod_input.text()
        }
        
        concentrations = []
        for row in range(self.table.rowCount()):
            conc_data = {
                "id": self.table.item(row, 0).text(),
                "type": self.table.cellWidget(row, 1).text(),
                "value": self.table.cellWidget(row, 2).value(),
                "replicates": self._safe_get_int(self.replicates_spin, 1),
                "wells": self.table.cellWidget(row, 3).value(),
                "per_plate": self.table.cellWidget(row, 4).findChild(QCheckBox).isChecked(),
                "color": self.table.cellWidget(row, 5).palette().button().color().name()
            }
            concentrations.append(conc_data)
            
        total_embryos_txt = self.total_embryos_label.text().split(": "); total_embryos = int(total_embryos_txt[1]) if len(total_embryos_txt) > 1 else 0
        
        final_project_data = {
            "project_name": self.name_input.text().strip(),
            "main_researcher": self.user_input.text(),
            "substance": self.substance_input.text(),
            "concentration_unit": self.conc_unit_input.text(),
            "substance_details": substance_details,
            "test_conditions": test_conditions,
            "num_days": self._safe_get_int(self.days_input, 1), 
            "num_plates": self._safe_get_int(self.num_plates_spin, 1),
            "plate_format": self.plate_format_selector.selected_format,
            "concentration_settings": {
                "concentrations": concentrations, 
                "required_embryos": total_embryos, 
                "required_plates": self.min_plates_required
            },
            "start_date": QDate.currentDate().toString(Qt.ISODate),
            "test_organisms": {},
            "methodology": {},
            "report_notes": ""
        }
        self.project_created.emit(final_project_data)
