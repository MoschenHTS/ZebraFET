from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QTextBrowser, QWidget,
                               QDialogButtonBox)

from src.core.update_checker import UpdateInfo


class UpdateAvailableDialog(QDialog):
    SKIP = 1
    LATER = 2
    UPDATE = 3

    def __init__(self, current_version: str, info: UpdateInfo, parent: QWidget = None):
        super().__init__(parent)
        self.info = info
        self.choice = self.LATER

        self.setWindowTitle("Update Available")
        self.setMinimumSize(520, 320)
        self.setModal(True)

        self._init_ui(current_version, info)

        if parent:
            self.move(parent.geometry().center() - self.rect().center())

    def _init_ui(self, current_version: str, info: UpdateInfo) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        header = QLabel(f"<h2>ZebraFET {info.version} is available</h2>")
        layout.addWidget(header)

        body = QLabel(
            f"You are running version {current_version}. A newer version has been "
            f"released on GitHub.\n\n"
            f"Clicking <b>Update Now</b> will open the download page in your browser. "
            f"Download and run the new installer — your existing projects and settings "
            f"will be preserved."
        )
        body.setWordWrap(True)
        body.setTextFormat(Qt.RichText)
        layout.addWidget(body)

        if info.release_notes.strip():
            notes_label = QLabel("<b>What's new:</b>")
            layout.addWidget(notes_label)

            notes_view = QTextBrowser()
            notes_view.setMarkdown(info.release_notes)
            notes_view.setMaximumHeight(160)
            notes_view.setOpenExternalLinks(True)
            layout.addWidget(notes_view)

        layout.addStretch()

        # A QDialogButtonBox so the platform decides where each button sits:
        # "Skip" is a destructive-ish choice and belongs on the far side of the
        # affirmative one, which the roles handle without hand-placed stretches.
        button_box = QDialogButtonBox()

        btn_update = button_box.addButton("Update Now", QDialogButtonBox.AcceptRole)
        btn_update.setToolTip("Open the download page in your browser.")
        btn_update.setObjectName("PrimaryButton")
        btn_update.setDefault(True)
        btn_update.clicked.connect(self._on_update)

        btn_later = button_box.addButton("Not Now", QDialogButtonBox.RejectRole)
        btn_later.setToolTip("Remind me on next launch.")
        btn_later.clicked.connect(self._on_later)

        btn_skip = button_box.addButton("Skip This Update", QDialogButtonBox.DestructiveRole)
        btn_skip.setToolTip("Do not notify me about this version again.")
        btn_skip.clicked.connect(self._on_skip)

        layout.addWidget(button_box)

    def _on_skip(self) -> None:
        self.choice = self.SKIP
        self.accept()

    def _on_later(self) -> None:
        self.choice = self.LATER
        self.reject()

    def _on_update(self) -> None:
        self.choice = self.UPDATE
        QDesktopServices.openUrl(QUrl(self.info.release_url))
        self.accept()
