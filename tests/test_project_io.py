"""
test_project_io.py — Verify export/import round-trip for ZebraFET archives.

Tests that:
  - export_project() creates a valid ZIP with the .zfet extension
  - The archive contains a .db file at the root
  - All concentration groups, plate layouts, and well observations
    survive a round-trip (export → manual extract → reopen)
  - Both .zfet and legacy .zebravet extensions produce valid archives
    (zipfile is extension-agnostic)

Note: import_project() imports utils.get_projects_base_dir() which pulls in
PySide6 (a Qt dependency not available in headless CI). This test therefore
exercises extract-then-open manually instead of calling import_project().
"""
import os
import zipfile
import pytest

from src.core.project_manager import ProjectManager
from src.core.project_exporter import export_project
from src.core.constants import STATUS_DEAD_EMBRYO, STATUS_LIVE_EMBRYO, STATUS_LIVE_HATCHED


INITIAL_DATA = {
    "project_name": "TestIO",
    "main_researcher": "Test Researcher",
    "substance": "TestSubstance",
    "num_days": 4,
    "num_plates": 1,
}

CONCENTRATIONS = [
    {"id": "ctrl", "type": "Control",   "value": 0.0, "replicates": 1, "wells": 4, "per_plate": True},
    {"id": "s1",   "type": "Substrate", "value": 1.0, "replicates": 1, "wells": 4, "per_plate": True},
    {"id": "s2",   "type": "Substrate", "value": 2.0, "replicates": 1, "wells": 4, "per_plate": True},
]

LAYOUT = {
    "0": {
        "A1": "ctrl", "A2": "ctrl",
        "B1": "s1",   "B2": "s1",
        "C1": "s2",   "C2": "s2",
    }
}


def _build_project(tmp_path) -> ProjectManager:
    """Create a fully populated project for IO testing."""
    proj_dir = str(tmp_path / "TestIO")
    m = ProjectManager.create_new(proj_dir, INITIAL_DATA)
    m.set_concentrations(CONCENTRATIONS, required_embryos=12, required_plates=1)
    m.commit_plate_layout(LAYOUT)
    m.save_well_data(
        day=1, plate_index=0, well_id="A1",
        status=STATUS_LIVE_EMBRYO,
        sublethal_conditions=[], lethal_conditions=[], notes="control note",
    )
    m.save_well_data(
        day=1, plate_index=0, well_id="B1",
        status=STATUS_DEAD_EMBRYO,
        sublethal_conditions=["Yolk sac oedema"],
        lethal_conditions=["Lack of heartbeat"],
        notes="",
    )
    m.save_well_data(
        day=1, plate_index=0, well_id="C1",
        status=STATUS_LIVE_HATCHED,
        sublethal_conditions=[], lethal_conditions=[], notes="",
    )
    return m


class TestExportArchive:
    def test_export_creates_file(self, tmp_path):
        m = _build_project(tmp_path)
        try:
            archive = str(tmp_path / "export.zfet")
            export_project(m, archive)
            assert os.path.isfile(archive)
        finally:
            m.close()

    def test_archive_is_valid_zip(self, tmp_path):
        m = _build_project(tmp_path)
        try:
            archive = str(tmp_path / "export.zfet")
            export_project(m, archive)
            assert zipfile.is_zipfile(archive)
        finally:
            m.close()

    def test_archive_contains_db_at_root(self, tmp_path):
        m = _build_project(tmp_path)
        try:
            archive = str(tmp_path / "export.zfet")
            export_project(m, archive)
            with zipfile.ZipFile(archive) as zf:
                names = zf.namelist()
            db_entries = [n for n in names if n.endswith(".db") and "/" not in n]
            assert len(db_entries) == 1
            assert db_entries[0] == "TestIO.db"
        finally:
            m.close()

    def test_zebravet_extension_also_valid_zip(self, tmp_path):
        """Extension should not affect zipfile validity."""
        m = _build_project(tmp_path)
        try:
            archive = str(tmp_path / "export.zebravet")
            export_project(m, archive)
            assert zipfile.is_zipfile(archive)
        finally:
            m.close()


class TestRoundTrip:
    def _export_and_reopen(self, tmp_path, extension=".zfet"):
        """Export a project, extract it to a new directory, and reopen."""
        m = _build_project(tmp_path)
        archive = str(tmp_path / f"export{extension}")
        try:
            export_project(m, archive)
        finally:
            m.close()

        # Extract the archive manually (mirrors what import_project does).
        # ProjectManager derives db_path from os.path.basename(project_dir), so
        # the directory must be named after the project ("TestIO") so that
        # ProjectManager("…/TestIO") → looks for "…/TestIO/TestIO.db".
        restore_dir = str(tmp_path / "restored" / "TestIO")
        os.makedirs(restore_dir, exist_ok=True)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(restore_dir)

        # Reopen as a ProjectManager
        restored = ProjectManager(restore_dir)
        return restored

    def test_project_name_survives_round_trip(self, tmp_path):
        m = self._export_and_reopen(tmp_path)
        try:
            assert m.get_project_name() == "TestIO"
        finally:
            m.close()

    def test_concentration_groups_survive_round_trip(self, tmp_path):
        m = self._export_and_reopen(tmp_path)
        try:
            concs = m.get_concentrations()
            ids = {c["id"] for c in concs}
            assert "ctrl" in ids
            assert "s1" in ids
            assert "s2" in ids
        finally:
            m.close()

    def test_plate_layout_survives_round_trip(self, tmp_path):
        m = self._export_and_reopen(tmp_path)
        try:
            layout = m.get_plate_layout(0)
            assert layout.get("A1") == "ctrl"
            assert layout.get("B1") == "s1"
            assert layout.get("C1") == "s2"
        finally:
            m.close()

    def test_well_observations_survive_round_trip(self, tmp_path):
        m = self._export_and_reopen(tmp_path)
        try:
            wd = m.get_well_data(1, 0, "B1")
            assert wd["status"] == STATUS_DEAD_EMBRYO
            assert "Yolk sac oedema" in wd["sublethal_conditions"]
            assert "Lack of heartbeat" in wd["lethal_conditions"]
        finally:
            m.close()

    def test_live_hatched_survives_round_trip(self, tmp_path):
        m = self._export_and_reopen(tmp_path)
        try:
            wd = m.get_well_data(1, 0, "C1")
            assert wd["status"] == STATUS_LIVE_HATCHED
        finally:
            m.close()

    def test_aggregation_survives_round_trip(self, tmp_path):
        """After round-trip, get_results_data() should still return correct counts."""
        m = self._export_and_reopen(tmp_path)
        try:
            results = m.get_results_data(1)
            assert results["ctrl"]["live"] == 1
            assert results["s1"]["dead"] == 1
            assert results["s2"]["live"] == 1  # live hatched = live
        finally:
            m.close()

    def test_zebravet_archive_round_trip(self, tmp_path):
        """Legacy .zebravet extension should import identically."""
        m = self._export_and_reopen(tmp_path, extension=".zebravet")
        try:
            concs = m.get_concentrations()
            assert len(concs) == 3
        finally:
            m.close()
