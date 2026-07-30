"""
report_sections_dialog.py — Choose what goes into the generated report.

The full document runs to every appendix and every figure, which suits a final
study record but not the interim reads that happen far more often. The selection
is remembered so an operator who always wants the same shape is not asked to
rebuild it each time.
"""
from typing import Optional, Set

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QLabel,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from src.export.report_generator import ReportGenerator

#: QSettings key holding the sections excluded last time. Storing the exclusions
#: rather than the inclusions means a section added in a later version is on by
#: default instead of silently missing from everyone's saved selection.
_SETTINGS_KEY = "report/excluded_sections"


class ReportSectionsDialog(QDialog):
    """Checkbox list of the optional report sections."""

    def __init__(self, settings: QSettings, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("Report Contents")
        self.setMinimumWidth(420)
        self._boxes = {}
        self._build_ui()
        self._restore()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Choose the sections to include. Test validity and the lethal "
            "endpoints are always included."
        ))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        for key, label in ReportGenerator.OPTIONAL_SECTIONS:
            box = QCheckBox(label)
            box.setChecked(True)
            content_layout.addWidget(box)
            self._boxes[key] = box
        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll)

        toggles = QDialogButtonBox()
        select_all = QPushButton("Select all")
        select_all.clicked.connect(lambda: self._set_all(True))
        select_none = QPushButton("Clear all")
        select_none.clicked.connect(lambda: self._set_all(False))
        toggles.addButton(select_all, QDialogButtonBox.ActionRole)
        toggles.addButton(select_none, QDialogButtonBox.ActionRole)
        layout.addWidget(toggles)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Generate")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _set_all(self, checked: bool) -> None:
        for box in self._boxes.values():
            box.setChecked(checked)

    def _restore(self) -> None:
        excluded = self._settings.value(_SETTINGS_KEY, [], type=list) or []
        for key in excluded:
            if key in self._boxes:
                self._boxes[key].setChecked(False)

    def selected_sections(self) -> Set[str]:
        return {key for key, box in self._boxes.items() if box.isChecked()}

    def accept(self) -> None:
        excluded = [key for key, box in self._boxes.items() if not box.isChecked()]
        self._settings.setValue(_SETTINGS_KEY, excluded)
        super().accept()
