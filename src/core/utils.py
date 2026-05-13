# utils.py
import os
import sys
import logging
from PySide6.QtGui import QIcon

# Configure logging
log = logging.getLogger(__name__)

# Project root: navigate up from src/core/ → src/ → project root
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

def resource_path(relative_path: str) -> str:
    """
    Get the absolute path to a resource, works for dev and for PyInstaller.
    All resource paths are relative to the project root (e.g. 'resources/icons/house.svg').
    """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = _PROJECT_ROOT
    return os.path.join(base_path, relative_path)

_icon_cache: dict = {}

def create_icon(icon_name: str) -> QIcon:
    """
    Returns a cached QIcon for the given icon name, loading from disk only once.
    """
    if icon_name not in _icon_cache:
        _icon_cache[icon_name] = QIcon(resource_path(f"resources/icons/{icon_name}"))
    return _icon_cache[icon_name]

def _get_base_data_dir() -> str:
    """
    Returns the user-configured data directory (from the setup wizard) or the
    OS default if no custom directory has been set.
    """
    from PySide6.QtCore import QSettings
    settings = QSettings("ZebraFET", "ZebraFET Hub")
    custom = settings.value("setup/data_dir", "")
    if custom:
        parent = os.path.dirname(custom) or custom
        if os.path.isdir(parent):
            os.makedirs(custom, exist_ok=True)
            return custom

    # OS default
    app_name = "ZebraFET"
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", app_name)
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(appdata, app_name)
    return os.path.join(os.path.expanduser("~"), "Documents", app_name)


def get_registry_db_path() -> str:
    """
    Returns the path to the global project registry database.
    The file lives in the ZebraFET data directory (user-configured or OS default).
    """
    base_path = _get_base_data_dir()
    os.makedirs(base_path, exist_ok=True)
    return os.path.join(base_path, "registry.db")


def get_projects_base_dir() -> str:
    """
    Determines and creates the base directory for projects.
    Respects a custom data directory set during the setup wizard.
    Raises PermissionError if the directory cannot be created.
    """
    base_path = _get_base_data_dir()
    projects_path = os.path.join(base_path, "projects")

    try:
        os.makedirs(projects_path, exist_ok=True)
        log.info(f"Projects base directory is set to: {projects_path}")
        return projects_path
    except OSError as e:
        log.error(f"Could not create projects directory at {projects_path}: {e}")
        raise PermissionError(
            f"Failed to create the directory '{projects_path}'. "
            "Please check your system's permissions."
        )