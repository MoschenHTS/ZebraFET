"""
test_aggregation.py — Guard against BUG 0 (silent aggregation failure).

Inserts known wells with known statuses and verifies that get_results_data()
produces the correct live / dead / total / malformed counts.

This test directly validates Phase 1 of the implementation plan.
"""
import pytest
from src.core.project_manager import ProjectManager
from src.core.constants import (
    STATUS_LIVE_EMBRYO, STATUS_DEAD_EMBRYO,
    STATUS_LIVE_HATCHED, STATUS_DEAD_HATCHED,
    STATUS_ABSENT,
)


INITIAL_DATA = {
    "project_name": "TestAgg",
    "num_days": 4,
    "num_plates": 1,
}

CONC_ID = "sub_1"
CTRL_ID = "ctrl"

CONCENTRATIONS = [
    {"id": CTRL_ID,  "type": "Control",   "value": 0.0, "replicates": 1, "wells": 12, "per_plate": True},
    {"id": CONC_ID,  "type": "Substrate", "value": 1.0, "replicates": 1, "wells": 12, "per_plate": True},
]


@pytest.fixture
def manager(tmp_path):
    m = ProjectManager.create_new(str(tmp_path / "TestAgg"), INITIAL_DATA)
    m.set_concentrations(CONCENTRATIONS, required_embryos=24, required_plates=1)

    # Assign wells: A1-A5 → substrate, B1-B3 → control
    layout = {
        "1": {
            "A1": CONC_ID, "A2": CONC_ID, "A3": CONC_ID,
            "A4": CONC_ID, "A5": CONC_ID,
            "B1": CTRL_ID, "B2": CTRL_ID, "B3": CTRL_ID,
        }
    }
    m.commit_plate_layout(layout)
    yield m
    m.close()


def _save(manager, well_id, status, sublethal=None):
    manager.save_well_data(
        day=1, plate_index=1, well_id=well_id,
        status=status,
        sublethal_conditions=sublethal or [],
        lethal_conditions=[],
        notes="",
    )


class TestAggregationCounts:
    def test_dead_embryo_counted_as_dead(self, manager):
        _save(manager, "A1", STATUS_DEAD_EMBRYO)
        results = manager.get_results_data(1)
        assert results[CONC_ID]["dead"] == 1
        assert results[CONC_ID]["live"] == 0
        assert results[CONC_ID]["total"] == 1

    def test_live_embryo_counted_as_live(self, manager):
        _save(manager, "A1", STATUS_LIVE_EMBRYO)
        results = manager.get_results_data(1)
        assert results[CONC_ID]["live"] == 1
        assert results[CONC_ID]["dead"] == 0

    def test_live_hatched_counted_as_live(self, manager):
        _save(manager, "A1", STATUS_LIVE_HATCHED)
        results = manager.get_results_data(1)
        assert results[CONC_ID]["live"] == 1
        assert results[CONC_ID]["dead"] == 0

    def test_dead_hatched_counted_as_dead(self, manager):
        _save(manager, "A1", STATUS_DEAD_HATCHED)
        results = manager.get_results_data(1)
        assert results[CONC_ID]["dead"] == 1
        assert results[CONC_ID]["live"] == 0

    def test_all_absent_group_unchanged(self, manager):
        """A group with no non-absent wells has no majority, so an absent well
        stays absent: counted toward total only, never live or dead."""
        _save(manager, "A1", STATUS_ABSENT)
        results = manager.get_results_data(1)
        assert results[CONC_ID]["total"] == 1
        assert results[CONC_ID]["live"] == 0
        assert results[CONC_ID]["dead"] == 0

    def test_absent_imputed_to_group_majority(self, manager):
        """An absent well is imputed to the majority status of its group and
        counted under that status (3 Dead + 1 Live + 1 Absent -> dead=4)."""
        _save(manager, "A1", STATUS_DEAD_EMBRYO)
        _save(manager, "A2", STATUS_DEAD_EMBRYO)
        _save(manager, "A3", STATUS_DEAD_EMBRYO)
        _save(manager, "A4", STATUS_LIVE_EMBRYO)
        _save(manager, "A5", STATUS_ABSENT)
        results = manager.get_results_data(1)
        assert results[CONC_ID]["dead"] == 4
        assert results[CONC_ID]["live"] == 1
        assert results[CONC_ID]["total"] == 5

    def test_absent_majority_tie_favours_live(self, manager):
        """On a majority tie the conservative priority order wins (Live Embryo
        first), so 2 Dead + 2 Live + 1 Absent -> absent becomes live."""
        _save(manager, "A1", STATUS_DEAD_EMBRYO)
        _save(manager, "A2", STATUS_DEAD_EMBRYO)
        _save(manager, "A3", STATUS_LIVE_EMBRYO)
        _save(manager, "A4", STATUS_LIVE_EMBRYO)
        _save(manager, "A5", STATUS_ABSENT)
        results = manager.get_results_data(1)
        assert results[CONC_ID]["dead"] == 2
        assert results[CONC_ID]["live"] == 3
        assert results[CONC_ID]["total"] == 5

    def test_known_mix_3_dead_5_live_2_hatched(self, manager):
        """
        Substrate: 3 Dead Embryo (A1-A3) + 2 Live Embryo (A4, A5) = dead=3, live=2, total=5.
        Control:   2 Live Embryo (B1, B2) + 1 Live Hatched (B3)   = dead=0, live=3, total=3.
        A4 is saved 3 times to verify idempotent upsert behaviour.
        This is the canonical BUG 0 regression guard.
        """
        _save(manager, "A1", STATUS_DEAD_EMBRYO)
        _save(manager, "A2", STATUS_DEAD_EMBRYO)
        _save(manager, "A3", STATUS_DEAD_EMBRYO)
        _save(manager, "A4", STATUS_LIVE_EMBRYO)
        _save(manager, "A4", STATUS_LIVE_EMBRYO)  # re-save to verify idempotent
        _save(manager, "A4", STATUS_LIVE_EMBRYO)
        _save(manager, "A5", STATUS_LIVE_EMBRYO)

        # Use control wells for the rest
        _save(manager, "B1", STATUS_LIVE_EMBRYO)
        _save(manager, "B2", STATUS_LIVE_EMBRYO)
        _save(manager, "B3", STATUS_LIVE_HATCHED)

        results = manager.get_results_data(1)

        # Substrate group: A1-A3 dead, A4-A5 alive
        assert results[CONC_ID]["dead"] == 3
        assert results[CONC_ID]["live"] == 2
        assert results[CONC_ID]["total"] == 5

        # Control group: B1-B2 live embryo, B3 live hatched
        assert results[CTRL_ID]["dead"] == 0
        assert results[CTRL_ID]["live"] == 3
        assert results[CTRL_ID]["total"] == 3

    def test_sublethal_increments_malformed(self, manager):
        """Wells with sublethal conditions should increment the malformed count."""
        _save(manager, "A1", STATUS_LIVE_EMBRYO, sublethal=["Yolk sac oedema"])
        results = manager.get_results_data(1)
        assert results[CONC_ID]["malformed"] == 1

    def test_no_sublethal_no_malformed(self, manager):
        _save(manager, "A1", STATUS_LIVE_EMBRYO, sublethal=[])
        results = manager.get_results_data(1)
        assert results[CONC_ID]["malformed"] == 0

    def test_dead_with_sublethal_counts_both(self, manager):
        """A dead well with a sublethal condition should increment both dead and malformed."""
        _save(manager, "A1", STATUS_DEAD_EMBRYO, sublethal=["Pericardial oedema"])
        results = manager.get_results_data(1)
        assert results[CONC_ID]["dead"] == 1
        assert results[CONC_ID]["malformed"] == 1

    def test_well_not_in_layout_ignored(self, manager):
        """Observations for wells not in the plate layout are silently ignored."""
        manager.save_well_data(
            day=1, plate_index=1, well_id="Z99",  # not in layout
            status=STATUS_DEAD_EMBRYO,
            sublethal_conditions=[], lethal_conditions=[], notes="",
        )
        results = manager.get_results_data(1)
        total_all = sum(v["total"] for v in results.values())
        assert total_all == 0  # Z99 should not appear in any bucket

    def test_empty_day_returns_zeros(self, manager):
        """If no observations for a day, all counts should be zero."""
        results = manager.get_results_data(1)
        for cid, data in results.items():
            assert data["total"] == 0
            assert data["live"] == 0
            assert data["dead"] == 0
