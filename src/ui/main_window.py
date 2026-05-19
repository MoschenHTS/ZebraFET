# main_window.py
import os
import re
import sys
import logging
import shutil
from typing import Optional

from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QPushButton, QFrame, QStackedWidget, QButtonGroup,
                             QLabel, QMessageBox, QFileDialog, QGraphicsOpacityEffect,
                             QApplication)
from PySide6.QtCore import (QSize, QPropertyAnimation, QEasingCurve, Qt, QEvent,
                              QSettings, QTimer, QByteArray, QParallelAnimationGroup,
                              QObject, Signal, QThread)
from PySide6.QtGui import QIcon, QFont, QCloseEvent, QAction, QKeySequence

from src.core.utils import resource_path, get_projects_base_dir
from src.ui.pages.project_hub_page import ProjectHubPage
from src.ui.widgets.experiment_view_widget import ExperimentViewWidget
from src.ui.pages.plate_layout_page import PlateLayoutPage
from src.ui.widgets.photo_assistant_widget import PhotoAssistantWidget
from src.ui.widgets.results_analysis_widget import ResultsAnalysisWidget
from src.ui.pages.concentration_planner_page import ConcentrationPlannerPage
from src.ui.pages.project_creation_page import ProjectCreationPage
from src.core.project_manager import ProjectManager
from src.ui.dialogs.project_settings_dialog import ProjectSettingsDialog
from src.ui.dialogs.about_dialog import AboutDialog
from src.ui.theme_manager import ThemeManager

log = logging.getLogger(__name__)

#Worker for loading projects in a background thread
class ProjectLoadWorker(QObject):
    """
    A worker object that loads a ProjectManager in a separate thread to avoid
    blocking the UI. Emits the manager instance on success or an error message.
    """
    finished = Signal(object)
    error = Signal(str)

    def __init__(self, path: str):
        super().__init__()
        self.path = path

    def run(self):
        """Loads the project file and initializes the ProjectManager."""
        try:
            log.info(f"Worker thread starting to load project: {self.path}")
            manager = ProjectManager(self.path)
            if not manager.get_project_name():
                raise ValueError("Project data is empty or appears corrupted.")
            self.finished.emit(manager)
        except Exception as e:
            log.error(f"Failed to load project in worker thread: {e}", exc_info=True)
            self.error.emit(f"Could not load project files from:\n{self.path}\n\nError: {e}")

class MainWindow(QMainWindow):
    """
    The main application window, orchestrating the user interface.
    """
    def __init__(self, settings: QSettings, theme_manager: ThemeManager, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.theme_manager = theme_manager

        self.setWindowTitle("ZebraFET")
        self.project_manager: Optional[ProjectManager] = None
        self.active_connections = {}
        self.temp_pages = []
        self.about_dialog: Optional[AboutDialog] = None
        self.page_transition_animation: Optional[QParallelAnimationGroup] = None
        self.load_thread: Optional[QThread] = None
        self.load_worker: Optional[ProjectLoadWorker] = None

        self._init_ui()
        self._load_initial_page()
        self._restore_window_state()
        self._setup_shortcuts()

    def _save_window_state(self):
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("maximized", self.isMaximized())
        log.info("Window state saved.")

    def _restore_window_state(self):
        geometry = self.settings.value("geometry")
        if isinstance(geometry, QByteArray):
            self.restoreGeometry(geometry)

        is_maximized_str = self.settings.value("maximized", "false", type=str)
        if is_maximized_str.lower() == 'true':
            if sys.platform == "win32":
                self.showFullScreen()
            else:
                self.showMaximized()

        log.info("Window state restored.")

    def changeEvent(self, event):
        if sys.platform == "win32" and event.type() == QEvent.Type.WindowStateChange:
            if self.windowState() & Qt.WindowState.WindowMaximized:
                QTimer.singleShot(0, self.showFullScreen)
        super().changeEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.isFullScreen():
            self.showNormal()
        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent):
        """Stop any active load thread and save window geometry before closing."""
        if self.load_thread is not None and self.load_thread.isRunning():
            self.load_thread.quit()
            self.load_thread.wait(3000)
        self._save_window_state()
        event.accept()

    def _init_ui(self):
        self.setMinimumSize(850, 600)
        
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(8, 8, 8, 8)
        sidebar_layout.setSpacing(5)

        self.toggle_button = QPushButton()
        self.toggle_button.setObjectName("IconButton")
        self.toggle_button.setIcon(QIcon(resource_path("resources/icons/align-justify.svg")))
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(True)
        self.toggle_button.clicked.connect(self.toggle_sidebar)

        self.nav_button_group = QButtonGroup(self)
        self.nav_button_group.setExclusive(True)
        self.btn_hub = self._create_nav_button("Projects Hub", "resources/icons/house.svg")
        self.btn_dilution = self._create_nav_button("Concentration Plan", "resources/icons/book-image.svg")
        self.btn_layout = self._create_nav_button("Plate Layout", "resources/icons/panel-left-right-dashed.svg")
        self.btn_experiment = self._create_nav_button("Experiment View", "resources/icons/notebook-pen.svg")
        self.btn_photo_assistant = self._create_nav_button("Photo Documentation", "resources/icons/camera.svg")
        self.btn_results = self._create_nav_button("Results and Analysis", "resources/icons/chart-column.svg")

        self.save_btn = QPushButton()
        self.save_btn.setObjectName("IconButton")
        self.save_btn.setIcon(QIcon(resource_path("resources/icons/save.svg")))
        self.save_btn.setToolTip("Save Project (Ctrl+S)")
        self.save_btn.clicked.connect(self.save_project)

        self.btn_export = QPushButton()
        self.btn_export.setObjectName("IconButton")
        self.btn_export.setIcon(QIcon(resource_path("resources/icons/file-up.svg")))
        self.btn_export.setToolTip("Export Project (.zfet)")
        self.btn_export.clicked.connect(self._export_project)

        self.btn_settings = QPushButton()
        self.btn_settings.setObjectName("IconButton")
        self.btn_settings.setIcon(QIcon(resource_path("resources/icons/settings.svg")))
        self.btn_settings.setToolTip("Project Settings")
        self.btn_settings.clicked.connect(self._open_project_settings)

        self.btn_theme = QPushButton()
        self.btn_theme.setObjectName("IconButton")
        self.btn_theme.setIcon(QIcon(resource_path("resources/icons/moon.svg")))
        self.btn_theme.setToolTip("Toggle Theme")
        self.btn_theme.clicked.connect(self.toggle_theme)

        self.btn_about = QPushButton()
        self.btn_about.setObjectName("IconButton")
        self.btn_about.setIcon(QIcon(resource_path("resources/icons/badge-question-mark.svg")))
        self.btn_about.setToolTip("Help and About")
        self.btn_about.clicked.connect(self.show_about_dialog)

        # utility_buttons: shown/hidden on expand/collapse (settings stays always visible)
        self.utility_buttons = [self.btn_theme, self.btn_about]

        self.save_feedback_label = QLabel()
        self.save_feedback_label.setObjectName("SaveFeedbackLabel")
        self.save_feedback_label.setFixedWidth(120)
        self.save_feedback_label.hide()

        bottom_layout = QHBoxLayout()
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.addWidget(self.save_btn)
        bottom_layout.addWidget(self.btn_export)
        bottom_layout.addWidget(self.save_feedback_label, 0, Qt.AlignLeft)
        bottom_layout.addStretch()
        bottom_layout.addWidget(self.btn_settings)
        for btn in self.utility_buttons:
            bottom_layout.addWidget(btn)

        sidebar_layout.addWidget(self.toggle_button, alignment=Qt.AlignLeft)
        sidebar_layout.addSpacing(15)
        sidebar_layout.addWidget(self.btn_hub)
        sidebar_layout.addWidget(self.btn_dilution)
        sidebar_layout.addWidget(self.btn_layout)
        sidebar_layout.addWidget(self.btn_experiment)
        sidebar_layout.addWidget(self.btn_photo_assistant)
        sidebar_layout.addWidget(self.btn_results)
        sidebar_layout.addStretch()
        sidebar_layout.addLayout(bottom_layout)

        self.pages_widget = QStackedWidget()
        main_layout.addWidget(self.sidebar)
        main_layout.addWidget(self.pages_widget, 1)

        self._set_project_pages_enabled(False)
        self.toggle_sidebar(self.toggle_button.isChecked())
        self._update_theme_icon()

    def _setup_shortcuts(self):
        save_action = QAction("Save", self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self.save_project)
        self.addAction(save_action)

    def show_about_dialog(self) -> None:
        if self.about_dialog is None:
            self.about_dialog = AboutDialog(self)
        self.about_dialog.exec()

    def _open_project_settings(self):
        if not self.project_manager: return
        dialog = ProjectSettingsDialog(self.project_manager, self)
        dialog.settings_saved.connect(self._handle_settings_saved)
        dialog.exec()

    def _handle_settings_saved(self, requires_reload: bool):
        project_name = self.project_manager.get_project_name()
        self.setWindowTitle(f"ZebraFET - {project_name}")
        if requires_reload:
            self._reload_project_pages()

    def _create_nav_button(self, text: str, icon_path: str = "") -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("NavButton")
        button.setCheckable(True)
        button.setToolTip(text)
        if icon_path:
            button.setIcon(QIcon(resource_path(icon_path)))
            button.setIconSize(QSize(22, 22))
        self.nav_button_group.addButton(button)
        return button

    def _set_project_pages_enabled(self, enabled: bool):
        self.save_btn.setEnabled(enabled)
        self.btn_export.setEnabled(enabled)
        self.btn_settings.setEnabled(enabled)
        for btn in [self.btn_dilution, self.btn_layout, self.btn_experiment, self.btn_photo_assistant, self.btn_results]:
            btn.setEnabled(enabled)

    def toggle_sidebar(self, checked: bool):
        is_expanding = checked
        collapsed_width = 60   # 8px left margin + 44px button + 8px right margin
        expanded_width = 240
        btn_collapsed_w = collapsed_width - 16  # subtract left+right margins = 44px

        # Read actual current width before any layout changes
        start_width = self.sidebar.width()
        end_width = expanded_width if is_expanding else collapsed_width

        # When collapsing: clear text and lock button widths immediately
        # When expanding: restore text only AFTER animation finishes (prevents jump)
        if not is_expanding:
            for btn in self.nav_button_group.buttons():
                btn.setText("")
                btn.setFixedWidth(btn_collapsed_w)
            self.btn_settings.setFixedWidth(btn_collapsed_w)
            self.save_btn.hide()
            self.btn_export.hide()
            self.save_feedback_label.hide()
            for btn in self.utility_buttons:
                btn.hide()

        if hasattr(self, 'animation') and self.animation and self.animation.state() == QPropertyAnimation.Running:
            self.animation.stop()

        self.animation = QParallelAnimationGroup(self)
        for prop in (b"minimumWidth", b"maximumWidth"):
            anim = QPropertyAnimation(self.sidebar, prop)
            anim.setDuration(220)
            anim.setStartValue(start_width)
            anim.setEndValue(end_width)
            anim.setEasingCurve(QEasingCurve.InOutCubic)
            self.animation.addAnimation(anim)

        if is_expanding:
            def _on_expand_done():
                for btn in self.nav_button_group.buttons():
                    btn.setMaximumWidth(16777215)
                    btn.setText(btn.toolTip())
                self.btn_settings.setMaximumWidth(16777215)
                self.save_btn.show()
                self.btn_export.show()
                if not self.save_feedback_label.isHidden():
                    self.save_feedback_label.show()
                for btn in self.utility_buttons:
                    btn.show()
            self.animation.finished.connect(_on_expand_done)

        self.animation.start()

    def _update_theme_icon(self):
        current_theme = self.theme_manager.current_theme
        icon_name = "sun.svg" if current_theme == "dark" else "moon.svg"
        self.btn_theme.setIcon(QIcon(resource_path(f"resources/icons/{icon_name}")))

    def toggle_theme(self):
        self.theme_manager.toggle_theme()
        self._update_theme_icon()

    def _cleanup_temp_pages(self):
        for page in self.temp_pages:
            if self.pages_widget.indexOf(page) > -1:
                self.pages_widget.removeWidget(page)
            page.deleteLater()
        self.temp_pages.clear()

    def _load_initial_page(self):
        self.hub_page = ProjectHubPage()
        self.pages_widget.addWidget(self.hub_page)
        self.pages_widget.setCurrentWidget(self.hub_page)
        self.btn_hub.setChecked(True)
        self.hub_page.project_path_selected.connect(self.start_project_load)
        self.hub_page.new_project_requested.connect(self.request_create_new_project)
        self.btn_hub.clicked.connect(self.request_show_hub)

    def request_show_hub(self):
        self._show_hub_internal()

    def _show_hub_internal(self):
        # Shut down background threads BEFORE closing the DB connection they use
        self._cleanup_temp_pages()
        while self.pages_widget.count() > 1:
            widget = self.pages_widget.widget(1)
            self.pages_widget.removeWidget(widget)
            if hasattr(widget, 'shutdown'):
                widget.shutdown()
            widget.deleteLater()
        if self.project_manager is not None:
            self.project_manager.close()
        self.project_manager = None
        self.pages_widget.setCurrentWidget(self.hub_page)
        self._set_project_pages_enabled(False)
        self.btn_hub.setChecked(True)
        self.setWindowTitle("ZebraFET")
        self.hub_page.populate_project_grid()

    def request_create_new_project(self):
        self._show_hub_internal()
        creation_page = ProjectCreationPage(parent=self)
        creation_page.project_created.connect(self.on_project_created)
        self.pages_widget.addWidget(creation_page)
        self.pages_widget.setCurrentWidget(creation_page)
        self.temp_pages.append(creation_page)
        self.btn_hub.setChecked(True)

    def on_project_created(self, initial_data: dict):
        project_name = initial_data.get("project_name")
        if not project_name:
            QMessageBox.critical(self, "Error", "Project name cannot be empty.")
            return

        base_dir = ""
        try:
            base_dir = get_projects_base_dir()
        except PermissionError as e:
            error_msg = (f"Could not create the standard projects directory.\n"
                         f"Error: {e}\n\n"
                         f"Would you like to choose an alternative folder to save this project?")
            reply = QMessageBox.question(self, "Permission Error", error_msg,
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

            if reply == QMessageBox.Yes:
                chosen_dir = QFileDialog.getExistingDirectory(self, "Choose a Project Folder")
                if not chosen_dir:
                    log.warning("Project creation cancelled by user after directory selection.")
                    return
                base_dir = chosen_dir
            else:
                log.warning("Project creation cancelled by user due to permission error.")
                return

        sanitized_name = re.sub(r'[<>:"/\\|?*]', '_', project_name.strip())
        project_path = os.path.join(base_dir, sanitized_name)

        if os.path.exists(project_path):
            reply = QMessageBox.question(self, "Project Exists",
                                           f"A project named '{sanitized_name}' already exists.\n"
                                           "Do you want to overwrite it? All existing data will be lost.",
                                           QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.No:
                return
            else:
                try:
                    shutil.rmtree(project_path)
                except OSError as e:
                    QMessageBox.critical(self, "Error", f"Could not overwrite project:\n{e}")
                    return
        
        try:
            manager = ProjectManager.create_new(project_path, initial_data)
            log.info(f"New project '{project_name}' created at: {project_path}")
            self.on_project_loaded(manager, is_new_project=True)
        except Exception as e:
            log.error(f"Failed to create and load new project: {e}", exc_info=True)
            QMessageBox.critical(self, "Error", f"Could not create project.\nCheck logs for details.")
            self._show_hub_internal()

    def open_archive(self, path: str):
        if not os.path.isfile(path):
            return
        self._show_hub_internal()
        self.hub_page._import_project(path)

    def save_project(self):
        if not self.project_manager:
            return
        # All writes are transactional; trigger a registry sync and show feedback.
        self.project_manager._sync_to_registry()
        self._show_save_feedback(True, "All changes saved.", force_show=True)

    def _export_project(self):
        if not self.project_manager:
            return
        from src.core.project_exporter import export_project
        default_name = f"{self.project_manager.get_project_name()}.zfet"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Project", default_name,
            "ZebraFET Archive (*.zfet)"
        )
        if not path:
            return
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                export_project(self.project_manager, path)
            finally:
                QApplication.restoreOverrideCursor()
            QMessageBox.information(self, "Export Complete",
                f"Project exported successfully:\n{path}")
        except Exception as e:
            log.error(f"Export failed: {e}", exc_info=True)
            QMessageBox.critical(self, "Export Failed", str(e))

    def _show_save_feedback(self, success: bool, message: str, force_show: bool = False):
        if "no changes detected" in message and not force_show:
            return

        feedback_text = "Project Saved!"
        if "no changes detected" in message:
            feedback_text = "No changes to save"
        elif not success:
            feedback_text = "Save Failed!"

        self.save_feedback_label.setText(feedback_text)
        self.save_feedback_label.setProperty("success", success)
        self.save_feedback_label.setProperty("failure", not success)
        self.save_feedback_label.style().unpolish(self.save_feedback_label)
        self.save_feedback_label.style().polish(self.save_feedback_label)

        if self.toggle_button.isChecked():
            self.save_feedback_label.show()

        QTimer.singleShot(3000, lambda: self.save_feedback_label.hide())

    # Methods to handle threaded project loading
    def start_project_load(self, path: str):
        if self.load_thread is not None and self.load_thread.isRunning():
            log.warning("Project load requested while another load is in progress; ignoring.")
            return

        self.setEnabled(False) # Disable main window to prevent interaction

        self.load_thread = QThread(self)
        self.load_worker = ProjectLoadWorker(path)
        self.load_worker.moveToThread(self.load_thread)

        # Connect worker signals to main thread slots
        self.load_worker.finished.connect(self.on_project_loaded)
        self.load_worker.error.connect(self._handle_project_load_error)
        self.load_thread.started.connect(self.load_worker.run)

        # Clean up thread and worker after completion
        self.load_worker.finished.connect(self.load_thread.quit)
        self.load_worker.error.connect(self.load_thread.quit)
        self.load_thread.finished.connect(self.load_thread.deleteLater)
        self.load_thread.finished.connect(lambda: setattr(self, 'load_thread', None))
        self.load_worker.finished.connect(self.load_worker.deleteLater)
        self.load_worker.error.connect(self.load_worker.deleteLater)

        self.load_thread.start()

    def _handle_project_load_error(self, message: str):
        log.error(f"Project load error: {message}")
        QMessageBox.critical(self, "Project Load Error", message)
        self.setEnabled(True) # Re-enable window
        self._show_hub_internal() # Return to hub

    def on_project_loaded(self, manager: ProjectManager, is_new_project: bool = False):
        """
        Finalizes project loading on the main thread after the worker succeeds.
        This method now primarily handles UI updates.
        """
        self.setEnabled(True)

        self._cleanup_temp_pages()
        self.project_manager = manager
        
        try:
            self._reload_project_pages(is_new_project=is_new_project)
            project_name = self.project_manager.get_project_name()
            self.setWindowTitle(f"ZebraFET - {project_name}")

            if not is_new_project:
                first_incomplete_day = self.project_manager.get_first_incomplete_day()
                if hasattr(self, 'experiment_page') and first_incomplete_day is not None:
                    day_index = max(0, first_incomplete_day - 1)
                    self.experiment_page.day_tabs.setCurrentIndex(day_index)

            self.hub_page.populate_project_grid()
        except Exception as e:
            log.error(f"Error while setting up project pages: {e}", exc_info=True)
            QMessageBox.critical(self, "Error", f"An error occurred while opening the project view.")
            self._show_hub_internal()

    def _reload_project_pages(self, is_new_project: bool = False):
        for btn, slot in self.active_connections.items():
            try: btn.clicked.disconnect(slot)
            except (RuntimeError, TypeError): pass
        self.active_connections.clear()

        while self.pages_widget.count() > 1:
            widget = self.pages_widget.widget(1)
            self.pages_widget.removeWidget(widget)
            if hasattr(widget, 'shutdown'):
                widget.shutdown()
            widget.deleteLater()

        _info = self.project_manager.get_project_info()
        _conc_data = {
            "project_name": _info.get("project_name", ""),
            "concentration_settings": {
                "concentrations": self.project_manager.get_concentrations(),
                **self.project_manager.get_concentration_settings(),
            },
        }
        self.concentration_page = ConcentrationPlannerPage(
            _conc_data,
            is_editing=True,
            parent=self,
            manager=self.project_manager,
        )
        self.layout_page = PlateLayoutPage(self.project_manager)
        self.experiment_page = ExperimentViewWidget(self.project_manager)
        self.photo_assistant_page = PhotoAssistantWidget(self.project_manager)
        self.results_page = ResultsAnalysisWidget(self.project_manager)

        self.concentration_page.project_settings_changed.connect(
            lambda: self._reload_project_pages(is_new_project=False)
        )
        self.layout_page.layout_saved.connect(self.experiment_page.refresh_view)
        self.experiment_page.well_editor.data_changed.connect(
            lambda *_: self.results_page.mark_dirty()
        )

        page_map = {
            self.btn_dilution: self.concentration_page,
            self.btn_layout: self.layout_page,
            self.btn_experiment: self.experiment_page,
            self.btn_photo_assistant: self.photo_assistant_page,
            self.btn_results: self.results_page,
        }
        for btn, page in page_map.items():
            self.pages_widget.addWidget(page)
            slot = lambda checked, p=page: self.switch_page(p)
            btn.clicked.connect(slot)
            self.active_connections[btn] = slot

        self._set_project_pages_enabled(True)
        
        initial_page = self.layout_page if is_new_project else self.experiment_page
        active_button = self.btn_layout if is_new_project else self.btn_experiment
        
        for i in range(self.pages_widget.count()):
            widget = self.pages_widget.widget(i)
            if widget != initial_page:
                widget.setVisible(False)
        self.pages_widget.setCurrentWidget(initial_page)
        active_button.setChecked(True)

        if hasattr(initial_page, 'enter_page'):
            initial_page.enter_page()

    def switch_page(self, page_to_show: QWidget):
        if self.pages_widget.currentWidget() is page_to_show:
            return

        if self.page_transition_animation and self.page_transition_animation.state() == QPropertyAnimation.Running:
            self.page_transition_animation.stop()

        current_widget = self.pages_widget.currentWidget()
        page_to_show.setVisible(True)

        current_effect = QGraphicsOpacityEffect(current_widget)
        current_widget.setGraphicsEffect(current_effect)
        new_effect = QGraphicsOpacityEffect(page_to_show)
        page_to_show.setGraphicsEffect(new_effect)

        fade_out = QPropertyAnimation(current_effect, b"opacity")
        fade_out.setDuration(250); fade_out.setStartValue(1.0); fade_out.setEndValue(0.0)
        fade_out.setEasingCurve(QEasingCurve.InOutCubic)

        fade_in = QPropertyAnimation(new_effect, b"opacity")
        fade_in.setDuration(250); fade_in.setStartValue(0.0); fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.InOutCubic)

        self.page_transition_animation = QParallelAnimationGroup(self)
        self.page_transition_animation.addAnimation(fade_out)
        self.page_transition_animation.addAnimation(fade_in)

        def on_animation_finished():
            self.pages_widget.setCurrentWidget(page_to_show)
            current_widget.setVisible(False)
            current_widget.setGraphicsEffect(None)
            page_to_show.setGraphicsEffect(None)

            if hasattr(page_to_show, 'enter_page'):
                page_to_show.enter_page()
            if hasattr(page_to_show, 'setFocus'):
                page_to_show.setFocus()
            if hasattr(page_to_show, 'refresh_view'):
                page_to_show.refresh_view()

        self.page_transition_animation.finished.connect(on_animation_finished)
        self.page_transition_animation.start()