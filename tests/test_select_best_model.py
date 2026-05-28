"""
test_select_best_model.py — Tests for select_best_model_lc50 (AICc-based auto model selection).
"""
import numpy as np
import pytest

from src.core.biostatistics import logistic_function, select_best_model_lc50


def _make_substrates(concs, mortalities):
    return [{"type": "Substrate", "x": x, "y": y} for x, y in zip(concs, mortalities)]


class TestSelectBestModelLc50:
    def test_returns_lc50_with_ci_on_clean_data(self):
        data = _make_substrates(
            [1, 2, 4, 8, 16, 32, 64],
            [0, 5, 50, 95, 100, 100, 100],
        )
        result = select_best_model_lc50(data)
        assert "95% CI" in result["lc50"]

    def test_lc50_in_expected_range(self):
        data = _make_substrates(
            [1, 2, 4, 8, 16, 32, 64],
            [0, 5, 50, 95, 100, 100, 100],
        )
        result = select_best_model_lc50(data)
        lc50_val = float(result["lc50"].split()[0])
        assert 2.0 <= lc50_val <= 8.0

    def test_model_info_mode_is_auto(self):
        data = _make_substrates([1, 2, 4, 8, 16], [0, 10, 50, 90, 100])
        result = select_best_model_lc50(data)
        assert result["model_info"]["mode"] == "auto"

    def test_aic_table_populated(self):
        data = _make_substrates([1, 2, 4, 8, 16], [0, 10, 50, 90, 100])
        result = select_best_model_lc50(data)
        aic_table = result["model_info"].get("aic_table")
        assert aic_table is not None
        assert len(aic_table) >= 1

    def test_best_model_has_delta_zero(self):
        data = _make_substrates([1, 2, 4, 8, 16], [0, 10, 50, 90, 100])
        result = select_best_model_lc50(data)
        deltas = [e["delta"] for e in result["model_info"]["aic_table"]]
        assert 0.0 in deltas

    def test_simpler_model_preferred_for_ll2_data(self):
        """Exact LL2 curve (bottom=0, top=100) — AICc should favour LL2 over LL4."""
        concs = np.array([0.5, 1, 2, 4, 8, 16, 32, 64])
        morts = [float(logistic_function(np.array([x]), 0.0, 100.0, -2.0, 4.0)[0]) for x in concs]
        data = _make_substrates(concs, morts)
        result = select_best_model_lc50(data)
        assert result["model_info"]["n_free"] == 2

    def test_fitted_params_present_on_success(self):
        data = _make_substrates([1, 2, 4, 8, 16], [0, 10, 50, 90, 100])
        result = select_best_model_lc50(data)
        assert "_fitted_params" in result
        assert len(result["_fitted_params"]) == 4

    def test_r_squared_non_negative(self):
        data = _make_substrates([1, 2, 4, 8, 16], [0, 10, 50, 90, 100])
        result = select_best_model_lc50(data)
        r2 = float(result["r_squared"])
        assert r2 >= 0.0

    def test_r_squared_close_to_one_on_clean_sigmoid(self):
        data = _make_substrates([1, 2, 4, 8, 16], [0, 10, 50, 90, 100])
        result = select_best_model_lc50(data)
        assert float(result["r_squared"]) >= 0.90

    def test_insufficient_data_returns_message(self):
        data = _make_substrates([1, 2, 3], [0, 50, 100])
        result = select_best_model_lc50(data)
        assert "not enough" in result["lc50"].lower()

    def test_no_mortality_returns_message(self):
        data = _make_substrates([1, 2, 4, 8], [0, 0, 0, 0])
        result = select_best_model_lc50(data)
        assert "no mortality" in result["lc50"].lower()

    def test_non_substrate_entries_excluded(self):
        data = [
            {"type": "Control",   "x": 0,  "y": 5},
            {"type": "Substrate", "x": 1,  "y": 0},
            {"type": "Substrate", "x": 2,  "y": 10},
            {"type": "Substrate", "x": 4,  "y": 50},
            {"type": "Substrate", "x": 8,  "y": 90},
            {"type": "Substrate", "x": 16, "y": 100},
        ]
        result = select_best_model_lc50(data)
        assert "95% CI" in result["lc50"]

    def test_aic_table_entries_have_required_fields(self):
        data = _make_substrates([1, 2, 4, 8, 16], [0, 10, 50, 90, 100])
        result = select_best_model_lc50(data)
        for entry in result["model_info"]["aic_table"]:
            assert "model" in entry
            assert "k" in entry
            assert "aicc" in entry
            assert "delta" in entry
