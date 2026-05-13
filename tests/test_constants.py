"""
test_constants.py — Verify that constants.py is the single source of truth
for all ZebraFET domain vocabulary.
"""
import pytest
from src.core.constants import (
    STATUS_LIVE_EMBRYO, STATUS_DEAD_EMBRYO,
    STATUS_LIVE_HATCHED, STATUS_DEAD_HATCHED,
    STATUS_ABSENT,
    LIVE_STATUSES, DEAD_STATUSES, HATCHED_STATUSES, IRREVERSIBLE_STATUSES,
    LETHAL_ENDPOINTS, NON_LETHAL_ENDPOINTS,
    CONCENTRATION_TYPE_MAP, CONCENTRATION_TYPE_REVERSE,
)


class TestStatusStrings:
    def test_status_strings_defined(self):
        assert STATUS_LIVE_EMBRYO == "Live Embryo"
        assert STATUS_DEAD_EMBRYO == "Dead Embryo"
        assert STATUS_LIVE_HATCHED == "Live Hatched"
        assert STATUS_DEAD_HATCHED == "Dead Hatched"
        assert STATUS_ABSENT == "Absent (use majority)"

    def test_live_statuses_frozenset(self):
        assert STATUS_LIVE_EMBRYO in LIVE_STATUSES
        assert STATUS_LIVE_HATCHED in LIVE_STATUSES
        assert STATUS_DEAD_EMBRYO not in LIVE_STATUSES
        assert STATUS_DEAD_HATCHED not in LIVE_STATUSES
        assert STATUS_ABSENT not in LIVE_STATUSES

    def test_dead_statuses_frozenset(self):
        assert STATUS_DEAD_EMBRYO in DEAD_STATUSES
        assert STATUS_DEAD_HATCHED in DEAD_STATUSES
        assert STATUS_LIVE_EMBRYO not in DEAD_STATUSES
        assert STATUS_LIVE_HATCHED not in DEAD_STATUSES

    def test_hatched_statuses_frozenset(self):
        assert STATUS_LIVE_HATCHED in HATCHED_STATUSES
        assert STATUS_DEAD_HATCHED in HATCHED_STATUSES
        assert STATUS_LIVE_EMBRYO not in HATCHED_STATUSES
        assert STATUS_DEAD_EMBRYO not in HATCHED_STATUSES

    def test_irreversible_statuses_frozenset(self):
        assert STATUS_DEAD_EMBRYO in IRREVERSIBLE_STATUSES
        assert STATUS_DEAD_HATCHED in IRREVERSIBLE_STATUSES
        assert STATUS_ABSENT in IRREVERSIBLE_STATUSES
        assert STATUS_LIVE_EMBRYO not in IRREVERSIBLE_STATUSES
        assert STATUS_LIVE_HATCHED not in IRREVERSIBLE_STATUSES

    def test_live_dead_disjoint(self):
        assert LIVE_STATUSES.isdisjoint(DEAD_STATUSES)


class TestEndpointLists:
    def test_lethal_endpoints_count(self):
        assert len(LETHAL_ENDPOINTS) == 4

    def test_lethal_endpoints_content(self):
        expected = {
            "Coagulation of embryo",
            "Lack of somite formation",
            "Non-detachment of the tail",
            "Lack of heartbeat",
        }
        assert set(LETHAL_ENDPOINTS) == expected

    def test_non_lethal_endpoints_count(self):
        assert len(NON_LETHAL_ENDPOINTS) == 9

    def test_non_lethal_endpoints_content(self):
        expected = {
            "Yolk sac oedema",
            "Pericardial oedema",
            "Spinal curvature (scoliosis)",
            "Tail / fin malformation",
            "Head / jaw malformation",
            "Fin malformation or absence",
            "Pigmentation abnormalities",
            "Uninflated swim bladder",
            "Developmental delay",
        }
        assert set(NON_LETHAL_ENDPOINTS) == expected


class TestConcentrationTypeMap:
    def test_map_has_four_entries(self):
        assert len(CONCENTRATION_TYPE_MAP) == 4

    def test_map_keys(self):
        assert "Control" in CONCENTRATION_TYPE_MAP
        assert "Substrate" in CONCENTRATION_TYPE_MAP
        assert "Solvent Control" in CONCENTRATION_TYPE_MAP
        assert "Positive Control" in CONCENTRATION_TYPE_MAP

    def test_reverse_map_is_inverse(self):
        for k, v in CONCENTRATION_TYPE_MAP.items():
            assert CONCENTRATION_TYPE_REVERSE[v] == k
