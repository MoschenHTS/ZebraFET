"""
test_status_propagation.py — Verify that propagate_day_data carries statuses
correctly and that IRREVERSIBLE_STATUSES prevents overwriting terminal states.
"""
import pytest
from src.core.project_manager import ProjectManager
from src.core.constants import (
    STATUS_LIVE_EMBRYO, STATUS_DEAD_EMBRYO,
    STATUS_LIVE_HATCHED, STATUS_DEAD_HATCHED,
    STATUS_ABSENT, IRREVERSIBLE_STATUSES,
)


INITIAL_DATA = {
    "project_name": "TestProp",
    "num_days": 4,
    "num_plates": 1,
}

CONC_ID = "ctrl"
CONCENTRATIONS = [
    {"id": CONC_ID, "type": "Control", "value": 0.0,
     "replicates": 1, "wells": 12, "per_plate": True},
]


@pytest.fixture
def manager(tmp_path):
    m = ProjectManager.create_new(str(tmp_path / "TestProp"), INITIAL_DATA)
    m.set_concentrations(CONCENTRATIONS, required_embryos=12, required_plates=1)
    layout = {"1": {"A1": CONC_ID, "A2": CONC_ID, "A3": CONC_ID}}
    m.commit_plate_layout(layout)
    yield m
    m.close()


def save(manager, day, well_id, status, sublethal=None):
    manager.save_well_data(
        day=day, plate_index=1, well_id=well_id,
        status=status,
        sublethal_conditions=sublethal or [],
        lethal_conditions=[],
        notes="",
    )


class TestPropagateDayData:
    def test_propagates_live_status_to_next_day(self, manager):
        save(manager, 1, "A1", STATUS_LIVE_EMBRYO)
        manager.propagate_day_data(from_day=1, to_day=2)
        wd = manager.get_well_data(2, 1, "A1")
        assert wd["status"] == STATUS_LIVE_EMBRYO

    def test_propagates_dead_status_to_next_day(self, manager):
        save(manager, 1, "A1", STATUS_DEAD_EMBRYO)
        manager.propagate_day_data(from_day=1, to_day=2)
        wd = manager.get_well_data(2, 1, "A1")
        assert wd["status"] == STATUS_DEAD_EMBRYO

    def test_propagates_hatched_status(self, manager):
        save(manager, 1, "A1", STATUS_LIVE_HATCHED)
        manager.propagate_day_data(from_day=1, to_day=2)
        wd = manager.get_well_data(2, 1, "A1")
        assert wd["status"] == STATUS_LIVE_HATCHED

    def test_propagate_does_not_overwrite_existing_day(self, manager):
        """propagate_day_data is a no-op when the target day already has observations."""
        save(manager, 1, "A1", STATUS_DEAD_EMBRYO)
        save(manager, 2, "A1", STATUS_LIVE_EMBRYO)  # already entered
        manager.propagate_day_data(from_day=1, to_day=2)
        wd = manager.get_well_data(2, 1, "A1")
        assert wd["status"] == STATUS_LIVE_EMBRYO  # unchanged

    def test_propagate_multiple_wells(self, manager):
        save(manager, 1, "A1", STATUS_DEAD_EMBRYO)
        save(manager, 1, "A2", STATUS_LIVE_HATCHED)
        save(manager, 1, "A3", STATUS_LIVE_EMBRYO)
        manager.propagate_day_data(from_day=1, to_day=2)
        assert manager.get_well_data(2, 1, "A1")["status"] == STATUS_DEAD_EMBRYO
        assert manager.get_well_data(2, 1, "A2")["status"] == STATUS_LIVE_HATCHED
        assert manager.get_well_data(2, 1, "A3")["status"] == STATUS_LIVE_EMBRYO

    def test_propagate_clears_notes(self, manager):
        """Propagation should carry status but not copy notes."""
        manager.save_well_data(
            day=1, plate_index=1, well_id="A1",
            status=STATUS_LIVE_EMBRYO,
            sublethal_conditions=[], lethal_conditions=[],
            notes="Some observation note",
        )
        manager.propagate_day_data(from_day=1, to_day=2)
        wd = manager.get_well_data(2, 1, "A1")
        assert wd.get("notes", "") == ""


class TestIrreversibleStatuses:
    def test_dead_is_irreversible(self):
        assert STATUS_DEAD_EMBRYO in IRREVERSIBLE_STATUSES

    def test_dead_hatched_is_irreversible(self):
        assert STATUS_DEAD_HATCHED in IRREVERSIBLE_STATUSES

    def test_absent_is_irreversible(self):
        assert STATUS_ABSENT in IRREVERSIBLE_STATUSES

    def test_live_embryo_is_not_irreversible(self):
        assert STATUS_LIVE_EMBRYO not in IRREVERSIBLE_STATUSES

    def test_live_hatched_is_not_irreversible(self):
        assert STATUS_LIVE_HATCHED not in IRREVERSIBLE_STATUSES

    def test_save_overwrites_with_any_status(self, manager):
        """
        ProjectManager.save_well_data itself does not enforce irreversibility —
        that guard lives in WellEditorWidget._propagate_states.
        Verify that a direct DB save CAN overwrite (the widget layer enforces the rule).
        """
        save(manager, 1, "A1", STATUS_DEAD_EMBRYO)
        save(manager, 1, "A1", STATUS_LIVE_EMBRYO)
        wd = manager.get_well_data(1, 1, "A1")
        assert wd["status"] == STATUS_LIVE_EMBRYO
