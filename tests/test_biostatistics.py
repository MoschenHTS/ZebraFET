"""
test_biostatistics.py — Verify the standalone biostatistics module.

Tests LC50 4PL curve fitting, Bonferroni correction, and NOEC/LOEC detection
using synthetic data with known expected outcomes.
"""
import math
import os

import numpy as np
import pandas as pd
import pytest
from scipy.optimize import OptimizeWarning

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from collections import Counter

from src.core.biostatistics import (
    mortality_bounding_concentrations,
    logistic_function,
    calculate_lc50_robust,
    calculate_noec_loec_with_correction,
    abbott_correct,
    pooled_control_mortality_pct,
    cochran_armitage_trend_test,
    wilson_ci,
    odds_ratio_ci,
    benjamini_hochberg,
    significance_marker,
    sublethal_endpoint_stats,
    calculate_teratogenic_index,
    format_with_unit,
    evaluable_n,
    select_control_rows,
    compare_control_groups,
    CONTROL_MODE_POOLED,
    CONTROL_MODE_NEGATIVE,
    CONTROL_MODE_SOLVENT,
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
        """Three groups cannot determine a 4PL, which has four free parameters."""
        data = self._make_plot_data([1.0, 2.0, 3.0], [0.0, 50.0, 100.0])
        result = calculate_lc50_robust(data)
        assert "not enough concentration groups" in result["lc50"].lower()

    def test_refusal_names_the_requested_model(self):
        """The message must not say 4PL when a 2PL was asked for."""
        data = self._make_plot_data([1.0], [50.0])
        result = calculate_lc50_robust(data, bottom=0.0, top=100.0)
        assert "2PL" in result["lc50"]
        assert "4PL" not in result["lc50"]

    def test_constrained_model_fits_three_groups(self):
        """A 2PL has two free parameters, so three groups are ample.

        The gate was previously fixed at four regardless of model, which refused
        fits the data could support whenever asymptotes were constrained.
        """
        data = self._make_plot_data([1.0, 2.0, 4.0], [10.0, 50.0, 90.0])
        result = calculate_lc50_robust(data, bottom=0.0, top=100.0)
        assert result.get("_fitted_params") is not None
        assert "not enough" not in result["lc50"].lower()

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


class TestAbbottCorrect:
    def test_zero_control_is_identity(self):
        """Control mortality of 0% leaves the observed value unchanged."""
        assert abbott_correct(40.0, 0.0) == pytest.approx(40.0)

    def test_standard_correction(self):
        """(55 - 10) / (100 - 10) * 100 = 50.0."""
        assert abbott_correct(55.0, 10.0) == pytest.approx(50.0)
        assert abbott_correct(50.0, 20.0) == pytest.approx(37.5)

    def test_observed_below_control_clamps_to_zero(self):
        """Corrected mortality is never negative."""
        assert abbott_correct(5.0, 10.0) == 0.0

    def test_full_control_mortality_is_undefined(self):
        """Control mortality of 100% makes the correction undefined (nan)."""
        assert math.isnan(abbott_correct(50.0, 100.0))


class TestPooledControlMortalityPct:
    def test_pools_control_and_solvent_by_counts(self):
        """Control 1/10 + Solvent 2/10 -> 3/20 = 15%."""
        df = pd.DataFrame([
            {"conc_type": "Control", "conc_value": 0.0, "total": 10, "dead": 1},
            {"conc_type": "Solvent Control", "conc_value": 0.0, "total": 10, "dead": 2},
            {"conc_type": "Substrate", "conc_value": 1.0, "total": 10, "dead": 5},
        ])
        assert pooled_control_mortality_pct(df) == pytest.approx(15.0)

    def test_no_control_returns_none(self):
        df = pd.DataFrame([
            {"conc_type": "Substrate", "conc_value": 1.0, "total": 10, "dead": 5},
        ])
        assert pooled_control_mortality_pct(df) is None

    def test_zero_control_total_returns_none(self):
        df = pd.DataFrame([
            {"conc_type": "Control", "conc_value": 0.0, "total": 0, "dead": 0},
            {"conc_type": "Substrate", "conc_value": 1.0, "total": 10, "dead": 5},
        ])
        assert pooled_control_mortality_pct(df) is None


class TestCochranArmitageTrendTest:
    def _make_df(self, control, treatments, control_type="Control"):
        """
        control:     (dead, total) for the pooled control group.
        treatments:  list of (conc_value, dead, total) for Substrate groups.
        """
        rows = [{
            "conc_id": "ctrl", "conc_type": control_type,
            "conc_value": 0.0, "total": control[1], "dead": control[0],
        }]
        for i, (conc_val, dead, total) in enumerate(treatments):
            rows.append({
                "conc_id": f"t{i}", "conc_type": "Substrate",
                "conc_value": conc_val, "total": total, "dead": dead,
            })
        return pd.DataFrame(rows)

    def test_increasing_trend_is_significant(self):
        """Monotonically increasing mortality -> significant, small p-value."""
        df = self._make_df((0, 10), [(1.0, 2, 10), (2.0, 5, 10), (4.0, 9, 10)])
        result = cochran_armitage_trend_test(df)
        assert "significant" in result["trend"].lower()
        assert result["trend"].lower().startswith("significant")
        p_val = float(result["p_value"])
        assert p_val < 0.05

    def test_flat_response_not_significant(self):
        """No dose-related change -> not significant."""
        df = self._make_df((1, 10), [(1.0, 1, 10), (2.0, 1, 10), (4.0, 1, 10)])
        result = cochran_armitage_trend_test(df)
        assert result["trend"] == "No significant dose-response trend."
        assert float(result["p_value"]) > 0.05

    def test_all_zero_mortality_not_applicable(self):
        """No mortality anywhere -> variance is zero, test not applicable."""
        df = self._make_df((0, 10), [(1.0, 0, 10), (2.0, 0, 10)])
        result = cochran_armitage_trend_test(df)
        assert "not applicable" in result["trend"].lower()

    def test_single_substrate_returns_error(self):
        """Fewer than two dose groups -> descriptive error, no trend."""
        df = self._make_df((0, 10), [(1.0, 5, 10)])
        result = cochran_armitage_trend_test(df)
        assert "not enough" in result["trend"].lower()

    def test_no_control_returns_error(self):
        rows = [
            {"conc_id": "t0", "conc_type": "Substrate", "conc_value": 1.0, "total": 10, "dead": 2},
            {"conc_id": "t1", "conc_type": "Substrate", "conc_value": 2.0, "total": 10, "dead": 5},
        ]
        result = cochran_armitage_trend_test(pd.DataFrame(rows))
        assert "control group not found" in result["trend"].lower()

    def test_solvent_control_pooled_as_baseline(self):
        """A Solvent Control row is accepted as the zero-dose baseline."""
        df = self._make_df((0, 10), [(1.0, 3, 10), (2.0, 6, 10), (4.0, 9, 10)],
                           control_type="Solvent Control")
        result = cochran_armitage_trend_test(df)
        assert "significant" in result["trend"].lower()
        assert float(result["p_value"]) < 0.05


class TestWilsonCI:
    def test_zero_n_returns_zeros(self):
        assert wilson_ci(0, 0) == (0.0, 0.0, 0.0)

    def test_center_matches_proportion(self):
        c, lo, hi = wilson_ci(5, 10)
        assert lo < c < hi
        assert 40.0 < c < 60.0  # centered near 50%, shrunk toward 0.5

    def test_bounds_clamped_to_unit_interval(self):
        c, lo, hi = wilson_ci(10, 10)  # 100% observed
        assert lo >= 0.0 and hi <= 100.0
        assert lo > 0.0  # Wilson does not collapse to a zero-width CI at the boundary

    def test_zero_successes_lower_bound_zero(self):
        c, lo, hi = wilson_ci(0, 20)
        assert lo == 0.0
        assert hi > 0.0


class TestOddsRatioCI:
    def test_standard_odds_ratio(self):
        # treat 8/10 event, control 1/10 event -> OR = (8*9)/(2*1) = 36
        or_, lo, hi = odds_ratio_ci(8, 10, 1, 10)
        assert or_ == pytest.approx(36.0)
        assert lo < or_ < hi

    def test_zero_cell_uses_haldane_correction(self):
        # treat 10/10 (non-event cell = 0) must stay finite via +0.5 correction
        or_, lo, hi = odds_ratio_ci(10, 10, 1, 10)
        assert math.isfinite(or_) and or_ > 1.0
        assert math.isfinite(lo) and math.isfinite(hi)

    def test_no_effect_gives_or_near_one(self):
        or_, lo, hi = odds_ratio_ci(5, 10, 5, 10)
        assert or_ == pytest.approx(1.0)
        assert lo < 1.0 < hi

    def test_degenerate_totals_return_nan(self):
        or_, lo, hi = odds_ratio_ci(0, 0, 1, 10)
        assert math.isnan(or_)


class TestBenjaminiHochberg:
    def test_empty_input(self):
        assert len(benjamini_hochberg([])) == 0

    def test_two_value_adjustment_and_order(self):
        adj = benjamini_hochberg([0.01, 0.04])
        assert adj[0] == pytest.approx(0.02)
        assert adj[1] == pytest.approx(0.04)

    def test_all_equal_pvalues_stay_capped(self):
        adj = benjamini_hochberg([0.05, 0.05, 0.05, 0.05, 0.05])
        assert np.allclose(adj, 0.05)

    def test_monotonic_non_decreasing_in_rank(self):
        adj = benjamini_hochberg([0.001, 0.02, 0.03, 0.9])
        srt = np.sort(adj)
        assert np.all(np.diff(srt) >= -1e-12)


class TestSignificanceMarker:
    def test_thresholds(self):
        assert significance_marker(0.0005) == "***"
        assert significance_marker(0.005) == "**"
        assert significance_marker(0.03) == "*"
        assert significance_marker(0.2) == "ns"
        assert significance_marker(float("nan")) == "n.d."
        assert significance_marker(None) == "n.d."


def _sublethal_df():
    return pd.DataFrame([
        {"conc_id": "ctrl", "conc_type": "Control",   "conc_value": 0.0, "total": 20,
         "malformed": 0, "malformation_details": Counter()},
        {"conc_id": "s1",   "conc_type": "Substrate",  "conc_value": 1.0, "total": 20,
         "malformed": 2, "malformation_details": Counter({"Oedema": 2})},
        {"conc_id": "s2",   "conc_type": "Substrate",  "conc_value": 2.0, "total": 20,
         "malformed": 8, "malformation_details": Counter({"Oedema": 6, "Tail malformation": 3})},
        {"conc_id": "s3",   "conc_type": "Substrate",  "conc_value": 4.0, "total": 20,
         "malformed": 16, "malformation_details": Counter({"Oedema": 14, "Tail malformation": 9})},
    ])


class TestSublethalEndpointStats:
    def test_missing_details_not_available(self):
        df = pd.DataFrame([{"conc_id": "s1", "conc_type": "Substrate", "conc_value": 1.0,
                            "total": 10, "malformed": 3}])
        assert sublethal_endpoint_stats(df)["available"] is False

    def test_battery_runs_and_flags_high_dose(self):
        res = sublethal_endpoint_stats(_sublethal_df())
        assert res["available"] is True
        # every endpoint x substrate group produces a test with a BH-adjusted p
        assert all("p_adj" in t for t in res["tests"])
        oedema_high = [t for t in res["tests"]
                       if t["endpoint"] == "Oedema" and t["conc_id"] == "s3"]
        assert oedema_high and oedema_high[0]["p_adj"] < 0.05
        assert oedema_high[0]["or"] > 1.0

    def test_per_endpoint_trend_present(self):
        res = sublethal_endpoint_stats(_sublethal_df())
        assert "Oedema" in res["ca"]
        assert res["ca"]["Oedema"]["p"] < 0.05

    def test_pooled_sublethal_noec_loec(self):
        res = sublethal_endpoint_stats(_sublethal_df())
        pooled = res["pooled"]
        assert len(pooled["tests"]) == 3
        # a clear dose-response should yield a detected LOEC
        assert pooled["loec"] != "Not detected"


class TestTeratogenicIndex:
    def _df(self):
        return pd.DataFrame([
            {"conc_id": "ctrl", "conc_type": "Control",  "conc_value": 0.0, "total": 20, "malformed": 0},
            {"conc_id": "s1",   "conc_type": "Substrate", "conc_value": 0.5, "total": 20, "malformed": 1},
            {"conc_id": "s2",   "conc_type": "Substrate", "conc_value": 1.0, "total": 20, "malformed": 3},
            {"conc_id": "s3",   "conc_type": "Substrate", "conc_value": 2.0, "total": 20, "malformed": 8},
            {"conc_id": "s4",   "conc_type": "Substrate", "conc_value": 4.0, "total": 20, "malformed": 15},
            {"conc_id": "s5",   "conc_type": "Substrate", "conc_value": 8.0, "total": 20, "malformed": 19},
        ])

    def test_ec50_and_ti_computed(self):
        res = calculate_teratogenic_index(self._df(), lc50_numeric=6.0)
        assert res["ec50_malformation"][:1].isdigit()
        assert res["teratogenic_index"] != "Not Calculated"
        assert res["interpretation"]

    def test_insufficient_points(self):
        df = pd.DataFrame([
            {"conc_id": "s1", "conc_type": "Substrate", "conc_value": 1.0, "total": 20, "malformed": 1},
        ])
        res = calculate_teratogenic_index(df, lc50_numeric=6.0)
        assert res["teratogenic_index"] == "Not Calculated"


class TestRigorousBootstrap:
    def test_rigorous_method_selected_and_ci_present(self):
        """Passing count data + a control switches the CI to the rigorous
        (control-resampled + Abbott in-loop) bootstrap."""
        concs = [1.0, 2.0, 4.0, 8.0, 16.0]
        dead = [1, 3, 10, 18, 20]
        total = [20] * 5
        ctrl_k, ctrl_n = 1, 20
        p_ctrl = ctrl_k / ctrl_n * 100.0
        plot_data, count_data = [], []
        for x, d, n in zip(concs, dead, total):
            plot_data.append({"type": "Substrate", "x": x, "y": abbott_correct(d / n * 100.0, p_ctrl)})
            count_data.append({"x": x, "k": d, "n": n})
        res = calculate_lc50_robust(plot_data, count_data=count_data, control_counts=(ctrl_k, ctrl_n))
        assert res.get("bootstrap_method") == "rigorous"
        assert "95% CI" in res["lc50"], res["lc50"]

    def test_default_is_case_resampling(self):
        data = [{"type": "Substrate", "x": x, "y": y}
                for x, y in zip([1, 2, 4, 8, 16], [0, 10, 50, 90, 100])]
        res = calculate_lc50_robust(data)
        assert res.get("bootstrap_method") == "case-resampling"


class TestEmptyDataFrameGuards:
    """Regression: stats entry points must not KeyError on a column-less empty frame."""

    def test_cochran_armitage_empty_df(self):
        res = cochran_armitage_trend_test(pd.DataFrame())
        assert res["trend"] == "Control group not found"
        assert res["p_value"] == "Not Calculated"

    def test_pooled_control_empty_df(self):
        assert pooled_control_mortality_pct(pd.DataFrame()) is None

    def test_sublethal_empty_df(self):
        assert sublethal_endpoint_stats(pd.DataFrame())["available"] is False

    def test_duplicated_condition_clamped_not_negative(self):
        # A group of 10 with an endpoint count of 12 (duplicated entry) must clamp
        # to n rather than produce a negative Fisher cell / crash.
        df = pd.DataFrame([
            {"conc_id": "ctrl", "conc_type": "Control",   "conc_value": 0.0, "total": 10,
             "malformed": 0, "malformation_details": Counter()},
            {"conc_id": "s1",   "conc_type": "Substrate",  "conc_value": 1.0, "total": 10,
             "malformed": 10, "malformation_details": Counter({"Oedema": 12})},
            {"conc_id": "s2",   "conc_type": "Substrate",  "conc_value": 2.0, "total": 10,
             "malformed": 10, "malformation_details": Counter({"Oedema": 10})},
        ])
        res = sublethal_endpoint_stats(df)
        assert res["available"] is True
        for t in res["tests"]:
            assert t["k"] <= t["n"]


class TestSurvivorDenominator:
    """Malformation endpoints are scored among survivors (live), not total."""

    def test_sublethal_n_is_live_not_total(self):
        df = pd.DataFrame([
            {"conc_id": "ctrl", "conc_type": "Control",  "conc_value": 0.0, "total": 20, "live": 20,
             "malformed": 0, "malformation_details": Counter()},
            {"conc_id": "s1",   "conc_type": "Substrate", "conc_value": 1.0, "total": 20, "live": 10,
             "malformed": 5, "malformation_details": Counter({"Oedema": 5})},
            {"conc_id": "s2",   "conc_type": "Substrate", "conc_value": 2.0, "total": 20, "live": 8,
             "malformed": 6, "malformation_details": Counter({"Oedema": 6})},
        ])
        res = sublethal_endpoint_stats(df)
        oed = [t for t in res["tests"] if t["endpoint"] == "Oedema" and t["conc_id"] == "s1"][0]
        assert oed["n"] == 10          # survivors, not total (20)
        assert oed["k"] == 5
        pooled_s1 = [t for t in res["pooled"]["tests"] if t["conc_id"] == "s1"][0]
        assert pooled_s1["n"] == 10

    def test_group_with_no_survivors_is_skipped(self):
        df = pd.DataFrame([
            {"conc_id": "ctrl", "conc_type": "Control",  "conc_value": 0.0, "total": 20, "live": 20,
             "malformed": 0, "malformation_details": Counter()},
            {"conc_id": "s1",   "conc_type": "Substrate", "conc_value": 1.0, "total": 20, "live": 10,
             "malformed": 4, "malformation_details": Counter({"Oedema": 4})},
            {"conc_id": "s2",   "conc_type": "Substrate", "conc_value": 4.0, "total": 20, "live": 0,
             "malformed": 0, "malformation_details": Counter()},
        ])
        res = sublethal_endpoint_stats(df)
        # s2 has zero survivors -> not represented among the per-group tests
        assert all(t["conc_id"] != "s2" for t in res["tests"])
        assert all(t["conc_id"] != "s2" for t in res["pooled"]["tests"])

    def test_ti_denominator_is_live(self):
        # Malformed counts fixed; shrinking 'live' raises the malformation rate and
        # therefore lowers the EC50 relative to a total-based denominator.
        rows = [{"conc_id": "ctrl", "conc_type": "Control", "conc_value": 0.0, "total": 20, "live": 20, "malformed": 0}]
        for i, (cv, live, malf) in enumerate([(0.5, 18, 1), (1.0, 15, 3), (2.0, 12, 6), (4.0, 8, 6), (8.0, 5, 5)]):
            rows.append({"conc_id": f"s{i}", "conc_type": "Substrate", "conc_value": cv,
                         "total": 20, "live": live, "malformed": malf})
        res = calculate_teratogenic_index(pd.DataFrame(rows), lc50_numeric=5.0)
        assert res["ec50_malformation"][:1].isdigit()
        assert res["teratogenic_index"] != "Not Calculated"


# ---------------------------------------------------------------------------
# Endpoint values must be machine-readable, not inferred from their prose
# ---------------------------------------------------------------------------

#: Every message calculate_lc50_robust can return in place of a concentration.
LC50_FAILURE_MESSAGES = [
    "Not enough data points (>3) for 4PL curve fitting.",
    "Insufficient unique concentrations for 4PL fitting.",
    "No mortality observed; LC50 is above the highest concentration tested.",
    "100% mortality at all concentrations; LC50 is below the lowest concentration tested.",
    "No variability in mortality values; curve fitting not possible.",
    "Curve fitting failed; data may be inconsistent.",
    "An unexpected error occurred during calculation.",
]


class TestFormatWithUnit:
    """Guards the defect where a failure message was rendered as a result.

    The report and the Results tab used to decide "is this a number?" with
    `text[:1].isdigit()`. That is true for "100% mortality at all
    concentrations...", which produced the sentence "the LC50 was determined to
    be 100% mortality at all concentrations; ... mg/L".
    """

    @pytest.mark.parametrize("message", LC50_FAILURE_MESSAGES)
    def test_failure_messages_never_receive_a_unit(self, message):
        assert format_with_unit(None, message, "mg/L") == message

    @pytest.mark.parametrize("message", LC50_FAILURE_MESSAGES)
    def test_leading_digit_heuristic_would_have_failed(self, message):
        """Documents why the numeric mirror exists rather than a string test."""
        if message.startswith("100%"):
            assert message[:1].isdigit()  # the exact case that broke

    def test_real_values_receive_the_unit(self):
        assert format_with_unit(2.0, "2.0000", "mg/L") == "2.0000 mg/L"

    def test_lc50_failure_leaves_no_fitted_params(self):
        """The report's numeric test keys off this, so it must hold."""
        flat = [{"type": "Substrate", "x": x, "y": 100.0} for x in (1.0, 2.0, 4.0, 8.0, 16.0)]
        result = calculate_lc50_robust(flat)
        assert result.get("_fitted_params") is None
        assert result["lc50"].startswith("100% mortality")


class TestNoecLoecNumericMirrors:
    def _frame(self, control_dead, treatment_dead):
        rows = [{"conc_id": "CTRL", "conc_type": "Control", "conc_value": 0.0,
                 "total": 20, "dead": control_dead}]
        for i, (cv, dead) in enumerate(zip([1.0, 2.0, 4.0, 8.0], treatment_dead)):
            rows.append({"conc_id": f"C{i}", "conc_type": "Substrate",
                         "conc_value": cv, "total": 20, "dead": dead})
        return pd.DataFrame(rows)

    def test_numeric_mirror_accompanies_a_real_concentration(self):
        result = calculate_noec_loec_with_correction(self._frame(0, [0, 1, 12, 19]))
        assert result["loec_numeric"] is not None
        assert result["loec"] == f"{result['loec_numeric']:.4f}"

    def test_numeric_mirror_is_none_for_an_explanatory_message(self):
        frame = pd.DataFrame([{"conc_id": "C0", "conc_type": "Substrate",
                               "conc_value": 1.0, "total": 20, "dead": 5}])
        result = calculate_noec_loec_with_correction(frame)
        assert result["noec_numeric"] is None
        assert result["noec"] == "Control group not found"
        # The sentence the report builds must not gain a unit.
        assert format_with_unit(result["noec_numeric"], result["noec"], "mg/L") \
            == "Control group not found"


# ---------------------------------------------------------------------------
# Denominators
# ---------------------------------------------------------------------------

class TestEvaluableN:
    def test_prefers_scored_over_assigned(self):
        frame = pd.DataFrame([{"total": 20, "n_scored": 18}])
        assert evaluable_n(frame).iloc[0] == 18

    def test_falls_back_to_total_for_older_callers(self):
        frame = pd.DataFrame([{"total": 20}])
        assert evaluable_n(frame).iloc[0] == 20

    def test_accepts_a_single_row(self):
        row = pd.Series({"total": 20, "n_scored": 15})
        assert evaluable_n(row) == 15

    def test_unscored_group_no_longer_reads_as_survival(self):
        """A group with nothing scored must not count as a zero-mortality dose."""
        rows = [{"conc_id": "CTRL", "conc_type": "Control", "conc_value": 0.0,
                 "total": 20, "n_scored": 20, "dead": 0}]
        rows.append({"conc_id": "UNSCORED", "conc_type": "Substrate", "conc_value": 2.0,
                     "total": 20, "n_scored": 0, "dead": 0})
        assert evaluable_n(pd.DataFrame(rows)).tolist() == [20, 0]


# ---------------------------------------------------------------------------
# Bootstrap and fit integrity
# ---------------------------------------------------------------------------

class TestFitIntegrity:
    """Four concentration groups is a real design, not a data error."""

    FOUR_GROUPS = [{"type": "Substrate", "x": x, "y": y}
                   for x, y in [(1.0, 10.0), (2.0, 25.0), (4.0, 55.0), (8.0, 90.0)]]

    def test_exactly_determined_fit_reports_an_lc50(self):
        result = calculate_lc50_robust(self.FOUR_GROUPS)
        assert result.get("_fitted_params") is not None
        assert result["lc50"][:1].isdigit()

    def test_exactly_determined_fit_suppresses_the_interval(self):
        """A curve through every point cannot also measure its own uncertainty."""
        result = calculate_lc50_robust(self.FOUR_GROUPS)
        assert "CI not estimable" in result["lc50"]
        assert "Bootstrap 95% CI" not in result["lc50"]

    def test_over_determined_fit_still_reports_an_interval(self):
        five = self.FOUR_GROUPS + [{"type": "Substrate", "x": 16.0, "y": 98.0}]
        assert "Bootstrap 95% CI" in calculate_lc50_robust(five)["lc50"]


# ---------------------------------------------------------------------------
# Reference-control selection
# ---------------------------------------------------------------------------

def _control_frame(negative_dead=1, solvent_dead=1, n=20, with_solvent=True):
    rows = [{"conc_id": "CTRL", "conc_type": "Control", "conc_value": 0.0,
             "total": n, "n_scored": n, "dead": negative_dead}]
    if with_solvent:
        rows.append({"conc_id": "SC", "conc_type": "Solvent Control", "conc_value": 0.0,
                     "total": n, "n_scored": n, "dead": solvent_dead})
    rows.append({"conc_id": "C1", "conc_type": "Substrate", "conc_value": 1.0,
                 "total": n, "n_scored": n, "dead": 10})
    return pd.DataFrame(rows)


class TestSelectControlRows:
    def test_pooled_selects_both_controls(self):
        rows = select_control_rows(_control_frame(), CONTROL_MODE_POOLED)
        assert set(rows["conc_id"]) == {"CTRL", "SC"}

    def test_negative_and_solvent_select_one_each(self):
        frame = _control_frame()
        assert set(select_control_rows(frame, CONTROL_MODE_NEGATIVE)["conc_id"]) == {"CTRL"}
        assert set(select_control_rows(frame, CONTROL_MODE_SOLVENT)["conc_id"]) == {"SC"}

    def test_missing_solvent_falls_back_rather_than_selecting_nothing(self):
        """Solvent controls are optional, so this must never divide by zero."""
        rows = select_control_rows(_control_frame(with_solvent=False), CONTROL_MODE_SOLVENT)
        assert not rows.empty
        assert set(rows["conc_id"]) == {"CTRL"}

    def test_unknown_mode_falls_back_to_pooled(self):
        rows = select_control_rows(_control_frame(), "nonsense")
        assert set(rows["conc_id"]) == {"CTRL", "SC"}

    def test_mode_changes_the_background_mortality(self):
        frame = _control_frame(negative_dead=1, solvent_dead=9)
        negative = pooled_control_mortality_pct(frame, CONTROL_MODE_NEGATIVE)
        pooled = pooled_control_mortality_pct(frame, CONTROL_MODE_POOLED)
        solvent = pooled_control_mortality_pct(frame, CONTROL_MODE_SOLVENT)
        assert negative == pytest.approx(5.0)
        assert solvent == pytest.approx(45.0)
        assert negative < pooled < solvent


class TestCompareControlGroups:
    def test_similar_controls_do_not_differ(self):
        result = compare_control_groups(_control_frame(negative_dead=1, solvent_dead=2))
        assert result["applicable"] is True
        assert result["differ"] is False

    def test_toxic_solvent_is_detected(self):
        result = compare_control_groups(_control_frame(negative_dead=1, solvent_dead=9))
        assert result["differ"] is True
        assert result["p_value"] < 0.05

    def test_is_two_sided(self):
        """A solvent control may be either worse or better than the negative one.

        A one-sided test in the "treatment is worse" direction, as used elsewhere
        in this module, would miss a solvent control that is markedly *less*
        lethal — which is equally a reason not to pool them.
        """
        protective = compare_control_groups(_control_frame(negative_dead=9, solvent_dead=1))
        toxic = compare_control_groups(_control_frame(negative_dead=1, solvent_dead=9))
        assert protective["differ"] is True
        assert protective["p_value"] == pytest.approx(toxic["p_value"])

    def test_inapplicable_without_a_solvent_control(self):
        result = compare_control_groups(_control_frame(with_solvent=False))
        assert result["applicable"] is False
        assert "solvent" in result["summary"].lower()

    def test_summary_quotes_both_groups(self):
        summary = compare_control_groups(_control_frame(negative_dead=1, solvent_dead=9))["summary"]
        assert "1/20" in summary and "9/20" in summary


class TestFittingEmitsNoLibraryWarnings:
    """Fit failures are reported through return values, not scipy warnings.

    `_fit_model_variant` deliberately tolerates an undefined covariance for an
    exactly-determined fit and rejects one for an over-determined fit. Both make
    scipy emit OptimizeWarning, which is an implementation detail: the function
    converts the condition into a documented return value, and the user is told
    in plain language. Letting the raw warning escape put 28 lines of noise into
    the test run and would reach a packaged application's stderr.
    """

    #: Malformation-style data with no dose-related trend: the fit is
    #: over-determined and its covariance cannot be estimated.
    FLAT_RESPONSE = [{"type": "Substrate", "x": x, "y": 25.0}
                     for x in (1.0, 2.0, 4.0, 8.0, 16.0)]
    EXACTLY_DETERMINED = [{"type": "Substrate", "x": x, "y": y}
                          for x, y in [(1.0, 10.0), (2.0, 25.0), (4.0, 55.0), (8.0, 90.0)]]

    def test_no_optimize_warning_escapes_a_rejected_fit(self, recwarn):
        calculate_lc50_robust(self.FLAT_RESPONSE)
        assert [w for w in recwarn.list
                if issubclass(w.category, OptimizeWarning)] == []

    def test_no_optimize_warning_escapes_an_exactly_determined_fit(self, recwarn):
        calculate_lc50_robust(self.EXACTLY_DETERMINED)
        assert [w for w in recwarn.list
                if issubclass(w.category, OptimizeWarning)] == []

    def test_suppression_did_not_mask_the_rejection(self):
        """Silencing the warning must not turn a bad fit into a reported result."""
        result = calculate_lc50_robust(self.FLAT_RESPONSE)
        assert result.get("_fitted_params") is None
        assert not result["lc50"][:1].isdigit()

    def test_malformation_ec50_reports_a_neutral_failure(self):
        """The teratogenic index must not borrow mortality wording on failure."""
        rows = [{"conc_id": f"C{i}", "conc_type": "Substrate", "conc_value": cv,
                 "total": 20, "n_scored": 20, "live": 20, "malformed": 5}
                for i, cv in enumerate([1.0, 2.0, 4.0, 8.0, 16.0])]
        result = calculate_teratogenic_index(pd.DataFrame(rows), lc50_numeric=4.0)
        assert result["ti_numeric"] is None
        assert "mortality" not in result["ec50_malformation"].lower()


class TestDegenerateBootstrapInterval:
    """A zero-width interval asserts perfect precision and must not be reported.

    Found on real data: in a well-spaced assay of a potent compound, nearly every
    dose group sits at exactly 0% or 100% mortality. Binomial(n, 0) and
    Binomial(n, 1) have no variance, so the parametric bootstrap reproduces the
    observed data on every iteration and all 500 LC50s coincide. The result was
    published as "95% CI: 0.9223 - 0.9223".
    """

    def test_all_or_nothing_response_yields_no_interval(self):
        """Mortality exactly 0% below the threshold and 100% above it."""
        plot_data = [{"type": "Substrate", "x": x, "y": y} for x, y in
                     [(0.03, 0.0), (0.08, 0.0), (0.19, 0.0),
                      (0.48, 0.0), (1.2, 100.0), (3.0, 100.0), (7.5, 100.0)]]
        count_data = [{"x": x, "k": k, "n": 60} for x, k in
                      [(0.03, 0), (0.08, 0), (0.19, 0),
                       (0.48, 0), (1.2, 60), (3.0, 60), (7.5, 60)]]
        result = calculate_lc50_robust(plot_data, count_data=count_data,
                                       control_counts=(0, 120))
        assert result.get("_fitted_params") is not None      # the LC50 is sound
        assert "Bootstrap 95% CI" not in result["lc50"]      # the interval is not
        assert "not available" in result["lc50"]

    def test_a_real_interval_is_still_reported(self):
        """The guard must not suppress intervals that carry information."""
        plot_data = [{"type": "Substrate", "x": x, "y": y} for x, y in
                     [(1.0, 12.0), (2.0, 28.0), (4.0, 51.0), (8.0, 74.0), (16.0, 91.0)]]
        assert "Bootstrap 95% CI" in calculate_lc50_robust(plot_data)["lc50"]


class TestMortalityBoundingConcentrations:
    """OECD TG 236 §42 requires the concentrations bracketing the lethal range.

    They show how much of the curve rests on observed data: an LC50 interpolated
    between a group that killed nothing and one that killed everything is on firmer
    ground than one extrapolated past both.
    """

    def _frame(self, doses):
        rows = [{"conc_id": "ctrl", "conc_type": "Control", "conc_value": 0.0,
                 "n_scored": 20, "dead": 1}]
        rows += [{"conc_id": f"s{i}", "conc_type": "Substrate", "conc_value": c,
                  "n_scored": 20, "dead": d} for i, (c, d) in enumerate(doses, 1)]
        return pd.DataFrame(rows)

    def test_both_bounds_from_a_bracketing_series(self):
        result = mortality_bounding_concentrations(
            self._frame([(0.5, 0), (1.0, 0), (2.0, 7), (4.0, 20), (8.0, 20)])
        )
        assert result["no_mortality_max"] == 1.0
        assert result["full_mortality_min"] == 4.0

    def test_the_highest_clean_and_lowest_lethal_are_chosen(self):
        """Not the first of each — the bounds must be the tightest available."""
        result = mortality_bounding_concentrations(
            self._frame([(0.1, 0), (0.5, 0), (1.0, 0), (4.0, 20), (8.0, 20)])
        )
        assert result["no_mortality_max"] == 1.0
        assert result["full_mortality_min"] == 4.0

    def test_a_series_that_brackets_neither_reports_neither(self):
        result = mortality_bounding_concentrations(
            self._frame([(1.0, 3), (2.0, 9), (4.0, 15)])
        )
        assert result == {"no_mortality_max": None, "full_mortality_min": None}

    def test_controls_are_not_eligible(self):
        """A control with no deaths is not a concentration causing no mortality."""
        result = mortality_bounding_concentrations(self._frame([(1.0, 4)]))
        assert result["no_mortality_max"] is None

    def test_unscored_groups_are_ignored(self):
        frame = self._frame([(1.0, 0), (2.0, 8)])
        frame.loc[frame["conc_value"] == 1.0, "n_scored"] = 0
        assert mortality_bounding_concentrations(frame)["no_mortality_max"] is None

    def test_full_mortality_uses_the_scored_denominator(self):
        """20 dead of 20 assigned but 10 scored is still 100% of what was scored."""
        frame = self._frame([(1.0, 10)])
        frame.loc[frame["conc_value"] == 1.0, "n_scored"] = 10
        assert mortality_bounding_concentrations(frame)["full_mortality_min"] == 1.0

    def test_an_empty_frame_yields_no_bounds(self):
        assert mortality_bounding_concentrations(pd.DataFrame()) == {
            "no_mortality_max": None, "full_mortality_min": None
        }


class TestModuleIsolation:
    """The module must import with no Qt, UI, database or persistence layer.

    The software paper states this in the architecture section and again in the
    figure caption, and the About tab repeats it to users. A single stray import
    would falsify a published claim while every other test kept passing, because
    the rest of the suite imports PySide6 anyway and would mask it.

    Run in a subprocess: by the time this test executes, other modules in the
    suite have already put PySide6 into sys.modules.
    """

    #: Everything the module is claimed not to depend on.
    FORBIDDEN = ("PySide6", "src.ui", "src.database", "src.export",
                 "src.core.project_manager", "src.core.task_manager")

    def _run_isolated(self, body: str):
        import subprocess
        import sys
        import textwrap

        script = textwrap.dedent(f"""
            import sys, importlib.abc
            FORBIDDEN = {self.FORBIDDEN!r}

            class Blocker(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    for name in FORBIDDEN:
                        if fullname == name or fullname.startswith(name + "."):
                            raise ImportError("blocked: " + fullname)
                    return None

            sys.meta_path.insert(0, Blocker())
            sys.path.insert(0, {str(PROJECT_ROOT)!r})
        """) + textwrap.dedent(body)
        return subprocess.run([sys.executable, "-c", script],
                              capture_output=True, text=True)

    def test_imports_without_qt_or_the_app_layers(self):
        result = self._run_isolated("""
            import src.core.biostatistics as bio
            assert "PySide6" not in sys.modules
            print("OK", bio.__name__)
        """)
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith("OK")

    def test_computes_endpoints_without_qt(self):
        """Importing is not enough — the analysis has to actually run."""
        result = self._run_isolated("""
            import pandas as pd
            import src.core.biostatistics as bio

            df = pd.DataFrame({
                "conc_id":    ["Co1", "C1", "C2", "C3", "C4"],
                "conc_type":  ["Control"] + ["Substrate"] * 4,
                "conc_value": [0.0, 2.5, 5.0, 10.0, 20.0],
                "total":      [20] * 5,
                "n_scored":   [20] * 5,
                "dead":       [0, 1, 4, 14, 20],
                "hatched":    [20, 19, 16, 6, 0],
            })
            plot_data = [
                {"id": r.conc_id, "type": r.conc_type, "x": r.conc_value,
                 "y": r.dead / r.total * 100.0, "n": r.total, "dead": r.dead}
                for r in df.itertuples()
            ]
            assert bio.select_best_model_lc50(plot_data).get("lc50")
            assert bio.calculate_noec_loec_with_correction(df).get("noec")
            assert bio.cochran_armitage_trend_test(df).get("p_value") is not None
            assert "PySide6" not in sys.modules
            print("OK")
        """)
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith("OK")

    def test_the_blocker_would_catch_a_violation(self):
        """Guards the guard: prove the subprocess really does reject Qt."""
        result = self._run_isolated("""
            import PySide6
        """)
        assert result.returncode != 0
        assert "blocked: PySide6" in result.stderr


class TestAbsoluteLc50:
    """The reported LC50 must be the 50%-mortality concentration.

    The logistic's fourth parameter is the curve's inflection — the midpoint
    *between the fitted asymptotes*. That is the relative EC50, and it coincides
    with the concentration killing half the embryos only when the asymptotes are
    0 and 100. Reporting the inflection understated the LC50 by 20% on a fit
    topping out at 79% mortality, and on an early timepoint that never reaches
    50% it invented a number outright.
    """

    def test_two_parameter_fit_is_unchanged(self):
        """bottom=0, top=100 is the case where the two definitions agree.

        Pinned because the published FET-15 analysis selects this model: the
        correction must be a provable no-op there.
        """
        from src.core.biostatistics import absolute_lc50

        for inflection, slope in ((0.7585059133369471, -32.6376), (4.0, -1.5), (12.3, -3.0)):
            assert absolute_lc50(0.0, 100.0, slope, inflection) == pytest.approx(
                inflection, rel=1e-12
            )

    @pytest.mark.parametrize("bottom,top,slope,inflection", [
        (0.0, 79.372, -2.5, 3.2037),   # 3PL, free top
        (8.0, 100.0, -2.0, 5.0),       # 3PL, free bottom
        (5.0, 85.0, -3.0, 7.0),        # 4PL, both free
    ])
    def test_free_asymptote_fits_land_on_fifty_percent(self, bottom, top, slope, inflection):
        from src.core.biostatistics import absolute_lc50, logistic_function

        value = absolute_lc50(bottom, top, slope, inflection)
        assert value is not None
        assert value != pytest.approx(inflection)
        # The definition: the curve must pass through 50% there.
        assert logistic_function(np.array([value]), bottom, top, slope, inflection)[0] == \
            pytest.approx(50.0, abs=1e-9)

    def test_returns_none_when_the_curve_never_reaches_fifty(self):
        """A curve topping out at 26% has no LC50; it must not report one."""
        from src.core.biostatistics import absolute_lc50

        assert absolute_lc50(0.0, 25.6, -2.2, 9.24) is None
        assert absolute_lc50(60.0, 100.0, -2.0, 5.0) is None

    def test_degenerate_inputs_are_rejected(self):
        from src.core.biostatistics import absolute_lc50

        assert absolute_lc50(0.0, 100.0, 0.0, 5.0) is None
        assert absolute_lc50(0.0, 100.0, -2.0, 0.0) is None

    def test_reported_lc50_is_the_absolute_one(self):
        """End to end: a response plateauing below 100% must not report the inflection."""
        from src.core.biostatistics import select_best_model_lc50

        pts = [{"id": "Co1", "type": "Control", "x": 0.0, "y": 0.0, "n": 20, "dead": 0}] + [
            {"id": f"C{i}", "type": "Substrate", "x": x, "y": y, "n": 20, "dead": round(y / 5)}
            for i, (x, y) in enumerate(
                [(0.5, 0.0), (1.0, 5.0), (2.0, 20.0), (4.0, 50.0), (8.0, 70.0), (16.0, 78.0)], 1)
        ]
        res = select_best_model_lc50(pts)
        inflection = res["_fitted_params"][3]
        reported = res["lc50_numeric"]
        assert res["_fitted_params"][1] < 100.0, "fixture must select a free-top model"
        assert reported > inflection
        assert reported == pytest.approx(4.03, abs=0.1)


class TestTeratogenicIndexThreshold:
    """Selderslaghs et al. (2009) use TI >= 2 as the teratogen criterion.

    A ratio just above 1 separates the two endpoints by less than the uncertainty
    on either, so the old TI >= 1 rule asserted selective developmental toxicity
    on noise — into a report a user may file.
    """

    def _summary(self):
        """Malformation among survivors, graded so an EC50 is fittable."""
        return pd.DataFrame({
            "conc_id":    ["Co1", "C1", "C2", "C3", "C4", "C5"],
            "conc_type":  ["Control"] + ["Substrate"] * 5,
            "conc_value": [0.0, 1.0, 2.0, 4.0, 8.0, 16.0],
            "total":      [20] * 6,
            "n_scored":   [20] * 6,
            "dead":       [0] * 6,
            "live":       [20] * 6,
            "malformed":  [0, 1, 4, 10, 17, 20],
        })

    def test_threshold_is_two(self):
        from src.core.biostatistics import _TERATOGENIC_INDEX_THRESHOLD
        assert _TERATOGENIC_INDEX_THRESHOLD == 2.0

    def test_ratio_just_above_one_is_not_called_teratogenic(self):
        """The old rule asserted selective developmental toxicity at TI = 1.01."""
        summary = self._summary()
        ec50 = calculate_teratogenic_index(summary, None).get("ec50_numeric")
        assert ec50, "fixture must produce a malformation EC50"

        result = calculate_teratogenic_index(summary, ec50 * 1.5)
        assert result["ti_numeric"] == pytest.approx(1.5, rel=1e-6)
        assert "inconclusive" in result["interpretation"].lower()
        assert "distinct from general lethality" not in result["interpretation"]

    def test_ratio_above_two_is_called_teratogenic(self):
        summary = self._summary()
        ec50 = calculate_teratogenic_index(summary, None).get("ec50_numeric")
        result = calculate_teratogenic_index(summary, ec50 * 2.5)
        assert result["ti_numeric"] == pytest.approx(2.5, rel=1e-6)
        assert "distinct from general lethality" in result["interpretation"]

    def test_ratio_below_one_is_non_selective(self):
        summary = self._summary()
        ec50 = calculate_teratogenic_index(summary, None).get("ec50_numeric")
        result = calculate_teratogenic_index(summary, ec50 * 0.5)
        assert "non-selective" in result["interpretation"]


class TestModelSelectionLimits:
    """What the auto-selector can and cannot reach, and why.

    AICc's correction term divides by (n - k - 1), so it is undefined when the
    design has no more groups than the model has parameters plus one. At OECD
    TG 236's minimum of five test concentrations that excludes the 4PL outright —
    a property of the criterion, not a heuristic, and one the paper has to
    qualify rather than claim all four models are always available.
    """

    def _points(self, n_doses):
        xs = [1.0 * (2.2 ** i) for i in range(n_doses)]
        ys = [min(99.0, 8.0 + 90.0 * i / max(1, n_doses - 1)) for i in range(n_doses)]
        return [{"id": "Co1", "type": "Control", "x": 0.0, "y": 0.0, "n": 20, "dead": 0}] + [
            {"id": f"C{i+1}", "type": "Substrate", "x": x, "y": y,
             "n": 20, "dead": round(y / 5)}
            for i, (x, y) in enumerate(zip(xs, ys))
        ]

    def test_four_parameter_model_is_unreachable_at_the_guideline_minimum(self):
        from src.core.biostatistics import select_best_model_lc50

        table = select_best_model_lc50(self._points(5))["model_info"]["aic_table"]
        fourpl = next(e for e in table if e["k"] == 4)
        assert fourpl["estimable"] is False
        assert math.isinf(fourpl["aicc"])

    def test_a_sixth_concentration_makes_it_available(self):
        from src.core.biostatistics import select_best_model_lc50

        table = select_best_model_lc50(self._points(6))["model_info"]["aic_table"]
        fourpl = next(e for e in table if e["k"] == 4)
        assert fourpl["estimable"] is True

    def test_the_exclusion_is_the_aicc_denominator(self):
        """n - k - 1 == 0 is why, so the guard must track k, not a fixed count."""
        from src.core.biostatistics import _compute_aicc

        assert math.isinf(_compute_aicc(10.0, n=5, k=4))   # 4PL at five groups
        assert not math.isinf(_compute_aicc(10.0, n=6, k=4))
        assert not math.isinf(_compute_aicc(10.0, n=5, k=3))  # 3PL still fits
