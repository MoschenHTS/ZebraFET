"""
test_analysis_worker.py — End-to-end coverage of the analysis pipeline.

AnalysisWorker sits between the database and both the Results tab and the Word
report: it imputes absent wells, aggregates per-group counts, chooses the
denominators, applies Abbott's correction, runs the statistical battery and
renders every figure. Until now none of it was covered — the statistics module
and the report generator were tested from either side of it, but the worker that
feeds them was exercised only by running the GUI.

The worker is a plain QObject, so it runs headlessly without a QApplication;
conftest.py already forces the Agg matplotlib backend.
"""
from collections import Counter

import pandas as pd
import pytest
from matplotlib.figure import Figure

from src.core.biostatistics import (CONTROL_MODE_NEGATIVE, CONTROL_MODE_POOLED,
                                    CONTROL_MODE_SOLVENT)
from src.core.constants import (STATUS_ABSENT, STATUS_DEAD_EMBRYO,
                                STATUS_LIVE_EMBRYO, STATUS_LIVE_HATCHED)
from src.ui.widgets.results_analysis_widget import AnalysisWorker

WELLS_PER_GROUP = 20
DAYS = (1, 2, 3, 4)

#: (group id, conc_type, concentration, cumulative deaths on days 1-4).
#: A clean monotonic dose-response with a 5% control mortality.
STANDARD_DESIGN = [
    ("CTRL", "Control", 0.0, [0, 0, 0, 1]),
    ("C1", "Substrate", 1.0, [0, 1, 1, 2]),
    ("C2", "Substrate", 2.0, [0, 1, 3, 5]),
    ("C3", "Substrate", 4.0, [1, 3, 7, 11]),
    ("C4", "Substrate", 8.0, [2, 8, 14, 18]),
    ("C5", "Substrate", 16.0, [4, 12, 18, 20]),
]

MALFORMATIONS = "Pericardial oedema,Yolk sac oedema"


def build_observations(design=STANDARD_DESIGN, days=DAYS,
                       malformed_groups=("C2", "C3"), all_absent_group=None):
    """Well observations in the shape get_all_well_observations_with_layout returns."""
    rows, layout, well_number = [], {1: {}}, 0
    for group_id, conc_type, conc_value, deaths in design:
        for replicate in range(WELLS_PER_GROUP):
            well_id = f"W{well_number}"
            well_number += 1
            layout[1][well_id] = group_id
            for day in days:
                if all_absent_group == group_id:
                    status = STATUS_ABSENT
                elif replicate < deaths[day - 1]:
                    status = STATUS_DEAD_EMBRYO
                else:
                    status = STATUS_LIVE_HATCHED if day >= 3 else STATUS_LIVE_EMBRYO
                sublethal = (MALFORMATIONS
                             if group_id in malformed_groups
                             and status != STATUS_DEAD_EMBRYO
                             and replicate % 4 == 0 else "")
                rows.append({
                    "day": day, "plate_index": 1, "well_id": well_id,
                    "status": status, "notes": "", "conc_id": group_id,
                    "conc_type": conc_type, "conc_value": conc_value,
                    "sublethal_count": 1 if sublethal else 0,
                    "sublethal_conditions": sublethal, "lethal_conditions": "",
                })
    concentrations = [{"id": g, "type": t, "value": v} for g, t, v, _ in design]
    return rows, layout, concentrations


def run_worker(design=STANDARD_DESIGN, day="4", days=DAYS, **kwargs):
    """Drive AnalysisWorker synchronously and return its emitted results."""
    build_keys = {"malformed_groups", "all_absent_group"}
    rows, layout, concentrations = build_observations(
        design, days, **{k: v for k, v in kwargs.items() if k in build_keys}
    )
    worker = AnalysisWorker(
        rows, layout, concentrations,
        {"concentration_unit": "mg/L", "num_days": len(days)}, day,
        mode=kwargs.get("mode", "LL4"),
        bottom=kwargs.get("bottom"), top=kwargs.get("top"),
        abbott=kwargs.get("abbott", False),
        control_mode=kwargs.get("control_mode", CONTROL_MODE_POOLED),
    )
    results, errors = {}, []
    worker.finished.connect(results.update)
    worker.error.connect(errors.append)
    worker.run()
    assert not errors, f"worker reported an error: {errors}"
    return results


def group(summary_df, conc_id):
    return summary_df.loc[summary_df["conc_id"] == conc_id].iloc[0]


class TestAggregation:
    def test_counts_match_the_design(self):
        summary = run_worker()["summary_df"]
        c4 = group(summary, "C4")
        assert c4["total"] == WELLS_PER_GROUP
        assert c4["dead"] == 18
        assert c4["live"] == 2

    def test_n_scored_is_live_plus_dead(self):
        """The statistical denominator counts embryos observed, not wells laid out."""
        summary = run_worker()["summary_df"]
        for _, row in summary.iterrows():
            assert row["n_scored"] == row["live"] + row["dead"]

    def test_malformations_are_counted_among_survivors_only(self):
        """A dead embryo cannot be scored for morphology, so it is not a denominator."""
        summary = run_worker()["summary_df"]
        for conc_id in ("C2", "C3"):
            row = group(summary, conc_id)
            assert row["malformed"] <= row["live"]

    def test_malformation_details_survive_as_counters(self):
        """Regression: a Counter coerced to a scalar silently empties the chart."""
        summary = run_worker()["summary_df"]
        details = group(summary, "C2")["malformation_details"]
        assert isinstance(details, Counter)
        assert details["Pericardial oedema"] > 0


class TestUnevaluableGroups:
    """A group with wells assigned but nothing scored is not 100% survival."""

    def test_unevaluable_group_is_reported_and_excluded(self):
        results = run_worker(all_absent_group="C3")
        assert "C3" in results["unevaluable_groups"]
        summary = group(results["summary_df"], "C3")
        assert summary["total"] == WELLS_PER_GROUP
        assert summary["n_scored"] == 0

    def test_unevaluable_group_does_not_depress_the_loec(self):
        """Counting C3 as 0% mortality used to push the NOEC up a whole dose."""
        baseline = run_worker()["noec_loec_results"]
        with_absent = run_worker(all_absent_group="C3")["noec_loec_results"]
        assert with_absent["noec_numeric"] == baseline["noec_numeric"]


class TestAbbottCorrection:
    def test_off_by_default(self):
        results = run_worker()
        assert results["abbott_applied"] is False
        assert "mortality_abbott" not in results["summary_df"].columns

    def test_on_discounts_the_background_mortality(self):
        """Abbott's correction removes control mortality, so it lowers the value.

        With a 5% control and 10% observed at C1:
            (10 - 5) / (100 - 5) * 100 = 5.26%
        """
        results = run_worker(abbott=True)
        assert results["abbott_applied"] is True
        summary = results["summary_df"]
        assert "mortality_abbott" in summary.columns

        control_pct = results["control_pct"]
        c1 = group(summary, "C1")
        observed = c1["dead"] / c1["n_scored"] * 100
        expected = (observed - control_pct) / (100 - control_pct) * 100

        assert c1["mortality_abbott"] == pytest.approx(expected)
        assert c1["mortality_abbott"] < observed

    def test_positive_control_is_not_background_corrected(self):
        """It is a sensitivity check, not a dose group measured against control."""
        design = STANDARD_DESIGN + [("PC", "Positive Control", 100.0, [5, 12, 18, 20])]
        summary = run_worker(design, abbott=True)["summary_df"]
        assert pd.isna(group(summary, "PC")["mortality_abbott"])

    def test_uses_the_rigorous_bootstrap_when_counts_are_available(self):
        results = run_worker(abbott=True, mode="auto")
        assert results["lc50_results"].get("bootstrap_method") == "rigorous"


class TestReferenceControlSelection:
    """The reference control is the operator's choice and must reach every stage."""

    DESIGN_WITH_SOLVENT = [
        ("CTRL", "Control", 0.0, [0, 0, 0, 1]),
        ("SC", "Solvent Control", 0.0, [0, 2, 5, 9]),  # solvent is itself toxic
    ] + STANDARD_DESIGN[1:]

    def test_comparison_is_computed_regardless_of_mode(self):
        """The evidence is always present, so the choice is never made blind."""
        for mode in (CONTROL_MODE_POOLED, CONTROL_MODE_NEGATIVE, CONTROL_MODE_SOLVENT):
            results = run_worker(self.DESIGN_WITH_SOLVENT, control_mode=mode)
            comparison = results["control_comparison"]
            assert comparison["applicable"] is True
            assert comparison["p_value"] is not None

    def test_a_toxic_solvent_is_flagged_as_different(self):
        results = run_worker(self.DESIGN_WITH_SOLVENT)
        assert results["control_comparison"]["differ"] is True

    def test_mode_changes_the_background_mortality(self):
        """Pooling a toxic solvent averages its effect into the baseline."""
        negative = run_worker(self.DESIGN_WITH_SOLVENT,
                              control_mode=CONTROL_MODE_NEGATIVE)["control_pct"]
        pooled = run_worker(self.DESIGN_WITH_SOLVENT,
                            control_mode=CONTROL_MODE_POOLED)["control_pct"]
        solvent = run_worker(self.DESIGN_WITH_SOLVENT,
                             control_mode=CONTROL_MODE_SOLVENT)["control_pct"]
        assert negative < pooled < solvent

    def test_mode_is_published_for_the_report(self):
        results = run_worker(self.DESIGN_WITH_SOLVENT, control_mode=CONTROL_MODE_SOLVENT)
        assert results["control_mode"] == CONTROL_MODE_SOLVENT

    def test_comparison_is_inapplicable_without_a_solvent_control(self):
        results = run_worker()
        assert results["control_comparison"]["applicable"] is False


class TestFigures:
    FIGURE_KEYS = ("mortality_plot_figure", "timecourse_plot_figure",
                   "fate_plot_figure", "hatching_plot_figure",
                   "malformation_plot_figure")

    @pytest.mark.parametrize("key", FIGURE_KEYS)
    def test_figure_is_produced_and_renders(self, key, tmp_path):
        figure = run_worker()[key]
        assert isinstance(figure, Figure)
        output = tmp_path / f"{key}.png"
        figure.savefig(output, format="png", dpi=72)
        assert output.stat().st_size > 0

    def test_figures_render_when_nothing_was_malformed(self):
        """The malformation chart must degrade to an empty plot, not raise."""
        results = run_worker(malformed_groups=())
        for key in self.FIGURE_KEYS:
            assert isinstance(results[key], Figure)


class TestTimepointsAndEdges:
    def test_analysis_day_is_published(self):
        """The report renders every hpf value from this."""
        results = run_worker(day="2")
        assert results["analysis_day"] == 2
        assert results["num_days"] == 4

    def test_shorter_test_reports_its_own_duration(self):
        results = run_worker(days=(1, 2, 3), day="3")
        assert results["analysis_day"] == 3
        assert results["num_days"] == 3

    def test_day_one_has_little_mortality_and_still_completes(self):
        results = run_worker(day="1")
        assert results["summary_df"]["dead"].sum() < 10
        assert "lc50_results" in results

    def test_no_malformations_yields_no_endpoint_tests(self):
        """Drives the report's decision not to emit a sublethal section."""
        stats = run_worker(malformed_groups=())["sublethal_stats"]
        assert stats["tests"] == []
        assert stats["pooled"]["no_events"] is True


class TestFinalizedDayDenominator:
    """A well with no observation row means different things by day state.

    Versions before day-end materialization wrote a row only when the operator
    *changed* a well, so a well left at its carried-forward live status has no
    row at all. On a finalized day — which declares the day fully observed —
    those wells were examined and are alive. Excluding them discarded most of a
    legacy day: on a real 7-concentration project it moved the day-1 NOEC from
    0.48 to 7.5 mg/L and dropped four groups from the analysis entirely.
    """

    def _sparse(self, finalized):
        """Rows for day 1 with the survivors' rows omitted, as a legacy day."""
        rows, layout, concentrations = build_observations(days=(1,))
        kept = [r for r in rows if r["status"] == STATUS_DEAD_EMBRYO]
        info = {"concentration_unit": "mg/L", "num_days": 4,
                "completed_days": [1] if finalized else []}
        worker = AnalysisWorker(kept, layout, concentrations, info, "1")
        results = {}
        worker.finished.connect(results.update)
        worker.run()
        return results

    def test_finalized_day_counts_wells_without_a_row(self):
        summary = self._sparse(finalized=True)["summary_df"]
        for _, row in summary.iterrows():
            assert row["n_scored"] == row["total"]

    def test_unfinalized_day_still_excludes_unscored_wells(self):
        """An in-progress day must not read unscored wells as survivors."""
        results = self._sparse(finalized=False)
        summary = results["summary_df"]
        assert (summary["n_scored"] < summary["total"]).any()
        assert results["unevaluable_groups"]

    def test_finalized_day_restores_the_dose_response(self):
        finalized = self._sparse(finalized=True)
        assert finalized["unevaluable_groups"] == []
        # Mortality is still read from the rows that exist, not invented.
        summary = finalized["summary_df"]
        assert summary["dead"].sum() > 0

    def test_rows_present_but_unscoreable_remain_excluded_when_finalized(self):
        """The relaxation applies to missing rows, not to unscoreable ones.

        An all-Absent group carries a row for every well: it was examined and
        could not be assessed. Finalizing the day must not convert it into a
        group of survivors, which would reintroduce the 0%-mortality reading
        this exclusion exists to prevent.
        """
        rows, layout, concentrations = build_observations(all_absent_group="C3")
        info = {"concentration_unit": "mg/L", "num_days": 4,
                "completed_days": [1, 2, 3, 4]}
        worker = AnalysisWorker(rows, layout, concentrations, info, "4")
        results = {}
        worker.finished.connect(results.update)
        worker.run()

        assert "C3" in results["unevaluable_groups"]
        assert group(results["summary_df"], "C3")["n_scored"] == 0
        # Every other group is unaffected by the exclusion.
        assert group(results["summary_df"], "C4")["n_scored"] == WELLS_PER_GROUP


class TestDayStateIsReadPerDay:
    """Each day is aggregated against its own finalization state, not the analysed day's.

    Finalizing a day licenses counting a well with no observation row as an
    unchanged survivor. That relaxation is correct for the day it was granted to
    and wrong for every other, but the check consulted `self.day_selection`
    regardless of which day was being aggregated. Once the LC50 time-series began
    routing every day through the same aggregation, an in-progress day analysed
    alongside a finalized one had its unscored wells silently counted as alive —
    understating its mortality and fabricating a dose-response for a day that had
    barely been scored.
    """

    def _sparse_day_one(self, completed_days):
        """Day 1 rows with the survivors omitted, analysed from day 4."""
        rows, layout, concentrations = build_observations()
        kept = [r for r in rows if r["day"] != 1 or r["status"] == STATUS_DEAD_EMBRYO]
        info = {"concentration_unit": "mg/L", "num_days": 4,
                "completed_days": completed_days}
        worker = AnalysisWorker(kept, layout, concentrations, info, "4",
                                with_timeseries=True)
        results = {}
        worker.finished.connect(results.update)
        worker.run()
        return next(e for e in results["lc50_timeseries"] if e["day"] == 1)

    def test_unfinalized_day_is_not_rescued_by_the_analysed_day(self):
        """Day 1 is not finalized here; analysing finalized day 4 must not change that."""
        assert self._sparse_day_one(completed_days=[4]) == self._sparse_day_one(
            completed_days=[]
        )

    def test_the_unfinalized_day_stays_unfittable(self):
        entry = self._sparse_day_one(completed_days=[4])
        assert entry["lc50_numeric"] is None
        assert entry["status"] == "not estimable"

    def test_a_genuinely_finalized_day_still_counts_its_missing_rows(self):
        """The relaxation must survive the fix wherever it legitimately applies.

        The discriminator is the group count, not the LC50: with the missing rows
        counted, day 1 has all five dose groups and a curve is fitted. That curve
        tops out at ~26% mortality, so it has no LC50 within the tested range —
        which is a statement about the compound at 24 h, not about the data being
        unusable, and the status distinguishes the two.
        """
        entry = self._sparse_day_one(completed_days=[1, 4])
        assert entry["n_groups"] == 5
        assert entry["status"] == "lc50 not reached"
        assert entry["lc50_numeric"] is None


class TestMortalityFigureMatchesTheReportedFit:
    """The curve is drawn from the analysis's parameters, or not at all.

    The figure goes into the Word report beside the LC50 text, so a curve the
    statistics declined to fit is an assertion the report is not entitled to make.
    Deciding this by searching the LC50 message for "failed"/"Not enough"/"error"
    missed the other ways the fit declines, and a private fallback curve_fit then
    drew a 4PL through data the analysis had refused to model.
    """

    def _fit_labels(self, figure):
        return [str(line.get_label()) for line in figure.axes[0].get_lines()
                if "PL" in str(line.get_label()) or "Fit" in str(line.get_label())]

    def test_flat_response_draws_no_curve(self):
        """Identical mortality at every dose: the analysis declines, so must the figure."""
        design = [("CTRL", "Control", 0.0, [0, 0, 0, 0])] + [
            (f"C{i}", "Substrate", float(2 ** i), [4, 4, 4, 4]) for i in range(1, 6)
        ]
        results = run_worker(design=design)
        assert not results["lc50_results"].get("_fitted_params")
        assert self._fit_labels(results["mortality_plot_figure"]) == []

    def test_three_group_constrained_fit_does_draw_its_curve(self):
        """A 2PL needs two dose groups, and its LC50 is reported — so plot it."""
        design = [
            ("CTRL", "Control", 0.0, [0, 0, 0, 0]),
            ("C1", "Substrate", 1.0, [2, 2, 2, 2]),
            ("C2", "Substrate", 2.0, [10, 10, 10, 10]),
            ("C3", "Substrate", 4.0, [18, 18, 18, 18]),
        ]
        results = run_worker(design=design, mode="LL2", bottom=0.0, top=100.0)
        assert results["lc50_results"].get("_fitted_params")
        assert self._fit_labels(results["mortality_plot_figure"])

    def test_the_drawn_curve_is_labelled_with_the_reported_model(self):
        results = run_worker()
        expected = results["lc50_results"]["model_info"]["display_name"]
        assert expected in self._fit_labels(results["mortality_plot_figure"])


class TestInternalPlateControls:
    """OECD TG 236 §23 rejects a plate with more than one dead internal control.

    ZebraFET reports such plates and excludes nothing: rejecting a plate withdraws
    whichever concentrations it carried from the LC50, which is a judgement about
    the study rather than an arithmetic consequence of it.
    """

    WELLS = 4

    def _run(self, deaths_per_plate, per_plate=True):
        layout, rows, concentrations = {}, [], [
            {"id": "ctrl", "type": "Control", "value": 0.0, "per_plate": per_plate},
            {"id": "C1", "type": "Substrate", "value": 1.0, "per_plate": False},
            {"id": "C2", "type": "Substrate", "value": 2.0, "per_plate": False},
        ]
        for plate, control_deaths in enumerate(deaths_per_plate, start=1):
            layout[plate] = {}
            for i in range(self.WELLS):
                well = f"A{i + 1}"
                layout[plate][well] = "ctrl"
                rows.append({
                    "day": 1, "plate_index": plate, "well_id": well, "conc_id": "ctrl",
                    "conc_type": "Control", "conc_value": 0.0, "notes": "",
                    "status": STATUS_DEAD_EMBRYO if i < control_deaths else STATUS_LIVE_EMBRYO,
                    "sublethal_conditions": None, "lethal_conditions": None,
                })
            for g, (group, deaths) in enumerate((("C1", 1), ("C2", 3))):
                for i in range(self.WELLS):
                    well = f"{chr(66 + g)}{i + 1}"
                    layout[plate][well] = group
                    rows.append({
                        "day": 1, "plate_index": plate, "well_id": well, "conc_id": group,
                        "conc_type": "Substrate", "conc_value": float(g + 1), "notes": "",
                        "status": STATUS_DEAD_EMBRYO if i < deaths else STATUS_LIVE_EMBRYO,
                        "sublethal_conditions": None, "lethal_conditions": None,
                    })
        info = {"concentration_unit": "mg/L", "num_days": 1, "completed_days": [1]}
        worker = AnalysisWorker(rows, layout, concentrations, info, "1")
        results = {}
        worker.finished.connect(results.update)
        worker.run()
        return results

    def test_one_death_is_within_the_guideline(self):
        assert self._run([0, 1])["plate_control_failures"] == []

    def test_two_deaths_names_the_offending_plate(self):
        failures = self._run([0, 2])["plate_control_failures"]
        assert [f["plate"] for f in failures] == [2]
        assert failures[0]["dead"] == 2

    def test_every_offending_plate_is_named(self):
        failures = self._run([2, 3])["plate_control_failures"]
        assert [f["plate"] for f in failures] == [1, 2]

    def test_a_control_that_is_not_per_plate_is_not_an_internal_control(self):
        """The flag is what makes a control group the plate's internal control."""
        assert self._run([2, 2], per_plate=False)["plate_control_failures"] == []

    def test_the_failing_plate_is_still_included_in_the_endpoints(self):
        """Detection must not silently change the numbers it reports on."""
        flagged = self._run([0, 2])
        clean = self._run([0, 1])
        assert flagged["plate_control_failures"]
        assert group(flagged["summary_df"], "C2")["dead"] == \
               group(clean["summary_df"], "C2")["dead"]
