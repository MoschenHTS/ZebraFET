"""
test_oecd_validity.py — Verify test condition persistence (Phase 2 DB schema fix)
and that the data model correctly tracks control mortality for OECD TG 236 validity.

Note: _check_test_validity() is a widget method (requires Qt). This module tests
the underlying data layer — that acceptable_mortality persists correctly and that
get_results_data() provides the right numbers for a validity check to act on.
"""
import pytest
from src.core.project_manager import ProjectManager
from src.core.constants import STATUS_DEAD_EMBRYO, STATUS_LIVE_EMBRYO


INITIAL_DATA = {
    "project_name": "TestOECD",
    "num_days": 4,
    "num_plates": 1,
}

CTRL_ID = "ctrl"
CONC_ID = "sub_1"

CONCENTRATIONS = [
    {"id": CTRL_ID, "type": "Control",   "value": 0.0, "replicates": 1, "wells": 10, "per_plate": True},
    {"id": CONC_ID, "type": "Substrate", "value": 1.0, "replicates": 1, "wells": 10, "per_plate": True},
]


@pytest.fixture
def manager(tmp_path):
    m = ProjectManager.create_new(str(tmp_path / "TestOECD"), INITIAL_DATA)
    m.set_concentrations(CONCENTRATIONS, required_embryos=20, required_plates=1)
    layout = {
        "1": {
            f"A{i+1}": CTRL_ID for i in range(5)
        } | {
            f"B{i+1}": CONC_ID for i in range(5)
        }
    }
    m.commit_plate_layout(layout)
    yield m
    m.close()


class TestAcceptableMortalityPersistence:
    def test_default_acceptable_mortality_is_10(self, manager):
        conditions = manager.get_test_conditions()
        assert conditions.get("acceptable_mortality", 10.0) == pytest.approx(10.0)

    def test_update_and_retrieve_acceptable_mortality(self, manager):
        manager.update_test_conditions(acceptable_mortality=5.0)
        conditions = manager.get_test_conditions()
        assert conditions["acceptable_mortality"] == pytest.approx(5.0)

    def test_update_acceptable_mortality_persists_across_reopen(self, tmp_path):
        """Verify the value survives closing and reopening the DB."""
        proj_dir = str(tmp_path / "TestPersist")
        m1 = ProjectManager.create_new(proj_dir, {"project_name": "TestPersist"})
        m1.update_test_conditions(acceptable_mortality=7.5)
        m1.close()

        m2 = ProjectManager(proj_dir)
        conditions = m2.get_test_conditions()
        m2.close()
        assert conditions["acceptable_mortality"] == pytest.approx(7.5)

    def test_temperature_and_dissolved_oxygen_persist(self, manager):
        """Verify the Phase 2 schema columns exist and can be written."""
        manager.update_test_conditions(temperature="26°C", dissolved_oxygen="8.2 mg/L")
        conditions = manager.get_test_conditions()
        assert conditions["temperature"] == "26°C"
        assert conditions["dissolved_oxygen"] == "8.2 mg/L"

    def test_unrecognized_fields_are_ignored(self, manager):
        """Fields not in the allowed set should be silently ignored."""
        before = manager.get_test_conditions()
        manager.update_test_conditions(unknown_field="value")
        after = manager.get_test_conditions()
        assert before == after


class TestControlMortalityDetection:
    def _save(self, manager, well_id, status):
        manager.save_well_data(
            day=1, plate_index=1, well_id=well_id,
            status=status,
            sublethal_conditions=[], lethal_conditions=[], notes="",
        )

    def test_control_mortality_above_threshold_detectable(self, manager):
        """
        2 of 5 control wells dead = 40% mortality > 10% threshold.
        get_results_data returns the raw numbers; caller computes mortality%.
        """
        self._save(manager, "A1", STATUS_DEAD_EMBRYO)
        self._save(manager, "A2", STATUS_DEAD_EMBRYO)
        self._save(manager, "A3", STATUS_LIVE_EMBRYO)
        self._save(manager, "A4", STATUS_LIVE_EMBRYO)
        self._save(manager, "A5", STATUS_LIVE_EMBRYO)

        results = manager.get_results_data(1)
        ctrl = results[CTRL_ID]
        mortality_pct = (ctrl["dead"] / ctrl["total"]) * 100
        threshold = manager.get_test_conditions().get("acceptable_mortality", 10.0)

        assert ctrl["dead"] == 2
        assert ctrl["total"] == 5
        assert mortality_pct > threshold  # test would be INVALID

    def test_control_mortality_below_threshold_is_valid(self, manager):
        """0 of 5 control wells dead = 0% mortality < 10% threshold → valid."""
        for well in ["A1", "A2", "A3", "A4", "A5"]:
            self._save(manager, well, STATUS_LIVE_EMBRYO)

        results = manager.get_results_data(1)
        ctrl = results[CTRL_ID]
        mortality_pct = (ctrl["dead"] / ctrl["total"]) * 100 if ctrl["total"] else 0
        threshold = manager.get_test_conditions().get("acceptable_mortality", 10.0)

        assert mortality_pct <= threshold  # test would be VALID

    def test_custom_threshold_at_5_percent(self, manager):
        """With custom threshold of 5%, even 1/20 dead (5%) should be at the boundary."""
        manager.update_test_conditions(acceptable_mortality=5.0)
        # 1 of 5 = 20% mortality → exceeds 5%
        self._save(manager, "A1", STATUS_DEAD_EMBRYO)
        for well in ["A2", "A3", "A4", "A5"]:
            self._save(manager, well, STATUS_LIVE_EMBRYO)

        results = manager.get_results_data(1)
        ctrl = results[CTRL_ID]
        mortality_pct = (ctrl["dead"] / ctrl["total"]) * 100
        threshold = manager.get_test_conditions()["acceptable_mortality"]

        assert mortality_pct > threshold  # 20% > 5%
