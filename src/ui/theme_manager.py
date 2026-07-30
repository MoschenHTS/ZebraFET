import os
import logging
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QSettings  # Import QSettings
from src.core.utils import resource_path

log = logging.getLogger(__name__)

class ThemeManager:
    """
    Manages loading, applying, and persisting QSS themes for the application.
    """
    # Define a key for storing the theme setting
    THEME_SETTING_KEY = "Appearance/Theme"

    #: The themes toggle_theme cycles through. The themes directory may hold more
    #: than these — anything not listed here is loaded but never offered, so a
    #: settings key naming one must not be applied on the next launch.
    SELECTABLE_THEMES = ("light", "dark")
    DEFAULT_THEME = "dark"

    def __init__(self, app: QApplication, settings: QSettings):
        """
        Initializes the ThemeManager.
        Args:
            app (QApplication): The main application instance.
            settings (QSettings): The application's settings manager.
        """
        self.app = app
        self.settings = settings
        self.themes = self._load_themes()
        # The current theme will be set by apply_last_theme()
        self.current_theme = "" 

    def _load_themes(self) -> dict:
        """
        Loads the content of all QSS files from the 'themes' directory.
        """
        # Scan the themes directory for .qss files
        themes_dir = resource_path("resources/themes")
        loaded_themes = {}
        try:
            for filename in os.listdir(themes_dir):
                if filename.endswith(".qss"):
                    theme_name = os.path.splitext(filename)[0]
                    stylesheet = self._load_stylesheet(filename)
                    if stylesheet:
                        loaded_themes[theme_name] = stylesheet
            log.info(f"Loaded themes: {list(loaded_themes.keys())}")
        except FileNotFoundError:
            log.error(f"Themes directory not found at: {themes_dir}")
        return loaded_themes

    def _load_stylesheet(self, filename: str) -> str:
        """
        Loads a specific QSS file and returns its content.
        """
        style_path = resource_path(os.path.join("resources", "themes", filename))
        try:
            with open(style_path, "r", encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            log.warning(f"Stylesheet not found at: {style_path}")
            return f"/* Stylesheet '{filename}' not found. */"

    def apply_theme(self, theme_name: str):
        """
        Applies a theme by name and saves the preference.
        """
        if theme_name in self.themes:
            self.current_theme = theme_name
            self.app.setStyleSheet(self.themes[theme_name])
            # Save the chosen theme to settings for the next launch
            self.settings.setValue(self.THEME_SETTING_KEY, theme_name)
            log.info(f"Applied and saved '{theme_name}' theme.")
        else:
            log.warning(f"Theme '{theme_name}' not found.")

    def apply_last_theme(self):
        """
        Loads and applies the theme stored in settings, or a default.

        A stored name that is no longer selectable — or no longer present in the
        themes directory — falls back to the default rather than leaving the
        window in a state the theme control cannot get out of.
        """
        last_theme = self.settings.value(self.THEME_SETTING_KEY, self.DEFAULT_THEME)
        if last_theme not in self.SELECTABLE_THEMES or last_theme not in self.themes:
            log.warning(f"Stored theme '{last_theme}' is not selectable; "
                        f"falling back to '{self.DEFAULT_THEME}'.")
            last_theme = self.DEFAULT_THEME
        self.apply_theme(last_theme)

    def toggle_theme(self):
        """ Switches between the light and dark themes. """
        if self.current_theme == "light":
            self.apply_theme("dark")
        else:
            self.apply_theme("light")