"""
test_registry.py — The hub index must survive a project rename.

Project Settings lets the name change while the database path stays put, so a
rename presents the registry with a new name for an already-registered
db_path. The UNIQUE index on db_path rejected that insert, ProjectManager
swallowed the error, and the hub kept the old name while the project's
progress count stopped advancing for the rest of its life.
"""
import pytest

import src.core.utils as utils
from src.core.project_manager import ProjectManager
from src.database.registry import ProjectRegistry


def _rows():
    # Resolved through the module so the conftest redirect into tmp_path applies;
    # a from-import would bind the real user data directory at collection time.
    registry = ProjectRegistry(utils.get_registry_db_path())
    try:
        return registry.list_projects()
    finally:
        registry.close()


def _row_for(db_path: str):
    return next((r for r in _rows() if r["db_path"] == db_path), None)


@pytest.fixture
def project(tmp_path):
    m = ProjectManager.create_new(
        str(tmp_path / "Original"),
        {"project_name": "Original", "num_days": 3, "num_plates": 1},
    )
    yield m
    m.close()


class TestRename:
    def test_registry_follows_the_new_name(self, project):
        project.update_project_info(project_name="Renamed")
        row = _row_for(project.db_path)
        assert row is not None
        assert row["project_name"] == "Renamed"

    def test_the_old_name_does_not_linger(self, project):
        project.update_project_info(project_name="Renamed")
        assert [r["project_name"] for r in _rows()].count("Original") == 0

    def test_one_row_per_project_after_repeated_renames(self, project):
        for name in ("A", "B", "C"):
            project.update_project_info(project_name=name)
        assert len(_rows()) == 1
        assert _row_for(project.db_path)["project_name"] == "C"

    def test_progress_keeps_updating_after_a_rename(self, project):
        """The regression that made this invisible: sync failed silently."""
        project.update_project_info(project_name="Renamed")
        project.set_day_as_completed(1)
        project.set_day_as_completed(2)
        assert _row_for(project.db_path)["completed_days_count"] == 2

    def test_metadata_edits_still_reach_the_registry(self, project):
        project.update_project_info(project_name="Renamed", substance="Copper")
        assert _row_for(project.db_path)["substance"] == "Copper"


class TestOrdinaryUpserts:
    def test_repeat_sync_under_the_same_name_updates_in_place(self, project):
        project.set_day_as_completed(1)
        project.set_day_as_completed(2)
        assert len(_rows()) == 1
        assert _row_for(project.db_path)["completed_days_count"] == 2

    def test_distinct_projects_both_stay_registered(self, tmp_path):
        a = ProjectManager.create_new(
            str(tmp_path / "Alpha"), {"project_name": "Alpha", "num_days": 1}
        )
        b = ProjectManager.create_new(
            str(tmp_path / "Beta"), {"project_name": "Beta", "num_days": 1}
        )
        try:
            names = {r["project_name"] for r in _rows()}
            assert {"Alpha", "Beta"} <= names
        finally:
            a.close()
            b.close()
