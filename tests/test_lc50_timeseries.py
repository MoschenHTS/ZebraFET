"""
test_lc50_timeseries.py — LC50 fitted at every daily timepoint.

Acute fish toxicity is reported as a potency that moves with exposure duration,
which a single-day LC50 cannot express. Refitting each day costs about as much as
the analysed day over again, so the series is computed only on request; what these
tests pin is that every day is fitted on the same terms as the headline result and
that a day the data cannot support is carried rather than dropped.
"""
import pytest

pytest.importorskip("PySide6")

import matplotlib
matplotlib.use("Agg")

from src.core.biostatistics import format_ci_range
from src.ui.widgets.results_analysis_widget import AnalysisWorker

CONCENTRATIONS = [
    {"id": "ctrl", "type": "Control", "value": 0.0},
    {"id": "c1", "type": "Substrate", "value": 0.5},
    {"id": "c2", "type": "Substrate", "value": 1.0},
    {"id": "c3", "type": "Substrate", "value": 2.0},
    {"id": "c4", "type": "Substrate", "value": 4.0},
    {"id": "c5", "type": "Substrate", "value": 8.0},
]
GROUPS = [c["id"] for c in CONCENTRATIONS]
WELLS_PER_GROUP = 10
NUM_DAYS = 4

#: Deaths per group per day — mortality rises with dose and with time, so the
#: LC50 must fall across the series.
DEATHS = {
    1: {"ctrl": 0, "c1": 0, "c2": 0, "c3": 1, "c4": 4, "c5": 9},
    2: {"ctrl": 0, "c1": 0, "c2": 1, "c3": 3, "c4": 7, "c5": 10},
    3: {"ctrl": 0, "c1": 1, "c2": 2, "c3": 5, "c4": 9, "c5": 10},
    4: {"ctrl": 1, "c1": 1, "c2": 4, "c3": 7, "c4": 10, "c5": 10},
}


def _build_inputs():
    obs_rows, layout = [], {"1": {}}
    well_no = 1
    for group in GROUPS:
        for _ in range(WELLS_PER_GROUP):
            well_id = f"A{well_no}"
            layout["1"][well_id] = group
            well_no += 1

    for day in range(1, NUM_DAYS + 1):
        for group in GROUPS:
            wells = [w for w, g in layout["1"].items() if g == group]
            dead = DEATHS[day][group]
            for i, well_id in enumerate(wells):
                obs_rows.append({
                    "day": day, "plate_index": 1, "well_id": well_id,
                    "status": "Dead Embryo" if i < dead else "Live Embryo",
                    "notes": "", "conc_id": group,
                    "conc_type": next(c["type"] for c in CONCENTRATIONS if c["id"] == group),
                    "conc_value": next(c["value"] for c in CONCENTRATIONS if c["id"] == group),
                    "sublethal_conditions": None, "lethal_conditions": None,
                })
    info = {
        "num_days": NUM_DAYS, "num_plates": 1, "concentration_unit": "mg/L",
        "completed_days": list(range(1, NUM_DAYS + 1)),
    }
    return obs_rows, layout, info


def _run(with_timeseries=True, **kwargs):
    obs_rows, layout, info = _build_inputs()
    captured = {}
    worker = AnalysisWorker(
        obs_rows, layout, CONCENTRATIONS, info, str(NUM_DAYS),
        with_timeseries=with_timeseries, **kwargs,
    )
    worker.finished.connect(lambda r: captured.update(r))
    worker.run()
    return captured


@pytest.fixture(scope="module")
def results():
    return _run()


class TestSeriesShape:
    def test_absent_unless_requested(self):
        assert "lc50_timeseries" not in _run(with_timeseries=False)

    def test_one_entry_per_day(self, results):
        assert [e["day"] for e in results["lc50_timeseries"]] == [1, 2, 3, 4]

    def test_hours_track_the_day(self, results):
        assert [e["hpf"] for e in results["lc50_timeseries"]] == [24, 48, 72, 96]

    def test_figure_accompanies_the_series(self, results):
        assert results.get("lc50_timeseries_figure") is not None

    def test_every_entry_reports_its_model(self, results):
        assert all(e["model"] for e in results["lc50_timeseries"])

    def test_dose_group_count_is_recorded(self, results):
        assert all(e["n_groups"] == 5 for e in results["lc50_timeseries"])


class TestSeriesContent:
    def test_lc50_falls_with_exposure_time(self, results):
        """The defining behaviour: longer exposure, lower lethal concentration."""
        values = [e["lc50_numeric"] for e in results["lc50_timeseries"]]
        assert all(v is not None for v in values), values
        assert values[0] > values[-1]

    def test_series_is_monotone_non_increasing(self, results):
        values = [e["lc50_numeric"] for e in results["lc50_timeseries"]]
        assert all(a >= b for a, b in zip(values, values[1:])), values

    def test_final_day_matches_the_headline_lc50(self, results):
        """The series and the analysed day must not disagree about the same day.

        Compared against the reported LC50, not against ``_fitted_params[3]``:
        that parameter is the curve's inflection, which equals the LC50 only when
        both asymptotes are fixed at 0 and 100.
        """
        last = results["lc50_timeseries"][-1]["lc50_numeric"]
        headline = results["lc50_results"]["lc50_numeric"]
        assert last == pytest.approx(headline, rel=1e-6)

    def test_spearman_karber_accompanies_every_day(self, results):
        """Auto-trim is what makes the fallback usable on a real design.

        A FET dose series rarely brackets 0% and 100% mortality exactly, so a
        fixed trim would decline on most days and the estimator would be absent
        precisely where it is wanted.
        """
        assert all(e["tsk_numeric"] is not None for e in results["lc50_timeseries"])

    def test_spearman_karber_tracks_the_fitted_lc50(self, results):
        """Two independent estimators on the same data should broadly agree."""
        for entry in results["lc50_timeseries"]:
            if entry["tsk_numeric"] is None:
                continue
            assert entry["tsk_numeric"] == pytest.approx(entry["lc50_numeric"], rel=0.5)

    def test_fitted_days_are_marked_as_such(self, results):
        assert all(e["status"] == "fitted" for e in results["lc50_timeseries"])


class TestSettingsArePropagated:
    def test_constrained_model_is_used_for_every_day(self):
        results = _run(mode="LL2", bottom=0.0, top=100.0)
        assert all("2PL" in e["model"] for e in results["lc50_timeseries"])

    def test_abbott_setting_reaches_every_day(self):
        """The flag records that the correction was applied, whatever it came to.

        A day with no control deaths still has the correction applied; it simply
        subtracts nothing. What must not happen is the series correcting some
        days and not others.
        """
        assert all(e["abbott_applied"] for e in _run(abbott=True)["lc50_timeseries"])
        assert not any(e["abbott_applied"] for e in _run(abbott=False)["lc50_timeseries"])


class TestUnfittableDay:
    def test_day_without_a_fit_is_carried_with_its_reason(self):
        """An unfittable timepoint and an absent one are different facts."""
        obs_rows, layout, info = _build_inputs()
        # Strip every death from day 1 so no dose-response exists to fit.
        for row in obs_rows:
            if row["day"] == 1:
                row["status"] = "Live Embryo"
        captured = {}
        worker = AnalysisWorker(obs_rows, layout, CONCENTRATIONS, info,
                                str(NUM_DAYS), with_timeseries=True)
        worker.finished.connect(lambda r: captured.update(r))
        worker.run()

        first = captured["lc50_timeseries"][0]
        assert first["day"] == 1
        assert first["lc50_numeric"] is None
        assert first["status"] == "not estimable"
        assert first["message"]

    def test_later_days_still_fit(self):
        obs_rows, layout, info = _build_inputs()
        for row in obs_rows:
            if row["day"] == 1:
                row["status"] = "Live Embryo"
        captured = {}
        worker = AnalysisWorker(obs_rows, layout, CONCENTRATIONS, info,
                                str(NUM_DAYS), with_timeseries=True)
        worker.finished.connect(lambda r: captured.update(r))
        worker.run()
        assert captured["lc50_timeseries"][-1]["lc50_numeric"] is not None


class TestIntervalFormatting:
    def test_interval_narrower_than_reported_precision_says_so(self):
        assert format_ci_range(1.213801, 1.213802) == "narrower than 0.0001"

    def test_distinguishable_bounds_are_shown(self):
        assert format_ci_range(0.7585, 1.2686) == "0.7585 – 1.2686"

    def test_missing_bounds_render_as_a_dash(self):
        assert format_ci_range(None, 1.0) == "—"


class TestPartiallyFinalizedTest:
    """A day in progress must be read as in progress, whichever day is analysed.

    The series is generated mid-experiment as often as at the end: finalize day 2,
    start scoring day 3, then look at how the LC50 has moved. Every day passes
    through the same aggregation as the analysed day, and that aggregation once
    asked whether the *analysed* day was finalized rather than the day in hand —
    so an in-progress day's unscored wells were counted as survivors whenever the
    analysed day happened to be finalized.
    """

    #: Day 3 exists but only the first three wells of each group were scored.
    SCORED_ON_DAY_3 = 3

    def _run(self, analysed_day):
        obs_rows, layout, info = _build_inputs()
        partial = []
        for row in obs_rows:
            if row["day"] < 3:
                partial.append(row)
                continue
            index = int(row["well_id"][1:]) % WELLS_PER_GROUP
            if index < self.SCORED_ON_DAY_3:
                partial.append(row)
        info["completed_days"] = [1, 2]
        captured = {}
        worker = AnalysisWorker(partial, layout, CONCENTRATIONS, info,
                                str(analysed_day), with_timeseries=True)
        worker.finished.connect(lambda r: captured.update(r))
        worker.run()
        return next(e for e in captured["lc50_timeseries"] if e["day"] == 3)

    def test_day_three_is_identical_whichever_day_is_analysed(self):
        assert self._run(analysed_day=2) == self._run(analysed_day=3)

    def test_only_the_scored_wells_reach_the_denominator(self):
        """Nine unscored wells per group must not be read as nine survivors."""
        obs_rows, layout, info = _build_inputs()
        partial = [r for r in obs_rows if r["day"] < 3
                   or int(r["well_id"][1:]) % WELLS_PER_GROUP < self.SCORED_ON_DAY_3]
        info["completed_days"] = [1, 2]
        worker = AnalysisWorker(partial, layout, CONCENTRATIONS, info, "2")
        summary = worker._aggregate_daily_data(
            worker._create_results_dataframe().query("day == 3"), 3
        )
        assert (summary["n_scored"] <= self.SCORED_ON_DAY_3).all()
