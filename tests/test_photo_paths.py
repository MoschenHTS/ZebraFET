"""
test_photo_paths.py — well_photos.relative_path is stored with forward slashes.

The column is matched by equality, so the separator must be identical at write
and at lookup. Photos added on Windows were written with backslashes by
os.path.join while removal normalized to forward slashes, so the DELETE matched
nothing: the Remove button reported success, the thumbnail stayed, and the row
survived. Projects also move between platforms as .zfet archives, so the stored
form has to be one every platform produces.
"""
import os
import sqlite3

import pytest
from PIL import Image

from src.core.project_manager import ProjectManager, normalize_rel_path

CONCENTRATIONS = [
    {"id": "ctrl", "type": "Control", "value": 0.0, "replicates": 1, "wells": 2, "per_plate": True},
]


@pytest.fixture
def project(tmp_path):
    m = ProjectManager.create_new(
        str(tmp_path / "Photos"), {"project_name": "Photos", "num_days": 1, "num_plates": 1}
    )
    m.set_concentrations(CONCENTRATIONS, required_embryos=2, required_plates=1)
    m.commit_plate_layout({"1": {"A1": "ctrl", "A2": "ctrl"}})
    yield m
    m.close()


@pytest.fixture
def source_image(tmp_path):
    path = tmp_path / "source.png"
    Image.new("RGB", (16, 16), (120, 120, 120)).save(path)
    return str(path)


class TestNormalizeRelPath:
    def test_backslashes_become_forward_slashes(self):
        assert normalize_rel_path(r"photos\Day_1\A1.jpg") == "photos/Day_1/A1.jpg"

    def test_forward_slashes_are_left_alone(self):
        assert normalize_rel_path("photos/Day_1/A1.jpg") == "photos/Day_1/A1.jpg"


class TestStoredForm:
    def test_added_photo_is_stored_with_forward_slashes(self, project, source_image):
        rel = project.add_photo_to_well(1, 1, "A1", source_image)
        assert rel is not None
        assert "\\" not in rel
        stored = project._conn.execute(
            "SELECT relative_path FROM well_photos"
        ).fetchone()[0]
        assert "\\" not in stored

    def test_added_photo_round_trips_through_the_getter(self, project, source_image):
        project.add_photo_to_well(1, 1, "A1", source_image)
        assert project.get_photos_for_well(1, 1, "A1")


class TestRemoval:
    def test_removes_row_and_file(self, project, source_image):
        rel = project.add_photo_to_well(1, 1, "A1", source_image)
        full = project.get_full_photo_path(rel)
        assert os.path.isfile(full)

        assert project.remove_photo_by_path(full) is True
        assert not os.path.isfile(full)
        assert project._conn.execute(
            "SELECT COUNT(*) FROM well_photos"
        ).fetchone()[0] == 0

    def test_removal_succeeds_for_a_windows_written_row(self, project, source_image):
        """A backslash row is still removable even if the migration never ran."""
        rel = project.add_photo_to_well(1, 1, "A1", source_image)
        full = project.get_full_photo_path(rel)
        with project._conn:
            project._conn.execute(
                "UPDATE well_photos SET relative_path = ?", (rel.replace("/", "\\"),)
            )

        assert project.remove_photo_by_path(full) is True
        assert not os.path.isfile(full)
        assert project._conn.execute(
            "SELECT COUNT(*) FROM well_photos"
        ).fetchone()[0] == 0


class TestMigration:
    def test_v9_project_is_normalized_on_open(self, tmp_path):
        """Databases written before v10 carry backslash paths from Windows."""
        proj_dir = tmp_path / "Legacy"
        m = ProjectManager.create_new(
            str(proj_dir), {"project_name": "Legacy", "num_days": 1, "num_plates": 1}
        )
        m.set_concentrations(CONCENTRATIONS, required_embryos=2, required_plates=1)
        m.commit_plate_layout({"1": {"A1": "ctrl"}})
        m.save_well_data(1, 1, "A1", "Live Embryo", [], [], "")
        db_path = m.db_path
        with m._conn:
            m._conn.execute(
                "INSERT INTO well_photos (day, plate_index, well_id, relative_path, added_at)"
                " VALUES (1, 1, 'A1', ?, 't')",
                (r"photos\Day_1\A1_Plate1_abc.jpg",),
            )
        m.close()

        # Rewind the recorded schema version so the migration runner replays v10.
        conn = sqlite3.connect(db_path)
        with conn:
            conn.execute("UPDATE schema_version SET version = 9")
        conn.close()

        reopened = ProjectManager(str(proj_dir))
        try:
            stored = reopened._conn.execute(
                "SELECT relative_path FROM well_photos"
            ).fetchone()[0]
            assert stored == "photos/Day_1/A1_Plate1_abc.jpg"
        finally:
            reopened.close()
