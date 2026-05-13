# project_settings_dialog.py
from typing import Dict, Any
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
                             QScrollArea, QWidget, QGridLayout, QGroupBox,
                             QLineEdit, QDateEdit, QSpinBox, QComboBox,
                             QDoubleSpinBox, QTextEdit, QMessageBox, QLabel,
                             QSizePolicy)
from PySide6.QtCore import Qt, Signal, QDate
from PySide6.QtGui import QCloseEvent

from src.core.project_manager import ProjectManager

class ProjectSettingsDialog(QDialog):
    """
    A modal dialog for editing the settings of an existing project.
    It allows modification of metadata, substance parameters, test conditions,
    and other report-related notes, following OECD guidelines.
    """
    settings_saved = Signal(bool) # Emits True if a critical change requires a UI reload

    def __init__(self, manager: ProjectManager, parent: QWidget = None):
        """
        Initializes the ProjectSettingsDialog.

        Args:
            manager (ProjectManager): The project data manager instance.
            parent (QWidget, optional): The parent widget. Defaults to None.
        """
        super().__init__(parent)
        self.manager = manager
        self.data_snapshot = self._get_data_snapshot()
        
        self.setWindowTitle("Project Settings")
        self.setMinimumSize(640, 560)
        
        self._init_ui()
        self._populate_fields()
        
        if parent:
            self.move(parent.geometry().center() - self.rect().center())

    def _init_ui(self) -> None:
        """Initializes and lays out all UI components of the dialog."""
        main_layout = QVBoxLayout(self)
        
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        content_widget = QWidget()
        scroll_area.setWidget(content_widget)
        
        content_layout = QVBoxLayout(content_widget)
        
        # Section 1: Project Information 
        project_info_group = QGroupBox("Project Information")
        project_info_layout = QGridLayout(project_info_group)
        self.name_input = QLineEdit()
        self.name_input.setToolTip("The official name of the research project.")
        self.researcher_input = QLineEdit()
        self.researcher_input.setToolTip("Name of the researcher responsible for the experiment.")
        self.conc_unit_input = QLineEdit()
        self.conc_unit_input.setToolTip("The unit for concentration values (e.g., mg/L, µM).")
        self.start_date_input = QDateEdit()
        self.start_date_input.setToolTip("The official start date of the experiment.")
        self.start_date_input.setCalendarPopup(True)
        self.start_date_input.setDisplayFormat("dd/MM/yyyy")
        self.days_input = QSpinBox()
        self.days_input.setToolTip("Total duration of the experiment in days. Changing this will reload the project view.")
        self.days_input.setRange(1, 365)
        project_info_layout.addWidget(QLabel("Project Name:"), 0, 0); project_info_layout.addWidget(self.name_input, 0, 1)
        project_info_layout.addWidget(QLabel("Researcher:"), 1, 0); project_info_layout.addWidget(self.researcher_input, 1, 1)
        project_info_layout.addWidget(QLabel("Concentration Unit:"), 2, 0); project_info_layout.addWidget(self.conc_unit_input, 2, 1)
        project_info_layout.addWidget(QLabel("Start Date:"), 3, 0); project_info_layout.addWidget(self.start_date_input, 3, 1)
        project_info_layout.addWidget(QLabel("Number of Days:"), 4, 0); project_info_layout.addWidget(self.days_input, 4, 1)
        content_layout.addWidget(project_info_group)


        # Section 2: Substance Parameters (Collapsible) 
        substance_group = QGroupBox("Substance Parameters")
        substance_group.setCheckable(True)
        substance_layout = QGridLayout(substance_group)
        self.substance_input = QLineEdit(); self.substance_input.setToolTip("Common name of the test substance.")
        self.cas_input = QLineEdit(); self.cas_input.setToolTip("CAS Registry Number of the substance.")
        self.mw_input = QLineEdit(); self.mw_input.setToolTip("Molecular Weight (e.g., g/mol).")
        self.purity_input = QDoubleSpinBox(); self.purity_input.setToolTip("Purity of the substance in percent."); self.purity_input.setRange(0, 100); self.purity_input.setSuffix(" %")
        self.supplier_input = QLineEdit(); self.supplier_input.setToolTip("Supplier or manufacturer of the substance.")
        self.appearance_input = QLineEdit(); self.appearance_input.setToolTip("Physical appearance of the substance (e.g., 'White crystalline powder').")
        self.solubility_input = QLineEdit(); self.solubility_input.setToolTip("Solubility in water (e.g., '10 mg/L at 20°C').")
        self.iupac_input = QLineEdit(); self.iupac_input.setToolTip("IUPAC name or other unique chemical identifier (SMILES, InChI).")
        self.solvent_used_input = QLineEdit(); self.solvent_used_input.setToolTip("Name and justification for the solvent used, if any.")
        self.positive_control_substance_input = QLineEdit(); self.positive_control_substance_input.setToolTip("The substance used as a positive control (e.g., 3,4-dichloroaniline).")
        
        substance_layout.addWidget(QLabel("Substance Name:"), 0, 0); substance_layout.addWidget(self.substance_input, 0, 1)
        substance_layout.addWidget(QLabel("CAS Number:"), 1, 0); substance_layout.addWidget(self.cas_input, 1, 1)
        substance_layout.addWidget(QLabel("IUPAC / SMILES / InChI:"), 2, 0); substance_layout.addWidget(self.iupac_input, 2, 1)
        substance_layout.addWidget(QLabel("Molecular Weight:"), 3, 0); substance_layout.addWidget(self.mw_input, 3, 1)
        substance_layout.addWidget(QLabel("Purity:"), 4, 0); substance_layout.addWidget(self.purity_input, 4, 1)
        substance_layout.addWidget(QLabel("Supplier:"), 5, 0); substance_layout.addWidget(self.supplier_input, 5, 1)
        substance_layout.addWidget(QLabel("Physical Appearance:"), 6, 0); substance_layout.addWidget(self.appearance_input, 6, 1)
        substance_layout.addWidget(QLabel("Water Solubility:"), 7, 0); substance_layout.addWidget(self.solubility_input, 7, 1)
        substance_layout.addWidget(QLabel("Solvent Used:"), 8, 0); substance_layout.addWidget(self.solvent_used_input, 8, 1)
        substance_layout.addWidget(QLabel("Positive Control Substance:"), 9, 0); substance_layout.addWidget(self.positive_control_substance_input, 9, 1)
        content_layout.addWidget(substance_group)


        # Section 3: Water Quality & Conditions (Collapsible) 
        conditions_group = QGroupBox("Water Quality & Test Conditions")
        conditions_group.setCheckable(True)
        conditions_layout = QGridLayout(conditions_group)
        self.water_type_combo = QComboBox(); self.water_type_combo.addItems(["Reconstituted (ISO)", "Deionized", "Natural Surface Water"]); self.water_type_combo.setToolTip("Type of water used for dilution and controls.")
        self.ph_input = QLineEdit(); self.ph_input.setToolTip("pH of the dilution water (e.g., '7.5 ± 0.2').")
        self.hardness_input = QLineEdit(); self.hardness_input.setToolTip("Total hardness of the water, expressed as mg/L CaCO₃.")
        self.conductivity_input = QLineEdit(); self.conductivity_input.setToolTip("Electrical conductivity of the water (e.g., '550 µS/cm').")
        self.photoperiod_input = QLineEdit(); self.photoperiod_input.setToolTip("The light:dark cycle for the experiment (e.g., '14h light / 10h dark').")
        self.temperature_input = QLineEdit(); self.temperature_input.setToolTip("Temperature range of the test medium (e.g., '26 ± 1 °C').")
        self.oxygen_input = QLineEdit(); self.oxygen_input.setToolTip("Dissolved oxygen concentration (e.g., '> 60% saturation').")
        self.mortality_input = QDoubleSpinBox(); self.mortality_input.setToolTip("Acceptable mortality rate in the control group for a valid test."); self.mortality_input.setRange(0, 100); self.mortality_input.setValue(10.0); self.mortality_input.setSuffix(" %")
        conditions_layout.addWidget(QLabel("Water Type:"), 0, 0); conditions_layout.addWidget(self.water_type_combo, 0, 1)
        conditions_layout.addWidget(QLabel("pH:"), 1, 0); conditions_layout.addWidget(self.ph_input, 1, 1)
        conditions_layout.addWidget(QLabel("Hardness (mg/L CaCO₃):"), 2, 0); conditions_layout.addWidget(self.hardness_input, 2, 1)
        conditions_layout.addWidget(QLabel("Conductivity (µS/cm):"), 3, 0); conditions_layout.addWidget(self.conductivity_input, 3, 1)
        conditions_layout.addWidget(QLabel("Temperature (°C):"), 4, 0); conditions_layout.addWidget(self.temperature_input, 4, 1)
        conditions_layout.addWidget(QLabel("Dissolved Oxygen:"), 5, 0); conditions_layout.addWidget(self.oxygen_input, 5, 1)
        conditions_layout.addWidget(QLabel("Photoperiod:"), 6, 0); conditions_layout.addWidget(self.photoperiod_input, 6, 1)
        conditions_layout.addWidget(QLabel("Acceptable Control Mortality:"), 7, 0); conditions_layout.addWidget(self.mortality_input, 7, 1)
        content_layout.addWidget(conditions_group)

        # Section 4: Test Organisms (Collapsible) 
        organisms_group = QGroupBox("Test Organisms")
        organisms_group.setCheckable(True)
        organisms_layout = QGridLayout(organisms_group)
        self.strain_input = QLineEdit(); self.strain_input.setToolTip("Strain of the zebrafish used (e.g., 'Wild-type AB/Tübingen').")
        self.source_input = QLineEdit(); self.source_input.setToolTip("Source of the brood stock (e.g., 'Zebrafish International Resource Center').")
        self.collection_method_input = QTextEdit(); self.collection_method_input.setToolTip("Description of the egg collection and initial handling procedures.")
        self.collection_method_input.setMinimumHeight(80)
        organisms_layout.addWidget(QLabel("Species:"), 0, 0); organisms_layout.addWidget(QLabel("<i>Danio rerio</i>"), 0, 1)
        organisms_layout.addWidget(QLabel("Strain:"), 1, 0); organisms_layout.addWidget(self.strain_input, 1, 1)
        organisms_layout.addWidget(QLabel("Source:"), 2, 0); organisms_layout.addWidget(self.source_input, 2, 1)
        organisms_layout.addWidget(QLabel("Egg Collection Method:"), 3, 0); organisms_layout.addWidget(self.collection_method_input, 3, 1)
        content_layout.addWidget(organisms_group)
        
        # Section 5: Methodology (Collapsible) 
        methodology_group = QGroupBox("Methodology")
        methodology_group.setCheckable(True)
        methodology_layout = QGridLayout(methodology_group)
        self.test_procedure_combo = QComboBox(); self.test_procedure_combo.addItems(["Static", "Semi-static renewal", "Flow-through"]); self.test_procedure_combo.setToolTip("The exposure system used during the test.")
        self.solution_prep_input = QTextEdit(); self.solution_prep_input.setToolTip("Description of how the stock and test solutions were prepared.")
        self.solution_prep_input.setMinimumHeight(80)
        self.selection_criteria_input = QTextEdit(); self.selection_criteria_input.setToolTip("Criteria for selecting embryos (e.g., 'fertilized, non-adhesive, <3 hpf').")
        self.selection_criteria_input.setMinimumHeight(80)
        methodology_layout.addWidget(QLabel("Test Procedure:"), 0, 0); methodology_layout.addWidget(self.test_procedure_combo, 0, 1)
        methodology_layout.addWidget(QLabel("Stock Solution Preparation:"), 1, 0, 1, 2); methodology_layout.addWidget(self.solution_prep_input, 2, 0, 1, 2)
        methodology_layout.addWidget(QLabel("Embryo Selection Criteria:"), 3, 0, 1, 2); methodology_layout.addWidget(self.selection_criteria_input, 4, 0, 1, 2)
        content_layout.addWidget(methodology_group)

        # Section 6: Report Notes 
        notes_group = QGroupBox("Notes for Report")
        notes_layout = QVBoxLayout(notes_group)
        self.notes_input = QTextEdit()
        self.notes_input.setToolTip("General notes and observations to be included in the final report.")
        self.notes_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        notes_layout.addWidget(self.notes_input)
        content_layout.addWidget(notes_group)
        
        content_layout.addStretch()

        # Bottom Buttons 
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        self.save_button = QPushButton("Save Changes")
        self.save_button.setObjectName("PrimaryButton")
        self.save_button.clicked.connect(self.accept)
        button_layout.addWidget(self.cancel_button)
        button_layout.addWidget(self.save_button)

        main_layout.addWidget(scroll_area)
        main_layout.addLayout(button_layout)

    def _get_data_snapshot(self) -> Dict[str, Any]:
        """Creates a snapshot of the current project data for dirty checking."""
        info = self.manager.get_project_info()
        sd = self.manager.get_substance_details()
        tc = self.manager.get_test_conditions()
        return {
            "project_name": info.get("project_name", ""),
            "user": info.get("main_researcher", ""),
            "concentration_unit": info.get("concentration_unit", "mg/L"),
            "start_date": info.get("start_date", QDate.currentDate().toString(Qt.ISODate)),
            "num_days": info.get("num_days", 1),
            "substance": info.get("substance", ""),
            "substance_details": dict(sd),
            "test_conditions": dict(tc),
            "test_organisms": dict(self.manager.get_test_organisms()),
            "methodology": dict(self.manager.get_methodology()),
            "report_notes": info.get("report_notes", ""),
        }

    def _gather_ui_data(self) -> Dict[str, Any]:
        """Gathers the current values from all UI fields into a dictionary."""
        return {
            "project_name": self.name_input.text(),
            "user": self.researcher_input.text(),
            "concentration_unit": self.conc_unit_input.text(),
            "start_date": self.start_date_input.date().toString(Qt.ISODate),
            "num_days": self.days_input.value(),
            "substance": self.substance_input.text(),
            "substance_details": {
                "cas_number": self.cas_input.text(), "molecular_weight": self.mw_input.text(),
                "purity": self.purity_input.value(), "supplier": self.supplier_input.text(),
                "physical_appearance": self.appearance_input.text(), "water_solubility": self.solubility_input.text(),
                "iupac_name": self.iupac_input.text(), "solvent_used": self.solvent_used_input.text(),
                "positive_control_substance": self.positive_control_substance_input.text()
            },
            "test_conditions": {
                "water_type": self.water_type_combo.currentText(), "ph": self.ph_input.text(),
                "hardness": self.hardness_input.text(), "conductivity": self.conductivity_input.text(),
                "temperature": self.temperature_input.text(),
                "dissolved_oxygen": self.oxygen_input.text(),
                "photoperiod": self.photoperiod_input.text(), 
                "acceptable_mortality": self.mortality_input.value()
            },
            "test_organisms": {
                "species": "Danio rerio",
                "strain": self.strain_input.text(), "source": self.source_input.text(),
                "collection_method": self.collection_method_input.toPlainText()
            },
            "methodology": {
                "test_procedure": self.test_procedure_combo.currentText(),
                "solution_preparation": self.solution_prep_input.toPlainText(),
                "selection_criteria": self.selection_criteria_input.toPlainText()
            },
            "report_notes": self.notes_input.toPlainText()
        }

    def _is_dirty(self) -> bool:
        """Checks if any fields have been modified since the dialog was opened."""
        return self._gather_ui_data() != self.data_snapshot

    def _populate_fields(self) -> None:
        """Pre-fills all input fields with data from the initial snapshot."""
        # General Info
        self.name_input.setText(self.data_snapshot["project_name"])
        self.researcher_input.setText(self.data_snapshot["user"])
        self.conc_unit_input.setText(self.data_snapshot["concentration_unit"])
        self.start_date_input.setDate(QDate.fromString(self.data_snapshot["start_date"], Qt.ISODate))
        self.days_input.setValue(self.data_snapshot["num_days"])
        self.substance_input.setText(self.data_snapshot["substance"])
        
        # Substance Details
        substance_details = self.data_snapshot["substance_details"]
        self.cas_input.setText(substance_details.get("cas_number", ""))
        self.mw_input.setText(substance_details.get("molecular_weight", ""))
        
        # Safely set purity
        purity_value = substance_details.get("purity", 99.0)
        if purity_value == "" or purity_value is None:
            purity_value = 99.0
        self.purity_input.setValue(float(purity_value))

        self.supplier_input.setText(substance_details.get("supplier", ""))
        self.appearance_input.setText(substance_details.get("physical_appearance", ""))
        self.solubility_input.setText(substance_details.get("water_solubility", ""))
        self.iupac_input.setText(substance_details.get("iupac_name", ""))
        self.solvent_used_input.setText(substance_details.get("solvent_used", ""))
        self.positive_control_substance_input.setText(substance_details.get("positive_control_substance", "3,4-dichloroaniline"))
        
        # Test Conditions
        test_conditions = self.data_snapshot["test_conditions"]
        self.water_type_combo.setCurrentText(test_conditions.get("water_type", "Reconstituted (ISO)"))
        self.ph_input.setText(test_conditions.get("ph", ""))
        self.hardness_input.setText(test_conditions.get("hardness", ""))
        self.conductivity_input.setText(test_conditions.get("conductivity", ""))
        self.temperature_input.setText(test_conditions.get("temperature", "26 ± 1 °C"))
        self.oxygen_input.setText(test_conditions.get("dissolved_oxygen", "> 60% saturation"))
        self.photoperiod_input.setText(test_conditions.get("photoperiod", ""))
        
        # Safely set acceptable mortality
        mortality_value = test_conditions.get("acceptable_mortality", 10.0)
        if mortality_value == "" or mortality_value is None:
            mortality_value = 10.0
        self.mortality_input.setValue(float(mortality_value))

        # Test Organisms
        test_organisms = self.data_snapshot["test_organisms"]
        self.strain_input.setText(test_organisms.get("strain", ""))
        self.source_input.setText(test_organisms.get("source", ""))
        self.collection_method_input.setText(test_organisms.get("collection_method", ""))
        
        # Methodology
        methodology = self.data_snapshot["methodology"]
        self.test_procedure_combo.setCurrentText(methodology.get("test_procedure", "Static"))
        self.solution_prep_input.setText(methodology.get("solution_preparation", ""))
        self.selection_criteria_input.setText(methodology.get("selection_criteria", ""))
        
        # Notes
        self.notes_input.setText(self.data_snapshot["report_notes"])

    def accept(self) -> None:
        """
        Gathers data, saves it to the ProjectManager, and closes the dialog.
        """
        current_data = self._gather_ui_data()
        is_critical_change = current_data["num_days"] != self.data_snapshot["num_days"]

        if is_critical_change:
            reply = QMessageBox.question(self, "Confirm Critical Changes",
                                         "Changing the number of days will reload the experiment view.\n\nAre you sure you want to continue?",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.No:
                return

        self.manager.update_project_info(
            project_name=current_data["project_name"],
            main_researcher=current_data["user"],
            concentration_unit=current_data["concentration_unit"],
            start_date=current_data["start_date"],
            num_days=current_data["num_days"],
            substance=current_data["substance"],
            report_notes=current_data["report_notes"],
        )
        self.manager.update_substance_details(**current_data["substance_details"])
        self.manager.update_test_conditions(**current_data["test_conditions"])
        self.manager.update_test_organisms(**current_data["test_organisms"])
        self.manager.update_methodology(**current_data["methodology"])

        self.settings_saved.emit(is_critical_change)
        super().accept()

    def reject(self) -> None:
        """
        Handles the dialog rejection (e.g., Cancel button or Escape key),
        checking for unsaved changes before closing.
        """
        if self._is_dirty():
            reply = QMessageBox.question(self, "Unsaved Changes",
                                         "You have unsaved changes. Are you sure you want to discard them?",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.No:
                return
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        """
        Overrides the default close event (e.g., clicking the 'X' button)
        to check for unsaved changes.
        """
        if self._is_dirty():
            reply = QMessageBox.question(self, "Unsaved Changes",
                                         "You have unsaved changes. Are you sure you want to discard them?",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()