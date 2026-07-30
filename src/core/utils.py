# utils.py
import os
import sys
import logging

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

#: Which variant of a two-tone icon suits each theme. The suffix names the ink,
#: not the background: a dark theme needs light strokes. Themes not listed here
#: fall back to the dark-ink variant, which reads on any pale background.
_ICON_VARIANT_FOR_THEME = {"dark": "light", "light": "dark"}

_active_icon_theme = "dark"


def set_icon_theme(theme_name: str) -> None:
    """
    Selects the icon variant to serve and drops the cache.

    QIcon instances already handed out keep the old artwork, so callers that
    hold on to icons have to ask for them again after a theme change; see
    MainWindow._refresh_themed_icons.
    """
    global _active_icon_theme
    if theme_name == _active_icon_theme:
        return
    _active_icon_theme = theme_name
    _icon_cache.clear()


def themed_icon_name(base_name: str) -> str:
    """
    Resolves a two-tone icon's base name to the file for the active theme.

    'eye' becomes 'eye-light.svg' under the dark theme and 'eye-dark.svg' under
    the light one. A base name with no variant on disk resolves to itself, so
    single-tone icons can be requested the same way.
    """
    variant = _ICON_VARIANT_FOR_THEME.get(_active_icon_theme, "dark")
    candidate = f"{base_name}-{variant}.svg"
    if os.path.exists(resource_path(f"resources/icons/{candidate}")):
        return candidate
    return f"{base_name}.svg"


def create_icon(icon_name: str):
    """
    Returns a cached QIcon for the given icon file name.

    The cache is keyed on the file name and cleared whenever the theme changes,
    so a themed variant is never served after the theme it belongs to is gone.
    """
    from PySide6.QtGui import QIcon
    if icon_name not in _icon_cache:
        _icon_cache[icon_name] = QIcon(resource_path(f"resources/icons/{icon_name}"))
    return _icon_cache[icon_name]


def create_themed_icon(base_name: str):
    """Returns the QIcon for *base_name* in the variant matching the active theme."""
    return create_icon(themed_icon_name(base_name))


#: The organization and application the settings file is filed under. Releases up
#: to 2.1.4 filed it under "ZebraFET Hub", a name that appeared nowhere else and
#: leaked into the window manager; _migrate_legacy_settings carries those keys over.
SETTINGS_ORG = "ZebraFET"
SETTINGS_APP = "ZebraFET"
_LEGACY_SETTINGS_APP = "ZebraFET Hub"

_settings_migrated = False


def app_settings():
    """
    Returns the application settings store.

    Every caller must go through here: a bare QSettings() resolves to NativeFormat
    and would read a different file from the one the setup wizard writes.
    """
    from PySide6.QtCore import QSettings
    settings = QSettings(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        SETTINGS_ORG,
        SETTINGS_APP,
    )
    _migrate_legacy_settings(settings)
    return settings


def _migrate_legacy_settings(settings) -> None:
    """
    Copies keys from the pre-2.2 settings file the first time it is needed.

    Without this an upgrade would lose the data directory and re-run the setup
    wizard on a machine that had already completed it. Existing keys win, so the
    copy is skipped once the current file has been written.
    """
    global _settings_migrated
    if _settings_migrated or settings.contains("setup/completed"):
        _settings_migrated = True
        return

    from PySide6.QtCore import QSettings
    legacy = QSettings(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        SETTINGS_ORG,
        _LEGACY_SETTINGS_APP,
    )
    for key in legacy.allKeys():
        if not settings.contains(key):
            settings.setValue(key, legacy.value(key))
    settings.sync()
    _settings_migrated = True


def _get_base_data_dir() -> str:
    """
    Returns the user-configured data directory (from the setup wizard) or the
    OS default if no custom directory has been set.
    """
    settings = app_settings()
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