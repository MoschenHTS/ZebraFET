# project_hub_page.py
import datetime
import logging
import os
import shutil
import subprocess
import sys

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QPushButton, QLabel,
                             QHBoxLayout, QMessageBox, QScrollArea, QGridLayout,
                             QMenu, QSizePolicy, QFrame, QFileDialog, QGraphicsBlurEffect)
from PySide6.QtCore import Qt, Signal, QObject, QThread
from PySide6.QtGui import QFont, QPixmap, QDragEnterEvent, QDropEvent, QResizeEvent

from src.core.utils import resource_path, get_registry_db_path, get_projects_base_dir
from src.database.registry import ProjectRegistry

log = logging.getLogger(__name__)


class ClickableCard(QFrame):
    """ Base class for a clickable card frame. """
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setMinimumSize(250, 150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class ActionCard(ClickableCard):
    """ A clickable card for performing an action, like creating or browsing. """
    def __init__(self, text: str, icon_path: str, parent=None):
        super().__init__(parent)
        self._init_ui(text, icon_path)

    def _init_ui(self, text: str, icon_path: str):
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(10)

        icon_label = QLabel()
        full_icon_path = resource_path(icon_path)
        if os.path.exists(full_icon_path):
            pixmap = QPixmap(full_icon_path)
            icon_label.setPixmap(pixmap.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            log.warning(f"Icon not found at path: {full_icon_path}")
        icon_label.setAlignment(Qt.AlignCenter)

        text_label = QLabel(text)
        font = self.font(); font.setPointSize(14); font.setBold(True)
        text_label.setFont(font)
        text_label.setAlignment(Qt.AlignCenter)

        self.setStyleSheet("QLabel { background-color: transparent; }")
        layout.addWidget(icon_label)
        layout.addWidget(text_label)


class CreateProjectCard(ActionCard):
    def __init__(self, parent=None):
        super().__init__("Create New Project", "resources/icons/plus.svg", parent)


class BrowseProjectCard(ActionCard):
    def __init__(self, parent=None):
        super().__init__("Browse for Project", "resources/icons/folder-down.svg", parent)


class ImportProjectCard(ActionCard):
    def __init__(self, parent=None):
        super().__init__("Import Project", "resources/icons/save.svg", parent)


class ProjectCard(QFrame):
    """
    An interactive card showing a single project's metadata from the registry.
    Right-click for Open Folder / Export / Delete options.
    """
    clicked = Signal(str)               # project directory path
    delete_requested = Signal(str)      # project directory path
    open_folder_requested = Signal(str) # project directory path
    export_requested = Signal(str)      # project directory path

    def __init__(self, registry_row: dict, parent=None):
        super().__init__(parent)
        self.project_dir_path = os.path.dirname(registry_row["db_path"])
        self.project_name = registry_row["project_name"]
        self.setObjectName("Card")
        self._init_ui(registry_row)

    def _init_ui(self, data: dict):
        self.setMinimumSize(250, 150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(5)

        name_label = QLabel(data["project_name"])
        font_name = self.font(); font_name.setPointSize(16); font_name.setBold(True)
        name_label.setFont(font_name)

        font_info = self.font(); font_info.setPointSize(10)
        researcher_label = QLabel(f"Researcher: {data.get('main_researcher') or 'N/A'}")
        researcher_label.setFont(font_info)
        substance_label = QLabel(f"Substance: {data.get('substance') or 'N/A'}")
        substance_label.setFont(font_info)

        num_days = data.get("num_days", 0)
        completed = data.get("completed_days_count", 0)
        progress_label = QLabel(f"Progress: Day {completed}/{num_days}")
        progress_label.setFont(font_info)

        # Format the ISO last_modified timestamp for display
        last_mod_raw = data.get("last_modified", "")
        try:
            dt = datetime.datetime.fromisoformat(last_mod_raw)
            last_mod_str = dt.strftime("%d/%m/%Y")
        except (ValueError, TypeError):
            last_mod_str = "Unknown"

        date_label = QLabel(f"Last updated: {last_mod_str}")
        font_date = self.font(); font_date.setPointSize(9); font_date.setItalic(True)
        date_label.setFont(font_date)
        date_label.setAlignment(Qt.AlignRight)

        for label in [name_label, researcher_label, substance_label, progress_label, date_label]:
            label.setStyleSheet("background-color: transparent;")

        layout.addWidget(name_label)
        layout.addWidget(researcher_label)
        layout.addWidget(substance_label)
        layout.addWidget(progress_label)
        layout.addStretch()
        layout.addWidget(date_label)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.project_dir_path)
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        open_action = menu.addAction("Open Project Folder")
        export_action = menu.addAction("Export Project...")
        menu.addSeparator()
        delete_action = menu.addAction("Delete Project")
        action = menu.exec(self.mapToGlobal(event.pos()))
        if action == open_action:
            self.open_folder_requested.emit(self.project_dir_path)
        elif action == export_action:
            self.export_requested.emit(self.project_dir_path)
        elif action == delete_action:
            self.delete_requested.emit(self.project_dir_path)


class ProjectHubPage(QWidget):
    """ A page for creating, selecting, and managing projects using a grid of cards. """
    project_path_selected = Signal(str)
    new_project_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._registry = ProjectRegistry(get_registry_db_path())
        self._init_ui()
        self.populate_project_grid()

    def closeEvent(self, event):
        self._registry.close()
        super().closeEvent(event)

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(15)

        title = QLabel("Projects Hub")
        font_title = self.font(); font_title.setPointSize(20); font_title.setBold(True)
        title.setFont(font_title)
        title.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title)

        container_widget = QWidget()
        container_layout = QVBoxLayout(container_widget)
        container_layout.setContentsMargins(0, 0, 0, 0)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.grid_container = QWidget()
        self.project_grid_layout = QGridLayout(self.grid_container)
        self.project_grid_layout.setSpacing(20)
        self.project_grid_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.scroll_area.setWidget(self.grid_container)

        container_layout.addWidget(self.scroll_area)
        main_layout.addWidget(container_widget)

        # Drag-and-drop overlay
        self.drop_overlay = QLabel(self)
        self.drop_overlay.setObjectName("DropOverlay")
        overlay_layout = QVBoxLayout(self.drop_overlay)
        overlay_layout.setAlignment(Qt.AlignCenter)
        overlay_layout.setSpacing(20)

        icon_label = QLabel()
        icon_path = resource_path("resources/icons/folder-down.svg")
        if os.path.exists(icon_path):
            pixmap = QPixmap(icon_path)
            icon_label.setPixmap(pixmap.scaled(96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            log.warning(f"Overlay icon not found at path: {icon_path}")

        text_label = QLabel("Drop a project folder or .zfet archive here")
        font_overlay = self.font(); font_overlay.setPointSize(20); font_overlay.setBold(True)
        text_label.setFont(font_overlay)
        overlay_layout.addWidget(icon_label)
        overlay_layout.addWidget(text_label)
        self.drop_overlay.hide()

        self.blur_effect = QGraphicsBlurEffect()
        self.blur_effect.setBlurRadius(15)
        self.blur_effect.setEnabled(False)
        self.grid_container.setGraphicsEffect(self.blur_effect)
        self._last_col_count = 0

    def resizeEvent(self, event: QResizeEvent):
        super().resizeEvent(event)
        self.drop_overlay.resize(self.size())
        available_w = self.width() - 30
        new_cols = max(2, available_w // 290) if available_w > 0 else 3
        if new_cols != self._last_col_count:
            self._last_col_count = new_cols
            self.populate_project_grid()

    # ------------------------------------------------------------------
    # Drag-and-drop
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent):
        mime = event.mimeData()
        if mime.hasUrls() and len(mime.urls()) == 1:
            path = mime.urls()[0].toLocalFile()
            if os.path.isdir(path) or path.endswith((".zfet", ".zebravet")):
                event.acceptProposedAction()
                self.blur_effect.setEnabled(True)
                self.drop_overlay.show()
                self.drop_overlay.raise_()

    def dragLeaveEvent(self, event):
        self.blur_effect.setEnabled(False)
        self.drop_overlay.hide()

    def dropEvent(self, event: QDropEvent):
        self.blur_effect.setEnabled(False)
        self.drop_overlay.hide()
        path = event.mimeData().urls()[0].toLocalFile()
        if path.endswith((".zfet", ".zebravet")):
            self._import_project(path)
        else:
            self._open_dir_path(path)

    # ------------------------------------------------------------------
    # Grid population
    # ------------------------------------------------------------------

    def populate_project_grid(self):
        while self.project_grid_layout.count():
            child = self.project_grid_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        available_w = self.width() - 30
        columns = max(2, available_w // 290) if available_w > 0 else 3
        create_card = CreateProjectCard()
        create_card.clicked.connect(self.new_project_requested.emit)
        browse_card = BrowseProjectCard()
        browse_card.clicked.connect(self._browse_for_project)
        import_card = ImportProjectCard()
        import_card.clicked.connect(lambda: self._import_project())

        self.project_grid_layout.addWidget(create_card, 0, 0)
        self.project_grid_layout.addWidget(browse_card, 0, 1)
        self.project_grid_layout.addWidget(import_card, 0, 2)

        row, col = 0, 3
        for registry_row in self._registry.list_projects():
            project_dir = os.path.dirname(registry_row["db_path"])
            if not os.path.isdir(project_dir):
                log.warning(f"Project directory missing: {project_dir}. Skipping.")
                continue

            if col >= columns:
                col = 0; row += 1

            card = ProjectCard(registry_row)
            card.clicked.connect(self.project_path_selected.emit)
            card.open_folder_requested.connect(self._open_project_folder)
            card.export_requested.connect(self._export_project)
            card.delete_requested.connect(self._delete_project)
            self.project_grid_layout.addWidget(card, row, col)
            col += 1

    def set_ui_enabled(self, enabled: bool):
        self.scroll_area.setEnabled(enabled)
        self.blur_effect.setEnabled(not enabled)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _open_dir_path(self, path: str):
        """Try to open a dropped/browsed directory as a project."""
        project_name = os.path.basename(path)
        db_path = os.path.join(path, f"{project_name}.db")
        if not os.path.exists(db_path):
            QMessageBox.warning(
                self, "Invalid Project",
                f"The selected directory does not appear to be a valid ZebraFET project\n"
                f"(missing '{project_name}.db')."
            )
            return
        self.project_path_selected.emit(path)

    def _browse_for_project(self):
        try:
            default_dir = get_projects_base_dir()
        except PermissionError:
            default_dir = os.path.expanduser("~")

        db_path, _ = QFileDialog.getOpenFileName(
            self, "Open Project Database", default_dir, "ZebraFET Project (*.db)"
        )
        if db_path and os.path.exists(db_path):
            self.project_path_selected.emit(os.path.dirname(db_path))

    def _import_project(self, archive_path: str = ""):
        """Open a .zfet (or legacy .zebravet) archive and import it into the projects directory."""
        from src.core import project_exporter  # local import — avoids circular at module level

        if not archive_path:
            archive_path, _ = QFileDialog.getOpenFileName(
                self, "Import Project Archive",
                os.path.expanduser("~"),
                "ZebraFET Archives (*.zfet *.zebravet)"
            )
        if not archive_path:
            return

        try:
            project_dir = project_exporter.import_project(archive_path)
            self.populate_project_grid()
            self.project_path_selected.emit(project_dir)
        except FileExistsError as e:
            QMessageBox.warning(self, "Import Failed — Project Already Exists", str(e))
        except Exception as e:
            log.error(f"Import failed: {e}", exc_info=True)
            QMessageBox.critical(self, "Import Failed", f"Could not import project:\n{e}")

    def _export_project(self, project_dir_path: str):
        """Export a project from the hub (right-click → Export)."""
        from src.core import project_exporter

        project_name = os.path.basename(project_dir_path)
        default_name = os.path.join(os.path.expanduser("~"), f"{project_name}.zfet")
        output_path, _ = QFileDialog.getSaveFileName(
            self, "Export Project", default_name, "ZebraFET Archives (*.zfet)"
        )
        if not output_path:
            return

        try:
            from src.core.project_manager import ProjectManager
            manager = ProjectManager(project_dir_path)
            project_exporter.export_project(manager, output_path)
            manager.close()
            QMessageBox.information(
                self, "Export Successful",
                f"Project '{project_name}' exported to:\n{output_path}"
            )
        except Exception as e:
            log.error(f"Export failed for '{project_name}': {e}", exc_info=True)
            QMessageBox.critical(self, "Export Failed", f"Could not export project:\n{e}")

    def _open_project_folder(self, path: str):
        if not os.path.isdir(path):
            QMessageBox.warning(self, "Directory Not Found",
                                f"The directory could not be found:\n{path}")
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.run(["open", path], check=True)
            else:
                subprocess.run(["xdg-open", path], check=True)
        except (OSError, subprocess.CalledProcessError) as e:
            log.error(f"Failed to open folder '{path}': {e}")
            QMessageBox.critical(self, "Error", f"Could not open the project folder:\n{e}")

    def _delete_project(self, project_dir_path: str):
        project_name = os.path.basename(project_dir_path)
        reply = QMessageBox.question(
            self, "Confirm Deletion",
            f"Are you sure you want to permanently delete the project\n"
            f"'{project_name}' and all its data?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        try:
            self._registry.remove_project(project_name)
            shutil.rmtree(project_dir_path)
            log.info(f"Deleted project '{project_name}' at: {project_dir_path}")
            self.populate_project_grid()
        except OSError as e:
            log.error(f"Failed to delete project '{project_name}': {e}")
            QMessageBox.critical(self, "Error", f"Failed to delete project:\n{e}")
