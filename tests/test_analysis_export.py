"""
test_analysis_export.py — The computed analysis leaves the application intact.

The raw-data export carries the analysis's input, one row per observation.
Everything derived from it had to be read off the screen and retyped to reach a
statistics package. These tables are those results in the shape they were
computed, so the numbers in a manuscript can be traced back to a file.
"""
import os

import pandas as pd
import pytest

from src.export.analysis_export import export_analysis

PROJECT = {"concentration_unit": "mg/L"}


@pytest.fixture
def results():
    summary = pd.DataFrame([
        {"conc_id": "ctrl", "conc_type": "Control", "conc_value": 0.0,
         "total": 20, "n_scored": 20, "live": 20, "dead": 0, "hatched": 12, "malformed": 1},
        {"conc_id": "c1", "conc_type": "Substrate", "conc_value": 1.0,
         "total": 20, "n_scored": 20, "live": 18, "dead": 2, "hatched": 10, "malformed": 3},
        {"conc_id": "c2", "conc_type": "Substrate", "conc_value": 2.0,
         "total": 20, "n_scored": 20, "live": 8, "dead": 12, "hatched": 4, "malformed": 6},
        # A group killed outright. Real FET series reach this at the top dose, and
        # it leaves no survivors for the malformation percentage to divide by.
        {"conc_id": "c3", "conc_type": "Substrate", "conc_value": 4.0,
         "total": 20, "n_scored": 20, "live": 0, "dead": 20, "hatched": 0, "malformed": 0},
    ])
    return {
        "analysis_day": 4,
        "summary_df": summary,
        "control_mode": "pooled",
        "control_pct": 0.0,
        "abbott_applied": False,
        "unevaluable_groups": [],
        "lc50_results": {
            "lc50": "1.8 (…)", "lc50_numeric": 1.8, "slope": "-2.1", "r_squared": "0.98",
            "weighting": "binomial", "bootstrap_method": "case-resampling",
            "bootstrap_resamples": 500,
            "ci_low": 1.5, "ci_high": 2.2,
            # 2PL, so the inflection and the absolute LC50 coincide at 1.8.
            "_fitted_params": [0.0, 100.0, -2.1, 1.8],
            "model_info": {"display_name": "2PL (bottom=0.0%, top=100.0%)"},
        },
        "tsk_results": {"lc50_numeric": 1.75, "ci_low": 1.4, "ci_high": 2.1, "trim": 0.1},
        "noec_loec_results": {
            "noec_numeric": 1.0, "loec_numeric": 2.0, "correction_label": "Holm-Bonferroni",
        },
        "trend_results": {"statistic": "3.9", "p_value": "4e-05", "trend": "Significant increasing dose-response trend."},
        "sublethal_stats": {
            "tests": [
                {"endpoint": "Pericardial oedema", "conc_id": "c1", "conc_value": 1.0,
                 "k": 2, "n": 18, "p_raw": 0.4, "p_adj": 0.6, "or": 2.1, "or_lo": 0.3, "or_hi": 14.0},
                {"endpoint": "Pericardial oedema", "conc_id": "c2", "conc_value": 2.0,
                 "k": 5, "n": 8, "p_raw": 0.002, "p_adj": 0.008, "or": 30.0, "or_lo": 3.0, "or_hi": 300.0},
            ],
            "pooled": {"noec_numeric": 1.0, "loec_numeric": 2.0},
        },
        "teratogenic_index": {"ec50_numeric": 1.6, "ti_numeric": 1.12},
    }


@pytest.fixture
def with_series(results):
    # A copy, so a test may take both fixtures and still have one without the series.
    results = dict(results)
    results["lc50_timeseries"] = [
        {"day": 1, "hpf": 24, "lc50_numeric": 3.0, "ci_low": 2.4, "ci_high": 3.9,
         "model": "2PL", "slope": "-2.0", "r_squared": "0.97", "n_groups": 2,
         "tsk_numeric": 2.9, "tsk_ci_low": 2.2, "tsk_ci_high": 3.6,
         "status": "fitted", "message": ""},
        {"day": 4, "hpf": 96, "lc50_numeric": None, "ci_low": None, "ci_high": None,
         "model": "2PL", "slope": None, "r_squared": None, "n_groups": 2,
         "tsk_numeric": None, "tsk_ci_low": None, "tsk_ci_high": None,
         "status": "not estimable", "message": "Curve fitting failed."},
    ]
    return results


def _read(out_dir, name):
    return pd.read_csv(os.path.join(out_dir, name))


class TestWhatGetsWritten:
    def test_core_tables_are_produced(self, tmp_path, results):
        written = export_analysis(results, PROJECT, str(tmp_path / "out"))
        assert set(written) == {
            "summary.csv", "endpoints.csv", "sublethal_tests.csv", "effect_sizes.csv"
        }

    def test_timeseries_appears_only_once_computed(self, tmp_path, results, with_series):
        assert "lc50_timeseries.csv" not in export_analysis(results, PROJECT, str(tmp_path / "a"))
        assert "lc50_timeseries.csv" in export_analysis(with_series, PROJECT, str(tmp_path / "b"))

    def test_empty_tables_are_not_written(self, tmp_path):
        """A folder of empty files would misrepresent what was computed."""
        written = export_analysis({"summary_df": pd.DataFrame()}, PROJECT, str(tmp_path / "out"))
        assert written == ["endpoints.csv"]

    def test_directory_is_created(self, tmp_path, results):
        out = tmp_path / "nested" / "run"
        export_analysis(results, PROJECT, str(out))
        assert out.is_dir()

    def test_files_are_utf8_with_bom_for_excel(self, tmp_path, results):
        out = tmp_path / "out"
        export_analysis(results, PROJECT, str(out))
        assert (out / "summary.csv").read_bytes().startswith(b"\xef\xbb\xbf")


class TestSummary:
    def test_one_row_per_group(self, tmp_path, results):
        export_analysis(results, PROJECT, str(tmp_path / "out"))
        assert len(_read(tmp_path / "out", "summary.csv")) == 4

    def test_a_group_with_no_survivors_exports_cleanly(self, tmp_path, results):
        """100% mortality leaves nothing to express malformation as a share of."""
        export_analysis(results, PROJECT, str(tmp_path / "out"))
        df = _read(tmp_path / "out", "summary.csv").set_index("group_id")
        assert df.loc["c3", "mortality_pct"] == pytest.approx(100.0)
        assert pd.isna(df.loc["c3", "malformed_pct"])

    def test_mortality_matches_scored_embryos(self, tmp_path, results):
        export_analysis(results, PROJECT, str(tmp_path / "out"))
        df = _read(tmp_path / "out", "summary.csv").set_index("group_id")
        assert df.loc["c2", "mortality_pct"] == pytest.approx(60.0)

    def test_malformation_is_per_survivor(self, tmp_path, results):
        """6 malformed among 8 survivors, not among 20 assigned."""
        export_analysis(results, PROJECT, str(tmp_path / "out"))
        df = _read(tmp_path / "out", "summary.csv").set_index("group_id")
        assert df.loc["c2", "malformed_pct"] == pytest.approx(75.0)

    def test_rows_are_ordered_by_concentration(self, tmp_path, results):
        export_analysis(results, PROJECT, str(tmp_path / "out"))
        values = _read(tmp_path / "out", "summary.csv")["concentration"].tolist()
        assert values == sorted(values)


class TestEndpoints:
    def _endpoints(self, tmp_path, results):
        export_analysis(results, PROJECT, str(tmp_path / "out"))
        return _read(tmp_path / "out", "endpoints.csv").set_index("endpoint")["value"]

    def test_lc50_is_the_fitted_value(self, tmp_path, results):
        assert float(self._endpoints(tmp_path, results)["LC50"]) == pytest.approx(1.8)

    def test_spearman_karber_is_carried(self, tmp_path, results):
        series = self._endpoints(tmp_path, results)
        assert float(series["LC50 (Spearman-Karber)"]) == pytest.approx(1.75)
        assert float(series["Spearman-Karber trim"]) == pytest.approx(0.1)

    def test_noec_loec_and_correction_are_recorded(self, tmp_path, results):
        series = self._endpoints(tmp_path, results)
        assert float(series["NOEC"]) == pytest.approx(1.0)
        assert float(series["LOEC"]) == pytest.approx(2.0)
        assert series["Multiplicity correction"] == "Holm-Bonferroni"

    def test_provenance_settings_are_recorded(self, tmp_path, results):
        """A table of numbers is not reproducible without how they were produced."""
        series = self._endpoints(tmp_path, results)
        assert series["Curve weighting"] == "binomial"
        assert series["Reference control"] == "pooled"
        assert str(series["Analysis time"]) == "96"

    def test_survives_a_failed_fit(self, tmp_path, results):
        results["lc50_results"] = {"lc50": "Curve fitting failed.", "model_info": {}}
        series = self._endpoints(tmp_path, results)
        assert pd.isna(series["LC50"])


class TestSublethalAndEffectSizes:
    def test_sublethal_rows_carry_adjusted_p(self, tmp_path, results):
        export_analysis(results, PROJECT, str(tmp_path / "out"))
        df = _read(tmp_path / "out", "sublethal_tests.csv")
        assert len(df) == 2
        assert "p_adjusted_bh" in df.columns

    def test_effect_sizes_cover_the_dose_groups_only(self, tmp_path, results):
        """Controls are the comparator, not a row of their own."""
        export_analysis(results, PROJECT, str(tmp_path / "out"))
        df = _read(tmp_path / "out", "effect_sizes.csv")
        assert df["group_id"].tolist() == ["c1", "c2", "c3"]

    def test_wilson_interval_brackets_the_point_estimate(self, tmp_path, results):
        export_analysis(results, PROJECT, str(tmp_path / "out"))
        df = _read(tmp_path / "out", "effect_sizes.csv")
        for _, row in df.iterrows():
            assert row["wilson_ci_lower"] <= row["mortality_pct"] <= row["wilson_ci_upper"]

    def test_no_effect_sizes_without_a_control(self, tmp_path, results):
        results["summary_df"] = results["summary_df"][
            results["summary_df"]["conc_type"] == "Substrate"
        ]
        written = export_analysis(results, PROJECT, str(tmp_path / "out"))
        assert "effect_sizes.csv" not in written


class TestTimeSeriesTable:
    def test_one_row_per_timepoint(self, tmp_path, with_series):
        export_analysis(with_series, PROJECT, str(tmp_path / "out"))
        assert _read(tmp_path / "out", "lc50_timeseries.csv")["hpf"].tolist() == [24, 96]

    def test_unfittable_day_is_kept_with_its_reason(self, tmp_path, with_series):
        export_analysis(with_series, PROJECT, str(tmp_path / "out"))
        df = _read(tmp_path / "out", "lc50_timeseries.csv").set_index("hpf")
        assert pd.isna(df.loc[96, "lc50"])
        assert df.loc[96, "status"] == "not estimable"
        assert df.loc[96, "note"] == "Curve fitting failed."
