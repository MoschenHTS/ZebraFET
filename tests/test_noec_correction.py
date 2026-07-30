"""
test_noec_correction.py — Multiplicity control for the NOEC/LOEC comparisons.

Holm controls the same family-wise error rate as Bonferroni but rejects at least
as often, so an effect Bonferroni misses for want of power can still be reported.
Which correction produced a given LOEC therefore has to travel with the result.
"""
import numpy as np
import pandas as pd
import pytest

from src.core.biostatistics import (
    NOEC_CORRECTION_BONFERRONI,
    NOEC_CORRECTION_HOLM,
    calculate_noec_loec_with_correction,
    holm_bonferroni,
)


def _summary(rows):
    """rows: (conc_id, conc_type, conc_value, dead, n_scored)"""
    return pd.DataFrame([
        {
            "conc_id": cid, "conc_type": ctype, "conc_value": value,
            "dead": dead, "total": n, "n_scored": n, "live": n - dead,
        }
        for cid, ctype, value, dead, n in rows
    ])


class TestHolmAdjustment:
    def test_empty_input(self):
        assert len(holm_bonferroni([])) == 0

    def test_smallest_p_is_multiplied_by_k(self):
        adjusted = holm_bonferroni([0.01, 0.04, 0.05])
        assert adjusted[0] == pytest.approx(0.03)

    def test_largest_p_is_multiplied_by_one(self):
        adjusted = holm_bonferroni([0.01, 0.04, 0.5])
        assert adjusted[2] == pytest.approx(0.5)

    def test_is_monotone_non_decreasing_in_rank(self):
        raw = [0.001, 0.02, 0.03, 0.9]
        adjusted = holm_bonferroni(raw)
        ordered = adjusted[np.argsort(raw)]
        assert list(ordered) == sorted(ordered)

    def test_never_exceeds_one(self):
        assert np.all(holm_bonferroni([0.5, 0.6, 0.7, 0.9]) <= 1.0)

    def test_order_is_preserved(self):
        raw = [0.5, 0.001, 0.2]
        adjusted = holm_bonferroni(raw)
        assert adjusted[1] < adjusted[2] < adjusted[0]

    def test_dominates_bonferroni(self):
        """Holm's adjusted p-values are never larger than Bonferroni's."""
        raw = np.array([0.004, 0.01, 0.03, 0.2])
        assert np.all(holm_bonferroni(raw) <= np.minimum(1.0, raw * len(raw)) + 1e-12)


class TestNoecLoec:
    #: Control is clean; mortality climbs with dose.
    ROWS = [
        ("ctrl", "Control", 0.0, 0, 40),
        ("s1", "Substrate", 1.0, 1, 40),
        ("s2", "Substrate", 2.0, 8, 40),
        ("s3", "Substrate", 4.0, 30, 40),
        ("s4", "Substrate", 8.0, 40, 40),
    ]

    def test_reports_the_correction_used(self):
        result = calculate_noec_loec_with_correction(
            _summary(self.ROWS), correction=NOEC_CORRECTION_HOLM
        )
        assert result["correction"] == NOEC_CORRECTION_HOLM
        assert result["correction_label"] == "Holm-Bonferroni"

    def test_defaults_to_holm(self):
        result = calculate_noec_loec_with_correction(_summary(self.ROWS))
        assert result["correction"] == NOEC_CORRECTION_HOLM

    def test_unknown_correction_falls_back_to_holm(self):
        result = calculate_noec_loec_with_correction(
            _summary(self.ROWS), correction="nonsense"
        )
        assert result["correction"] == NOEC_CORRECTION_HOLM

    def test_per_dose_tests_are_returned(self):
        result = calculate_noec_loec_with_correction(_summary(self.ROWS))
        assert [t["conc_value"] for t in result["tests"]] == [1.0, 2.0, 4.0, 8.0]
        assert all("p_adj" in t for t in result["tests"])

    def test_loec_is_the_lowest_significant_dose(self):
        result = calculate_noec_loec_with_correction(_summary(self.ROWS))
        significant = [t["conc_value"] for t in result["tests"] if t["p_adj"] < 0.05]
        assert result["loec_numeric"] == min(significant)

    def test_noec_sits_below_the_loec(self):
        result = calculate_noec_loec_with_correction(_summary(self.ROWS))
        assert result["noec_numeric"] < result["loec_numeric"]

    @pytest.mark.parametrize(
        "correction", [NOEC_CORRECTION_HOLM, NOEC_CORRECTION_BONFERRONI]
    )
    def test_both_corrections_produce_an_endpoint(self, correction):
        result = calculate_noec_loec_with_correction(
            _summary(self.ROWS), correction=correction
        )
        assert result["loec_numeric"] is not None

    def test_holm_never_reports_a_higher_loec_than_bonferroni(self):
        """Holm rejects at least as often, so its LOEC cannot be the weaker one."""
        holm = calculate_noec_loec_with_correction(
            _summary(self.ROWS), correction=NOEC_CORRECTION_HOLM
        )
        bonf = calculate_noec_loec_with_correction(
            _summary(self.ROWS), correction=NOEC_CORRECTION_BONFERRONI
        )
        assert holm["loec_numeric"] <= bonf["loec_numeric"]

    def test_no_effect_leaves_the_loec_undetected(self):
        rows = [
            ("ctrl", "Control", 0.0, 1, 40),
            ("s1", "Substrate", 1.0, 1, 40),
            ("s2", "Substrate", 2.0, 2, 40),
        ]
        result = calculate_noec_loec_with_correction(_summary(rows))
        assert result["loec_numeric"] is None
        assert result["noec_numeric"] == 2.0

    def test_missing_control_is_reported(self):
        rows = [("s1", "Substrate", 1.0, 5, 40)]
        result = calculate_noec_loec_with_correction(_summary(rows))
        assert result["noec_numeric"] is None
        assert "Control group not found" in result["noec"]
