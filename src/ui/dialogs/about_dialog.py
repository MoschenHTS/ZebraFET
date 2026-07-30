import os
import glob
import logging
import markdown
from typing import Optional

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QTabWidget, QWidget, QLabel,
                             QTextEdit, QScrollArea, QPushButton,
                             QHBoxLayout, QMessageBox, QDialogButtonBox)
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QFont, QDesktopServices

from src.core.constants import APP_VERSION
from src.core.utils import resource_path
from src.ui.components import CollapsibleSection
from src.ui.typography import scaled_pt

log = logging.getLogger(__name__)

#: Published identifiers shown in the About tab.
ZENODO_DOI = "10.5281/zenodo.20183712"
REPOSITORY_URL = "https://github.com/MoschenHTS/ZebraFET"

class AboutDialog(QDialog):
    """
    A dialog window providing information about the application.
    """
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("About ZebraFET")
        self.setMinimumSize(640, 520)
        self.setObjectName("AboutDialog")

        self.user_guide_html: Optional[str] = None
        self.oecd_guide_html: Optional[str] = None
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.tab_widget = QTabWidget()
        self.tab_widget.currentChanged.connect(self._on_tab_changed)

        self.tab_widget.addTab(self._create_about_tab(), "About")
        self.tab_widget.addTab(QWidget(), "User Guide")
        self.tab_widget.addTab(QWidget(), "OECD TG 236")
        self.tab_widget.addTab(self._create_licenses_tab(), "Licenses")
        self.tab_widget.addTab(self._create_credits_tab(), "Credits")

        layout.addWidget(self.tab_widget)

        # Without a button bar this dialog could only be dismissed by the window
        # close box or an undiscoverable Escape.
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self._on_tab_changed(0)

    def show_tab(self, tab_name: str) -> None:
        """Select a tab by its label; unknown names leave the selection alone."""
        for index in range(self.tab_widget.count()):
            if self.tab_widget.tabText(index) == tab_name:
                self.tab_widget.setCurrentIndex(index)
                return
        log.warning(f"About dialog has no tab named {tab_name!r}")

    def _on_tab_changed(self, index: int) -> None:
        tab_content_widget = self.tab_widget.widget(index)
        if tab_content_widget is None:
            return
        if tab_content_widget.layout() is None:
            tab_text = self.tab_widget.tabText(index)
            
            content_map = {
                "User Guide": ("user_guide_html", "resources/docs/User_Manual.md", "resources/docs/User_Manual.pdf"),
                "OECD TG 236": ("oecd_guide_html", "resources/docs/OECD_TG_236.md", "resources/docs/OECD_TG_236.pdf")
            }

            if tab_text in content_map:
                cache_attr, md_path, pdf_path = content_map[tab_text]
                self._create_markdown_viewer_tab(tab_content_widget, md_path, pdf_path, cache_attr)

    def _create_markdown_viewer_tab(self, tab_widget: QWidget, md_path: str, pdf_path: str, cache_attr: str) -> None:
        layout = QVBoxLayout(tab_widget)
        layout.setContentsMargins(5, 5, 5, 5)

        button_bar = QHBoxLayout()
        button_bar.addStretch()

        pdf_full_path = resource_path(pdf_path)
        if os.path.exists(pdf_full_path):
            pdf_button = QPushButton("View as PDF")
            pdf_button.clicked.connect(lambda: self._open_pdf(pdf_full_path))
            button_bar.addWidget(pdf_button)
        
        layout.addLayout(button_bar)

        text_edit = QTextEdit()
        text_edit.setReadOnly(True)

        resources_path_abs = resource_path("resources")
        text_edit.document().setBaseUrl(QUrl.fromLocalFile(resources_path_abs + os.path.sep))
        
        cached_html = getattr(self, cache_attr)
        md_full_path = resource_path(md_path)

        if cached_html is None:
            content_md = self._read_file_content(md_full_path, f"File not found: {os.path.basename(md_path)}")
            if content_md.startswith("<b>Error:"):
                cached_html = content_md
            else:
                cached_html = markdown.markdown(content_md, extensions=['tables', 'fenced_code'])
            setattr(self, cache_attr, cached_html)

        text_edit.setHtml(cached_html)
        layout.addWidget(text_edit)

    def _open_pdf(self, file_path: str):
        if not os.path.exists(file_path):
            QMessageBox.warning(self, "File Not Found", f"The PDF file could not be found at the expected location:\n{file_path}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(file_path))

    def _create_about_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(20, 20, 20, 20); layout.setSpacing(12)

        title_font = QFont(); title_font.setPointSizeF(scaled_pt(18)); title_font.setBold(True)
        subtitle_font = QFont(); subtitle_font.setPointSizeF(scaled_pt(11)); subtitle_font.setItalic(True)

        title = QLabel(f"ZebraFET {APP_VERSION}")
        title.setFont(title_font)

        subtitle = QLabel(
            "Fish Embryo Acute Toxicity (FET) test assistant implementing OECD TG 236."
        )
        subtitle.setFont(subtitle_font); subtitle.setWordWrap(True)

        body = QLabel(
            "<p>ZebraFET covers the FET workflow in one application: concentration planning "
            "and plate layout, daily scoring of the OECD TG 236 lethal and sublethal "
            "endpoints, LC50 estimation with NOEC and LOEC determination and the sublethal "
            "endpoint battery, and export to a Word report or CSV tables.</p>"
            "<p>The statistics module carries no interface dependencies and can be imported "
            "directly from scripts and notebooks.</p>"
            "<table cellpadding='3'>"
            "<tr><td><b>Guideline</b></td><td>OECD TG 236 (2025 edition)</td></tr>"
            "<tr><td><b>License</b></td><td>GNU General Public License v3.0</td></tr>"
            f"<tr><td><b>DOI</b></td><td><a href='https://doi.org/{ZENODO_DOI}'>{ZENODO_DOI}</a></td></tr>"
            f"<tr><td><b>Source</b></td><td><a href='{REPOSITORY_URL}'>{REPOSITORY_URL}</a></td></tr>"
            "</table>"
            "<p>Authors, collaborators and acknowledgements are listed under Credits.</p>"
        )
        body.setWordWrap(True); body.setTextFormat(Qt.RichText); body.setOpenExternalLinks(True)

        layout.addWidget(title); layout.addWidget(subtitle); layout.addWidget(body)
        layout.addStretch()
        return widget

    def _create_licenses_tab(self) -> QWidget:
        scroll_widget = QScrollArea(); scroll_widget.setWidgetResizable(True); scroll_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        container = QWidget(); layout = QVBoxLayout(container); layout.setSpacing(10)
        
        eula_path = resource_path("resources/docs/Licenses/EULA_zebraFET.md")
        eula_content_md = self._read_file_content(eula_path, "ZebraFET End User License Agreement not found.")
        eula_box = CollapsibleSection("ZebraFET License")
        eula_text = QTextEdit()
        eula_text.setReadOnly(True)
        if not eula_content_md.startswith("<b>Error:"):
            eula_html = markdown.markdown(eula_content_md)
            eula_text.setHtml(eula_html)
        else:
            eula_text.setText(eula_content_md)
        eula_box.setContentLayout(eula_text)
        layout.addWidget(eula_box)

        third_party_box = CollapsibleSection("Third-party Licenses")
        third_party_layout = QVBoxLayout()
        licenses_path = resource_path("resources/docs/Licenses/")
        if not os.path.isdir(licenses_path):
            log.error(f"Licenses directory not found at: {licenses_path}")
            third_party_layout.addWidget(QLabel("Licenses directory not found."))
        else:
            license_files = glob.glob(os.path.join(licenses_path, "*.txt"))
            if not license_files:
                third_party_layout.addWidget(QLabel("No third-party licenses found."))
            
            for file_path in sorted(license_files):
                license_name = os.path.basename(file_path).replace(".txt", "").replace("_", " ")
                content = self._read_file_content(file_path, f"Could not read {license_name} license.")
                lib_box = CollapsibleSection(license_name)
                text_edit = QTextEdit(content)
                text_edit.setReadOnly(True)
                lib_box.setContentLayout(text_edit)
                third_party_layout.addWidget(lib_box)

        third_party_box.setContentLayout(third_party_layout)
        layout.addWidget(third_party_box)
        layout.addStretch()
        scroll_widget.setWidget(container)
        return scroll_widget

    def _create_credits_tab(self) -> QWidget:
        widget = QWidget(); layout = QVBoxLayout(widget)
        credits_path = resource_path("resources/docs/credits.md")
        content = self._read_file_content(credits_path, "Credits file not found.")
        text_edit = QTextEdit()
        if not content.startswith("<b>Error:"):
            text_edit.setMarkdown(content)
        else:
            text_edit.setText(content)
        text_edit.setReadOnly(True)
        layout.addWidget(text_edit)
        return widget

    def _read_file_content(self, file_path: str, error_message: str) -> str:
        try:
            with open(file_path, 'r', encoding='utf-8') as f: return f.read()
        except FileNotFoundError:
            log.warning(f"File not found: {file_path}"); return f"<b>Error:</b> {error_message}"
        except Exception as e:
            log.error(f"Error reading file {file_path}: {e}"); return f"<b>Error:</b> Could not read file."