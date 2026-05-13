import sys
import logging
import traceback
from logging.handlers import RotatingFileHandler
from PySide6.QtWidgets import (QApplication, QMessageBox, QWidget, QVBoxLayout,
                             QHBoxLayout, QLabel, QGraphicsOpacityEffect)
from PySide6.QtCore import (QSettings, Qt, QPropertyAnimation, QEasingCurve, QTimer,
                             QObject, Signal, QStandardPaths)
from PySide6.QtGui import QIcon, QPixmap

from src.ui.main_window import MainWindow
from src.ui.theme_manager import ThemeManager
from src.core.utils import resource_path
from src.core.constants import APP_VERSION

log = logging.getLogger(__name__)


class CustomSplashScreen(QWidget):
    """
    A Splash screen that fades out smoothly.
    """
    def __init__(self, pixmap: QPixmap):
        super().__init__()
        self.setWindowFlags(
            Qt.SplashScreen |
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(600, 300)
        self._init_ui(pixmap)

    def _init_ui(self, pixmap: QPixmap):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        container = QWidget()
        container.setObjectName("SplashContainer")
        container_layout = QHBoxLayout(container)
        container_layout.setContentsMargins(30, 20, 30, 20)
        container_layout.setSpacing(25)

        # Icon on the left
        icon_label = QLabel()
        icon_label.setPixmap(pixmap.scaled(128, 128, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        icon_label.setAlignment(Qt.AlignCenter)

        # Vertical layout for text on the right
        text_container = QWidget()
        text_layout = QVBoxLayout(text_container)
        text_layout.setAlignment(Qt.AlignCenter)
        text_layout.setContentsMargins(0, 0, 0, 0)

        # Title
        title_label = QLabel("ZebraFET")
        title_font = self.font()
        title_font.setPointSize(48)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignCenter)

        # Loading message
        self.message_label = QLabel("Loading application...")
        message_font = self.font()
        message_font.setPointSize(12)
        self.message_label.setFont(message_font)
        self.message_label.setAlignment(Qt.AlignCenter)

        text_layout.addWidget(title_label)
        text_layout.addWidget(self.message_label)
        
        container_layout.addWidget(icon_label)
        container_layout.addWidget(text_container, 1)

        main_layout.addWidget(container)
        
        self.setStyleSheet("""
            #SplashContainer {
                background-color: #2c3e50;
                border-radius: 15px;
            }
            QLabel {
                background-color: transparent;
                color: #ecf0f1;
            }
        """)

    def close_splash(self):
        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity_effect)
        
        self.animation = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.animation.setDuration(400)
        self.animation.setStartValue(1.0)
        self.animation.setEndValue(0.0)
        self.animation.setEasingCurve(QEasingCurve.Type.OutQuad)
        self.animation.finished.connect(self.close)
        self.animation.start()


class _ExceptionRelay(QObject):
    """
    Routes unhandled exceptions from any thread to the GUI thread via a
    Qt queued signal, preventing PySide6 segfaults that occur when
    QMessageBox is called directly from a non-GUI thread.

    Note: QThread exceptions not re-emitted via a signal are silently
    swallowed by Qt. Worker threads should catch and emit errors explicitly.
    """
    _exception_occurred = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._in_dialog = False
        self._exception_occurred.connect(self._show_dialog, Qt.QueuedConnection)
        sys.excepthook = self._hook

    def _hook(self, exc_type, exc_value, exc_tb):
        try:
            msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
            log.critical(f"Unhandled exception:\n{msg}")
            self._exception_occurred.emit(msg)
        except Exception:
            pass  # prevent recursive exception hook invocation

    def _show_dialog(self, msg: str):
        if self._in_dialog:
            return
        self._in_dialog = True
        try:
            QMessageBox.critical(
                None, "Unexpected Error",
                f"An unexpected error occurred. Please check ZebraFET.log.\n\n{msg[:800]}"
            )
        finally:
            self._in_dialog = False

def setup_logging(log_file_path: str | None = None):
    log_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - [%(name)s] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter)
    root_logger.addHandler(console_handler)
    if log_file_path:
        file_handler = RotatingFileHandler(log_file_path, maxBytes=5*1024*1024, backupCount=3)
        file_handler.setFormatter(log_formatter)
        root_logger.addHandler(file_handler)
    logging.info("Logging configured successfully.")


def main():
    # Console-only logging until QApplication (and thus QStandardPaths) is available
    setup_logging()

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("ZebraFET Hub")
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("ZebraFET")

    # Add file handler now that QStandardPaths is available
    import os
    _app_data = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    os.makedirs(_app_data, exist_ok=True)
    _log_path = os.path.join(_app_data, "zebrafet.log")
    _fh = __import__('logging.handlers', fromlist=['RotatingFileHandler']).RotatingFileHandler(
        _log_path, maxBytes=5 * 1024 * 1024, backupCount=3
    )
    _fh.setFormatter(logging.Formatter(
        '%(asctime)s - %(levelname)s - [%(name)s] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    logging.getLogger().addHandler(_fh)
    logging.info(f"File logging enabled at: {_log_path}")

    # Must be instantiated after QApplication so the Signal/Slot mechanism is ready
    _relay = _ExceptionRelay()

    # Set Application Icon
    icon_path = resource_path("resources/icons/fishapp_icon.png")
    app.setWindowIcon(QIcon(icon_path))

    #  Splash Screen Implementation
    splash = None
    try:
        pixmap = QPixmap(icon_path)
        splash = CustomSplashScreen(pixmap)
        
        screen_geometry = app.primaryScreen().geometry()
        splash.move(screen_geometry.center() - splash.rect().center())
        
        splash.show()
        app.processEvents()
    except Exception as e:
        log.error(f"Could not create or show splash screen: {e}")

    settings = QSettings(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        app.organizationName(),
        app.applicationName()
    )

    # First-run setup wizard — shown before the main window on a fresh install
    if not settings.value("setup/completed", False, type=bool):
        if splash:
            splash.hide()
        from src.ui.setup_wizard import SetupWizard
        from PySide6.QtWidgets import QDialog
        wizard = SetupWizard(settings)
        if wizard.exec() != QDialog.DialogCode.Accepted:
            sys.exit(0)
        if splash:
            splash.show()
            app.processEvents()

    theme_manager = ThemeManager(app, settings)
    
    window = MainWindow(settings, theme_manager)
    
    theme_manager.apply_last_theme()
    
    QTimer.singleShot(500, window.show)
    
    if splash:
        QTimer.singleShot(600, splash.close_splash)

    try:
        sys.exit(app.exec())
    except Exception as e:
        logging.critical(f"Application exited with an exception: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()