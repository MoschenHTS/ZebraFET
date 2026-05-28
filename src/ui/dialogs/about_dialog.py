import os
import glob
import logging
import markdown
from typing import Optional

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QTabWidget, QWidget, QLabel,
                             QTextEdit, QScrollArea, QGroupBox, QPushButton,
                             QHBoxLayout, QMessageBox)
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QFont, QDesktopServices

from src.core.utils import resource_path

log = logging.getLogger(__name__)

class ClickableCollapsibleBox(QGroupBox):
    """
    A QGroupBox that acts as a collapsible box, toggled by clicking its title.
    """
    def __init__(self, title: str = "", parent: Optional[QWidget] = None):
        # Initialize the QGroupBox without a title initially to manage it manually.
        super().__init__("", parent)
        self.setCheckable(True)
        self.setChecked(False)
        self.title_text = title

        # Set the initial title with the correct collapsed indicator.
        super().setTitle(f"► {self.title_text}")

        # The content widget will be hidden/shown
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)
        
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.content_widget)

        self.content_widget.setVisible(False)
        self.toggled.connect(self.toggle_content)

    def setContentLayout(self, layout_or_widget) -> None:
        # Clear existing layout
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        if isinstance(layout_or_widget, QWidget):
            self.content_layout.addWidget(layout_or_widget)
        elif isinstance(layout_or_widget, QVBoxLayout):
            while layout_or_widget.count():
                item = layout_or_widget.takeAt(0)
                if item.widget():
                    self.content_layout.addWidget(item.widget())
                elif item.layout():
                     self.content_layout.addLayout(item.layout())

    def toggle_content(self, checked: bool) -> None:
        """Shows or hides the content and updates the title arrow."""
        self.content_widget.setVisible(checked)
        arrow_char = "▼" if checked else "►"
        super().setTitle(f"{arrow_char} {self.title_text}")



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
        self.tab_widget.addTab(self._create_researcher_info_tab(), "Researcher Info")

        layout.addWidget(self.tab_widget)
        self._on_tab_changed(0)

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
        layout.setContentsMargins(20, 20, 20, 20); layout.setSpacing(15)
        title_font = QFont(); title_font.setPointSize(18); title_font.setBold(True)
        author_font = QFont(); author_font.setPointSize(12); author_font.setItalic(True)
        body_font = QFont(); body_font.setPointSize(11)
        title = QLabel("ZebraFET 2.0 — A Software for Standardization and Execution of the Fish Embryo Acute Toxicity Test (OECD TG 236)")
        title.setFont(title_font); title.setWordWrap(True)
        author = QLabel("Author: Henrique Tamanini S. Moschen"); author.setFont(author_font); author.setWordWrap(True)
        body_text = """
        <p>The Fish Embryo Acute Toxicity (FET) test, standardized under OECD TG 236, is a cornerstone method in environmental and regulatory toxicology. However, its predominantly manual execution introduces variability, transcription errors, and inefficiencies that compromise reproducibility and data traceability.</p>
        <p><b>ZebraFET</b> was developed in Python (PySide6) to address these limitations by structuring the FET workflow into integrated modules:</p>
        <ul>
            <li><b>Experimental planning:</b> plate layout design and dilution series calculation.</li>
            <li><b>Guided execution:</b> timed monitoring and systematic recording of lethality endpoints.</li>
            <li><b>Automated analysis:</b> mortality curves, dose-response visualization, and exportable results.</li>
        </ul>
        <p>The software aims to <b>enhance reproducibility, traceability, and efficiency</b>, providing a robust digital protocol for academic, industrial, and regulatory laboratories.</p>
        """
        body = QLabel(body_text)
        body.setFont(body_font); body.setWordWrap(True); body.setTextFormat(Qt.RichText); body.setOpenExternalLinks(True)
        layout.addWidget(title); layout.addWidget(author); layout.addWidget(body); layout.addStretch()
        return widget

    def _create_researcher_info_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(20, 20, 20, 20); layout.setSpacing(10)
        main_group = QGroupBox("Researcher Identity"); group_layout = QVBoxLayout(main_group)
        def create_info_label(title: str, value: str, link: Optional[str] = None) -> QLabel:
            text = f'<b>{title}:</b> <a href="{link}">{value}</a>' if link else f'<b>{title}:</b> {value}'
            label = QLabel(text); label.setTextFormat(Qt.RichText); label.setOpenExternalLinks(True)
            return label
        group_layout.addWidget(create_info_label("Full Name", "Henrique Tamanini S. Moschen"))
        group_layout.addWidget(create_info_label("E-mail", "henriquetamanini@icloud.com", "mailto:henriquetamanini@icloud.com"))
        group_layout.addWidget(create_info_label("ORCID", "0000-0002-1920-8915", "https://orcid.org/0000-0002-1920-8915"))
        group_layout.addWidget(create_info_label("Lattes", "0806842036446591", "http://lattes.cnpq.br/0806842036446591"))
        layout.addWidget(main_group); layout.addStretch()
        return widget

    def _create_licenses_tab(self) -> QWidget:
        scroll_widget = QScrollArea(); scroll_widget.setWidgetResizable(True); scroll_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        container = QWidget(); layout = QVBoxLayout(container); layout.setSpacing(10)
        
        eula_path = resource_path("resources/docs/Licenses/EULA_zebraFET.md")
        eula_content_md = self._read_file_content(eula_path, "ZebraFET End User License Agreement not found.")
        eula_box = ClickableCollapsibleBox("ZebraFET License")
        eula_text = QTextEdit()
        eula_text.setReadOnly(True)
        if not eula_content_md.startswith("<b>Error:"):
            eula_html = markdown.markdown(eula_content_md)
            eula_text.setHtml(eula_html)
        else:
            eula_text.setText(eula_content_md)
        eula_box.setContentLayout(eula_text)
        layout.addWidget(eula_box)

        third_party_box = ClickableCollapsibleBox("Third-party Licenses")
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
                lib_box = ClickableCollapsibleBox(license_name)
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