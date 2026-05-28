"""
test_report_snapshot.py — Verify that get_report_snapshot() assembles the complete
data bundle that ReportGenerator needs, with no further DB calls required at render time.
"""
import pytest

from src.core.project_manager import ProjectManager
from src.core.constants import PLATE_FORMATS


INITIAL_DATA = {
    "project_name": "SnapshotTest",
    "main_researcher": "Researcher",
    "substance": "Substance X",
    "concentration_unit": "mg/L",
    "start_date": "2025-01-01",
    "num_days": 4,
    "num_plates": 1,
    "plate_format": "96-well",
}

CONCENTRATIONS = [
    {"id": "ctrl", "type": "Control",   "value": 0.0, "replicates": 1, "wells": 4, "per_plate": True, "color": "#000", "sort_order": 0},
    {"id": "s1",   "type": "Substrate", "value": 1.0, "replicates": 1, "wells": 4, "per_plate": True, "color": "#000", "sort_order": 1},
    {"id": "s2",   "type": "Substrate", "value": 2.0, "replicates": 1, "wells": 4, "per_plate": True, "color": "#000", "sort_order": 2},
]

LAYOUT = {"0": {"A1": "ctrl", "A2": "ctrl", "B1": "s1", "B2": "s1", "C1": "s2", "C2": "s2"}}

REQUIRED_KEYS = {
    "project_name", "substance", "main_researcher", "concentration_unit",
    "start_date", "num_days", "num_plates", "plate_format",
    "substance_details", "test_conditions", "test_organisms", "methodology",
    "concentration_settings", "plate_layout", "well_data",
    "concentration_map", "plate_dimensions", "photos_with_metadata",
}


@pytest.fixture
def manager(tmp_path):
    proj_dir = str(tmp_path / "SnapshotTest")
    mgr = ProjectManager.create_new(proj_dir, INITIAL_DATA)
    mgr.set_concentrations(CONCENTRATIONS, required_embryos=20, required_plates=1)
    mgr.commit_plate_layout(LAYOUT)
    yield mgr
    mgr.close()


class TestGetReportSnapshot:
    def test_snapshot_contains_all_required_keys(self, manager):
        snap = manager.get_report_snapshot()
        missing = REQUIRED_KEYS - snap.keys()
        assert not missing, f"Missing keys: {missing}"

    def test_concentration_map_is_dict_keyed_by_id(self, manager):
        snap = manager.get_report_snapshot()
        cmap = snap["concentration_map"]
        assert isinstance(cmap, dict)
        for cid in ["ctrl", "s1", "s2"]:
            assert cid in cmap

    def test_concentration_map_values_match_concentration_list(self, manager):
        snap = manager.get_report_snapshot()
        cmap = snap["concentration_map"]
        for c in snap["concentration_settings"]["concentrations"]:
            assert c["id"] in cmap
            assert cmap[c["id"]]["value"] == c["value"]

    def test_plate_dimensions_match_format(self, manager):
        snap = manager.get_report_snapshot()
        assert tuple(snap["plate_dimensions"]) == PLATE_FORMATS["96-well"]

    def test_plate_layout_matches_committed_layout(self, manager):
        snap = manager.get_report_snapshot()
        assert snap["plate_layout"] == LAYOUT

    def test_photos_with_metadata_is_list(self, manager):
        snap = manager.get_report_snapshot()
        assert isinstance(snap["photos_with_metadata"], list)

    def test_photos_empty_when_none_added(self, manager):
        snap = manager.get_report_snapshot()
        assert snap["photos_with_metadata"] == []

    def test_project_name_in_snapshot(self, manager):
        snap = manager.get_report_snapshot()
        assert snap["project_name"] == "SnapshotTest"

    def test_concentration_settings_has_concentrations_list(self, manager):
        snap = manager.get_report_snapshot()
        concs = snap["concentration_settings"]["concentrations"]
        assert isinstance(concs, list)
        assert len(concs) == 3

    def test_snapshot_is_independent_of_further_db_writes(self, manager):
        snap = manager.get_report_snapshot()
        manager.update_project_info(substance="Changed Substance")
        assert snap["substance"] == "Substance X"
