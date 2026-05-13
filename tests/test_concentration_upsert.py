"""
test_concentration_upsert.py — Verify set_concentrations() ID semantics.

set_concentrations() replaces all rows. The caller controls the IDs, so tests
verify:
  - IDs passed in are preserved in the DB
  - Adding a new group produces the new ID
  - Removing a group removes it from the DB
  - Changing a value does not change the ID
  - concentration_settings (required_embryos, required_plates) is persisted
"""
import pytest
from src.core.project_manager import ProjectManager


INITIAL_DATA = {
    "project_name": "TestConc",
    "num_days": 4,
    "num_plates": 1,
}


@pytest.fixture
def manager(tmp_path):
    m = ProjectManager.create_new(str(tmp_path / "TestConc"), INITIAL_DATA)
    yield m
    m.close()


def _make_concs(*entries):
    """Build a concentration list from (id, type, value) tuples."""
    return [
        {"id": cid, "type": ctype, "value": val,
         "replicates": 1, "wells": 4, "per_plate": False}
        for cid, ctype, val in entries
    ]


class TestSetConcentrations:
    def test_ids_are_stored_as_provided(self, manager):
        concs = _make_concs(("ctrl-01", "Control", 0.0), ("sub-01", "Substrate", 1.0))
        manager.set_concentrations(concs, required_embryos=8, required_plates=1)
        stored = manager.get_concentrations()
        ids = {c["id"] for c in stored}
        assert "ctrl-01" in ids
        assert "sub-01" in ids

    def test_adding_a_new_group_produces_new_id(self, manager):
        concs = _make_concs(("ctrl-01", "Control", 0.0), ("sub-01", "Substrate", 1.0))
        manager.set_concentrations(concs, required_embryos=8, required_plates=1)

        concs_extended = _make_concs(
            ("ctrl-01", "Control", 0.0),
            ("sub-01", "Substrate", 1.0),
            ("sub-02", "Substrate", 2.0),  # new
        )
        manager.set_concentrations(concs_extended, required_embryos=12, required_plates=1)
        stored = manager.get_concentrations()
        ids = {c["id"] for c in stored}
        assert "sub-02" in ids
        assert len(stored) == 3

    def test_removing_a_group_drops_it(self, manager):
        concs = _make_concs(("ctrl-01", "Control", 0.0), ("sub-01", "Substrate", 1.0))
        manager.set_concentrations(concs, required_embryos=8, required_plates=1)

        concs_reduced = _make_concs(("ctrl-01", "Control", 0.0))
        manager.set_concentrations(concs_reduced, required_embryos=4, required_plates=1)
        stored = manager.get_concentrations()
        ids = {c["id"] for c in stored}
        assert "ctrl-01" in ids
        assert "sub-01" not in ids
        assert len(stored) == 1

    def test_changing_value_preserves_id(self, manager):
        concs = _make_concs(("sub-01", "Substrate", 1.0))
        manager.set_concentrations(concs, required_embryos=4, required_plates=1)

        concs_updated = _make_concs(("sub-01", "Substrate", 5.0))  # same id, new value
        manager.set_concentrations(concs_updated, required_embryos=4, required_plates=1)
        stored = manager.get_concentrations()
        assert len(stored) == 1
        assert stored[0]["id"] == "sub-01"
        assert stored[0]["value"] == pytest.approx(5.0)

    def test_required_embryos_and_plates_persisted(self, manager):
        concs = _make_concs(("ctrl-01", "Control", 0.0))
        manager.set_concentrations(concs, required_embryos=42, required_plates=3)
        settings = manager.get_concentration_settings()
        assert settings["required_embryos"] == 42
        assert settings["required_plates"] == 3

    def test_sort_order_preserved(self, manager):
        """Concentrations should be returned in insertion (sort_order) order."""
        concs = _make_concs(
            ("c1", "Control", 0.0),
            ("s1", "Substrate", 1.0),
            ("s2", "Substrate", 2.0),
            ("s3", "Substrate", 4.0),
        )
        manager.set_concentrations(concs, required_embryos=16, required_plates=1)
        stored = manager.get_concentrations()
        assert [c["id"] for c in stored] == ["c1", "s1", "s2", "s3"]

    def test_per_plate_flag_stored(self, manager):
        concs = [
            {"id": "ctrl", "type": "Control", "value": 0.0,
             "replicates": 1, "wells": 4, "per_plate": True},
        ]
        manager.set_concentrations(concs, required_embryos=4, required_plates=1)
        stored = manager.get_concentrations()
        assert stored[0]["per_plate"] is True

    def test_concentration_map_matches_list(self, manager):
        concs = _make_concs(("c1", "Control", 0.0), ("s1", "Substrate", 1.0))
        manager.set_concentrations(concs, required_embryos=8, required_plates=1)
        conc_list = manager.get_concentrations()
        conc_map = manager.get_concentration_map()
        assert set(c["id"] for c in conc_list) == set(conc_map.keys())
