"""
test_biostatistics.py — Verify the standalone biostatistics module.

Tests LC50 4PL curve fitting, Bonferroni correction, and NOEC/LOEC detection
using synthetic data with known expected outcomes.
"""
import numpy as np
import pandas as pd
import pytest

from src.core.biostatistics import (
    logistic_function,
    calculate_lc50_robust,
    calculate_noec_loec_with_correction,
)


class TestLogisticFunction:
    def test_returns_ec50_at_midpoint(self):
        """At x == ec50, the 4PL model returns min + (max-min)/2."""
        result = logistic_function(np.array([5.0]), 0, 100, -1, 5.0)
        assert abs(result[0] - 50.0) < 0.01

    def test_returns_nan_for_nonpositive_ec50(self):
        result = logistic_function(np.array([1.0, 2.0]), 0, 100, -1, 0)
        assert np.all(np.isnan(result))

    def test_min_plateau_at_low_x(self):
        """For a negative slope (dose-response), very low x → min_val (0%)."""
        result = logistic_function(np.array([0.001]), 0, 100, -1, 5.0)
        assert result[0] < 10  # close to 0 (min_val)

    def test_max_plateau_at_high_x(self):
        """For a negative slope (dose-response), very high x → max_val (100%)."""
        result = logistic_function(np.array([1000.0]), 0, 100, -1, 5.0)
        assert result[0] > 90  # close to 100 (max_val)


class TestCalculateLc50Robust:
    def _make_plot_data(self, concs, mortalities):
        return [
            {"type": "Substrate", "x": x, "y": y}
            for x, y in zip(concs, mortalities)
        ]

    def test_lc50_within_range(self):
        """Classic sigmoid: LC50 should fall near the midpoint concentration."""
        data = self._make_plot_data(
            concs=[1.0, 2.0, 4.0, 8.0, 16.0],
            mortalities=[0.0, 10.0, 50.0, 90.0, 100.0],
        )
        result = calculate_lc50_robust(data)
        assert "lc50" in result
        assert "slope" in result
        # The LC50 should be a formatted string with a numeric value
        assert "95% CI" in result["lc50"], f"Unexpected: {result['lc50']}"
        # Extract the LC50 value and verify it's roughly 4.0
        lc50_str = result["lc50"].split(" ")[0]
        lc50_val = float(lc50_str)
        assert 2.0 <= lc50_val <= 8.0, f"LC50 {lc50_val} not in expected range"

    def test_not_enough_data_points(self):
        data = self._make_plot_data([1.0, 2.0, 3.0], [0.0, 50.0, 100.0])
        result = calculate_lc50_robust(data)
        assert "not enough data" in result["lc50"].lower()

    def test_zero_concentration_excluded(self):
        """x=0 entries should be filtered out (control wells)."""
        data = [
            {"type": "Substrate", "x": 0, "y": 0.0},
            {"type": "Substrate", "x": 1.0, "y": 0.0},
            {"type": "Substrate", "x": 2.0, "y": 25.0},
            {"type": "Substrate", "x": 4.0, "y": 50.0},
            {"type": "Substrate", "x": 8.0, "y": 90.0},
        ]
        result = calculate_lc50_robust(data)
        # Only 4 valid points after x=0 filter; should attempt fitting
        assert "lc50" in result

    def test_no_mortality_returns_message(self):
        data = self._make_plot_data([1.0, 2.0, 4.0, 8.0], [0.0, 0.0, 0.0, 0.0])
        result = calculate_lc50_robust(data)
        assert "no mortality" in result["lc50"].lower()

    def test_full_mortality_returns_message(self):
        data = self._make_plot_data([1.0, 2.0, 4.0, 8.0], [100.0, 100.0, 100.0, 100.0])
        result = calculate_lc50_robust(data)
        assert "100%" in result["lc50"]

    def test_non_substrate_entries_ignored(self):
        data = [
            {"type": "Control", "x": 0, "y": 5.0},
            {"type": "Substrate", "x": 1.0, "y": 0.0},
            {"type": "Substrate", "x": 2.0, "y": 25.0},
            {"type": "Substrate", "x": 4.0, "y": 50.0},
            {"type": "Substrate", "x": 8.0, "y": 90.0},
            {"type": "Substrate", "x": 16.0, "y": 100.0},
        ]
        result = calculate_lc50_robust(data)
        assert "95% CI" in result["lc50"]

    def test_r_squared_present_on_success(self):
        """Successful fit should include an R-squared value close to 1.0."""
        data = self._make_plot_data(
            concs=[1.0, 2.0, 4.0, 8.0, 16.0],
            mortalities=[0.0, 10.0, 50.0, 90.0, 100.0],
        )
        result = calculate_lc50_robust(data)
        assert "r_squared" in result
        r2_val = float(result["r_squared"].split()[0])
        assert 0.8 <= r2_val <= 1.0, f"R\u00b2 {r2_val} not in expected range"

    def test_r_squared_default_on_insufficient_data(self):
        """When there aren't enough data points, r_squared should be 'Not Calculated'."""
        data = self._make_plot_data([1.0, 2.0, 3.0], [0.0, 50.0, 100.0])
        result = calculate_lc50_robust(data)
        assert result["r_squared"] == "Not Calculated"

    def test_r_squared_is_always_set_when_fit_converges(self):
        """When curve_fit converges (even poorly), r_squared should be a numeric string."""
        data = self._make_plot_data(
            concs=[1.0, 2.0, 4.0, 8.0, 16.0],
            mortalities=[50.0, 0.0, 100.0, 0.0, 50.0],
        )
        result = calculate_lc50_robust(data)
        # Either "Not Calculated" (if RuntimeError) or a float string (if converged)
        r2 = result["r_squared"]
        if r2 != "Not Calculated":
            parsed = float(r2.split()[0])
            assert 0.0 <= parsed <= 1.0, f"r_squared out of [0, 1] range: {parsed}"

    def test_fitted_params_returned_on_success(self):
        """Successful fit should return raw parameters for plot reuse."""
        data = self._make_plot_data(
            concs=[1.0, 2.0, 4.0, 8.0, 16.0],
            mortalities=[0.0, 10.0, 50.0, 90.0, 100.0],
        )
        result = calculate_lc50_robust(data)
        assert "_fitted_params" in result
        assert len(result["_fitted_params"]) == 4


class TestCalculateNoecLoecWithCorrection:
    def _make_summary_df(self, control_dead, control_total, treatments):
        """
        treatments: list of (conc_value, dead, total) tuples for Substrate groups.
        """
        rows = [
            {
                "conc_id": "ctrl",
                "conc_type": "Control",
                "conc_value": 0.0,
                "total": control_total,
                "dead": control_dead,
            }
        ]
        for i, (conc_val, dead, total) in enumerate(treatments):
            rows.append({
                "conc_id": f"t{i}",
                "conc_type": "Substrate",
                "conc_value": conc_val,
                "total": total,
                "dead": dead,
            })
        return pd.DataFrame(rows)

    def test_bonferroni_alpha_five_groups(self):
        """Five treatment groups → alpha_adjusted = 0.05 / 5 = 0.01."""
        treatments = [(i, 0, 10) for i in range(1, 6)]
        df = self._make_summary_df(0, 10, treatments)
        result = calculate_noec_loec_with_correction(df)
        assert abs(result["alpha_adjusted"] - 0.01) < 1e-10

    def test_bonferroni_alpha_one_group(self):
        """Single treatment group → alpha_adjusted = 0.05 / 1 = 0.05."""
        df = self._make_summary_df(0, 10, [(1.0, 0, 10)])
        result = calculate_noec_loec_with_correction(df)
        assert abs(result["alpha_adjusted"] - 0.05) < 1e-10

    def test_loec_detected_at_high_mortality(self):
        """
        Control: 0/10 dead. Group at 4.0: 9/10 dead.
        Fisher's exact should yield p << 0.05 → LOEC = 4.0.
        """
        treatments = [
            (1.0, 0, 10),
            (2.0, 1, 10),
            (4.0, 9, 10),
        ]
        df = self._make_summary_df(0, 10, treatments)
        result = calculate_noec_loec_with_correction(df)
        assert result["loec"] != "Not detected"
        loec_val = float(result["loec"])
        assert loec_val == pytest.approx(4.0, abs=0.01)

    def test_noec_is_concentration_before_loec(self):
        """NOEC should be the concentration just below the LOEC."""
        treatments = [
            (1.0, 0, 10),
            (2.0, 1, 10),
            (4.0, 9, 10),
        ]
        df = self._make_summary_df(0, 10, treatments)
        result = calculate_noec_loec_with_correction(df)
        noec_val = float(result["noec"])
        assert noec_val == pytest.approx(2.0, abs=0.01)

    def test_no_effect_detected_returns_not_detected(self):
        """When no concentration is significantly different, LOEC = Not detected."""
        treatments = [(i, 0, 10) for i in range(1, 6)]
        df = self._make_summary_df(0, 10, treatments)
        result = calculate_noec_loec_with_correction(df)
        assert result["loec"] == "Not detected"

    def test_no_control_group_returns_error(self):
        rows = [
            {"conc_id": "t1", "conc_type": "Substrate", "conc_value": 1.0, "total": 10, "dead": 5},
        ]
        df = pd.DataFrame(rows)
        result = calculate_noec_loec_with_correction(df)
        assert "not found" in result["noec"].lower()

    def test_effect_at_lowest_concentration(self):
        """If LOEC is the lowest concentration, NOEC = 'Effect at lowest concentration'."""
        treatments = [(1.0, 10, 10), (2.0, 10, 10), (4.0, 10, 10)]
        df = self._make_summary_df(0, 10, treatments)
        result = calculate_noec_loec_with_correction(df)
        assert result["noec"] == "Effect at lowest concentration"

    def test_solvent_control_used_as_control(self):
        """Solvent Control rows should be combined with Control for the baseline."""
        rows = [
            {"conc_id": "sc", "conc_type": "Solvent Control", "conc_value": 0.0,
             "total": 10, "dead": 0},
            {"conc_id": "t1", "conc_type": "Substrate", "conc_value": 4.0,
             "total": 10, "dead": 9},
        ]
        df = pd.DataFrame(rows)
        result = calculate_noec_loec_with_correction(df)
        assert result["loec"] != "Not detected"

    def test_no_substrate_groups_returns_error(self):
        """When no Substrate rows exist, should return 'No treatment groups found'."""
        rows = [{"conc_id": "ctrl", "conc_type": "Control",
                 "conc_value": 0.0, "total": 10, "dead": 0}]
        result = calculate_noec_loec_with_correction(pd.DataFrame(rows))
        assert "no treatment groups" in result["noec"].lower()

    def test_zero_control_total_returns_error(self):
        """When control group has zero total embryos, should return descriptive error."""
        rows = [
            {"conc_id": "ctrl", "conc_type": "Control",   "conc_value": 0.0, "total": 0, "dead": 0},
            {"conc_id": "s1",   "conc_type": "Substrate", "conc_value": 1.0, "total": 5, "dead": 1},
        ]
        result = calculate_noec_loec_with_correction(pd.DataFrame(rows))
        assert "zero total embryos" in result["noec"].lower()
