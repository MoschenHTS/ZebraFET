"""
conftest.py — Shared pytest configuration and fixtures for ZebraFET tests.

Adds the project root to sys.path so that project_manager, constants,
biostatistics, and the database package can be imported without Qt.
"""
import os
import sys

# Qt must not try to reach a display. Set before anything imports PySide6, and
# only as a default so a developer can override it to watch a test render.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import matplotlib
matplotlib.use("Agg")

import pytest

# Insert the project root (one level above this file) at the front of sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture(autouse=True)
def _isolated_app_data(tmp_path, monkeypatch):
    """Redirect every project-registry and projects-base write into tmp_path.

    ProjectManager._sync_to_registry(), .remove_from_registry(), and
    project_exporter.import_project() all resolve their target path via
    src.core.utils.get_registry_db_path() / get_projects_base_dir() through a
    function-local import at call time, so patching those two names on the
    utils module intercepts every one of them. Left unpatched, any test that
    creates a ProjectManager permanently registers a project in the developer's
    real, local ZebraFET application data directory — this happened silently
    for months before being noticed and cleaned up by hand.
    """
    import src.core.utils as utils

    fake_base = tmp_path / "zebrafet_app_data"
    fake_projects = fake_base / "projects"
    fake_projects.mkdir(parents=True)

    monkeypatch.setattr(utils, "get_registry_db_path", lambda: str(fake_base / "registry.db"))
    monkeypatch.setattr(utils, "get_projects_base_dir", lambda: str(fake_projects))
