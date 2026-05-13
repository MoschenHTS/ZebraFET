"""
setup_wizard.py — First-run setup wizard for ZebraFET.

Shown automatically before the main window when the app is launched for the
first time on a new machine (detected via QSettings "setup/completed" flag).
"""

import os
import sys

from PySide6.QtWidgets import (
    QApplication, QWizard, QWizardPage, QVBoxLayout, QHBoxLayout,
    QLabel, QCheckBox, QLineEdit, QPushButton, QTextBrowser,
    QFileDialog, QSizePolicy,
)
from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QPixmap, QFont

from src.core.utils import resource_path
from src.core.constants import APP_VERSION


# ---------------------------------------------------------------------------
# Page 1 — Welcome
# ---------------------------------------------------------------------------

class WelcomePage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Welcome to ZebraFET")
        self.setSubTitle(
            "Standardized Zebrafish Embryo Toxicity Test Assistant (OECD TG 236)"
        )
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(20, 20, 20, 20)

        # Icon
        icon_label = QLabel()
        pixmap = QPixmap(resource_path("resources/icons/fishapp_icon.png"))
        if not pixmap.isNull():
            icon_label.setPixmap(
                pixmap.scaled(96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        icon_label.setAlignment(Qt.AlignHCenter)
        layout.addWidget(icon_label)

        # Description
        desc = QLabel(
            "ZebraFET helps you record, manage, and analyse Fish Embryo Acute "
            "Toxicity (FET) tests following the OECD Test Guideline 236 protocol.<br><br>"
            "This wizard will guide you through a quick one-time setup:<br>"
            "&nbsp;&nbsp;\u2022&nbsp; Review and accept the software license<br>"
            "&nbsp;&nbsp;\u2022&nbsp; Choose where your project data will be stored<br><br>"
            "Click <b>Next</b> to continue."
        )
        desc.setTextFormat(Qt.TextFormat.RichText)
        desc.setWordWrap(True)
        desc.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        layout.addWidget(desc)
        layout.addStretch()


# ---------------------------------------------------------------------------
# Page 2 — License Agreement
# ---------------------------------------------------------------------------

class LicensePage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("License Agreement")
        self.setSubTitle(
            "Please read the license agreement carefully before using ZebraFET."
        )
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(20, 10, 20, 10)

        # License text
        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(True)
        self._browser.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._load_license()
        layout.addWidget(self._browser, 1)

        # Acceptance checkbox — blocks Next until checked
        self._accept_cb = QCheckBox("I have read and accept the terms of this license.")
        self._accept_cb.toggled.connect(self.completeChanged)
        layout.addWidget(self._accept_cb)

        # Register as a required field (the * suffix marks it mandatory)
        self.registerField("license_accepted*", self._accept_cb)

    def _load_license(self):
        eula_path = resource_path("resources/docs/Licenses/EULA_zebraFET.md")
        try:
            with open(eula_path, encoding="utf-8") as fh:
                text = fh.read()
            self._browser.setMarkdown(text)
        except OSError:
            self._browser.setPlainText(
                "License file not found. "
                "ZebraFET is distributed under the GNU General Public License v3.0.\n"
                "See https://www.gnu.org/licenses/gpl-3.0.txt for the full text."
            )

    def isComplete(self):
        return self._accept_cb.isChecked()


# ---------------------------------------------------------------------------
# Page 3 — Data Directory
# ---------------------------------------------------------------------------

class DataDirectoryPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Data Directory")
        self.setSubTitle(
            "Choose where ZebraFET will store your projects and data."
        )
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        info = QLabel(
            "ZebraFET will create a <b>projects</b> folder inside the directory "
            "you choose below. You can change this location at any time by "
            "re-running the setup wizard from the application settings."
        )
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setWordWrap(True)
        layout.addWidget(info)

        # Directory picker row
        row = QHBoxLayout()
        self._dir_edit = QLineEdit()
        self._dir_edit.setReadOnly(True)
        self._dir_edit.setPlaceholderText("Select a directory…")
        self._dir_edit.setText(self._default_dir())
        row.addWidget(self._dir_edit, 1)

        browse_btn = QPushButton("Browse\u2026")
        browse_btn.setFixedWidth(90)
        browse_btn.clicked.connect(self._browse)
        row.addWidget(browse_btn)
        layout.addLayout(row)

        # Status label
        self._status_label = QLabel()
        self._status_label.setTextFormat(Qt.TextFormat.RichText)
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)
        layout.addStretch()

        # Register field so wizard.field("data_dir") works
        self.registerField("data_dir", self._dir_edit)

    @staticmethod
    def _default_dir() -> str:
        app_name = "ZebraFET"
        if sys.platform == "darwin":
            return os.path.join(
                os.path.expanduser("~"), "Library", "Application Support", app_name
            )
        return os.path.join(os.path.expanduser("~"), "Documents", app_name)

    def _browse(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "Select Data Directory", self._dir_edit.text()
        )
        if chosen:
            self._dir_edit.setText(chosen)
            self._validate()

    def _validate(self):
        path = self._dir_edit.text()
        parent = os.path.dirname(path) or path
        if os.path.isdir(parent):
            self._status_label.setText(
                f"\u2713 Projects will be stored in: <i>{path}/projects</i>"
            )
            self._status_label.setStyleSheet("color: green;")
        else:
            self._status_label.setText(
                "\u26a0 The parent directory does not exist. Please choose a valid path."
            )
            self._status_label.setStyleSheet("color: red;")

    def initializePage(self):
        self._validate()


# ---------------------------------------------------------------------------
# Page 4 — Finish
# ---------------------------------------------------------------------------

class FinishPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Setup Complete")
        self.setSubTitle("ZebraFET is ready to use.")
        self.setFinalPage(True)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        self._summary = QLabel()
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)

        note = QLabel(
            "Click <b>Finish</b> to save your settings and launch ZebraFET."
        )
        note.setTextFormat(Qt.TextFormat.RichText)
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()

    def initializePage(self):
        data_dir = self.field("data_dir") or DataDirectoryPage._default_dir()
        self._summary.setText(
            f"<b>Data directory:</b> {data_dir}/projects<br><br>"
            "Your settings have been saved. You can change them later from "
            "the application's settings menu."
        )


# ---------------------------------------------------------------------------
# Main wizard
# ---------------------------------------------------------------------------

class SetupWizard(QWizard):
    """
    First-run installation wizard.  Pass the existing QSettings instance so
    the wizard can write its results directly.
    """

    def __init__(self, settings: QSettings, parent=None):
        super().__init__(parent)
        self._settings = settings

        self.setWindowTitle(f"ZebraFET {APP_VERSION} — Setup")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setMinimumSize(560, 420)
        self.resize(620, 460)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)

        # Set app icon on wizard window
        from PySide6.QtGui import QIcon
        self.setWindowIcon(QIcon(resource_path("resources/icons/fishapp_icon.png")))

        self.addPage(WelcomePage())
        self.addPage(LicensePage())
        self.addPage(DataDirectoryPage())
        self.addPage(FinishPage())

    def accept(self):
        """Persist wizard results to QSettings before closing."""
        self._settings.setValue("setup/completed", True)
        self._settings.setValue(
            "setup/data_dir",
            self.field("data_dir") or DataDirectoryPage._default_dir(),
        )
        self._settings.sync()
        super().accept()

    def reject(self):
        """User closed/cancelled the wizard — exit the application."""
        super().reject()
