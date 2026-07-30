import io
import logging
import os
from collections import Counter
from typing import Dict, Any, List
from datetime import datetime

import numpy as np
import pandas as pd
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             QTableWidget, QTableWidgetItem, QHeaderView, QComboBox,
                             QGroupBox, QTabWidget, QFileDialog, QMessageBox, QDialog,
                             QDialogButtonBox, QFrame, QSizePolicy, QDoubleSpinBox, QCheckBox)
from PySide6.QtCore import Qt, QObject, Signal, QThread
from PySide6.QtGui import QResizeEvent

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
from scipy.stats import t, fisher_exact

from src.core.project_manager import ProjectManager
from src.export.report_generator import ReportGenerator
from src.ui.components import SpinningIcon, LoadingOverlay
from src.ui.plot_style import (apply_style, PUB_COLORS,
                               style_axes_ticks, get_malformation_colors)
from src.core.biostatistics import (logistic_function, calculate_lc50_robust,
                                    select_best_model_lc50, calculate_noec_loec_with_correction,
                                    impute_absent_as_majority, abbott_correct,
                                    pooled_control_mortality_pct, cochran_armitage_trend_test,
                                    wilson_ci, sublethal_endpoint_stats,
                                    calculate_teratogenic_index, format_with_unit,
                                    evaluable_n, select_control_rows,
                                    compare_control_groups, CONTROL_MODE_POOLED,
                                    CONTROL_MODE_NEGATIVE, CONTROL_MODE_SOLVENT,
                                    NOEC_CORRECTION_HOLM, NOEC_CORRECTION_BONFERRONI,
                                    NOEC_CORRECTION_LABELS, NOEC_CORRECTION_SHORT_LABELS,
                                    trimmed_spearman_karber)
from src.core.constants import (
    STATUS_DEAD_EMBRYO as STATUS_EMBRYO_DEAD,
    STATUS_DEAD_HATCHED as STATUS_HATCHED_DEAD,
    STATUS_LIVE_EMBRYO as STATUS_EMBRYO_ALIVE,
    STATUS_LIVE_HATCHED as STATUS_HATCHED_ALIVE,
    STATUS_ABSENT,
    DEAD_STATUSES, LIVE_STATUSES, HATCHED_STATUSES,
)
from src.ui.typography import scaled_pt

log = logging.getLogger(__name__)

apply_style()


#: Outcomes a single day of the LC50 time-series can have.
TIMESERIES_FITTED = "fitted"
TIMESERIES_NOT_REACHED = "lc50 not reached"
TIMESERIES_NOT_ESTIMABLE = "not estimable"


def _timeseries_status(lc50_results: Dict[str, Any]) -> str:
    """Classify one day of the time-series.

    Three outcomes, not two. A day with too few groups to fit is a different
    fact from a day whose fitted curve tops out below 50% mortality: the first
    says nothing about the compound, the second says the compound did not kill
    half the embryos at any tested concentration. Collapsing them would hide an
    early-timepoint dose-response behind the same label as missing data.
    """
    if lc50_results.get("lc50_numeric") is not None:
        return TIMESERIES_FITTED
    if lc50_results.get("_fitted_params"):
        return TIMESERIES_NOT_REACHED
    return TIMESERIES_NOT_ESTIMABLE


class AnalysisWorker(QObject):
    """
    Performs all heavy data processing and plot generation in a separate thread
    to keep the main UI responsive and ensure analytical consistency.

    Supports cooperative cancellation via cancel(): the worker checks
    self._cancelled between each pipeline stage and exits early if set.
    """
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, obs_rows: list, plate_layout: dict, concentrations: list,
                 project_info: dict, day_selection: str,
                 mode: str = "LL4", bottom: float = None, top: float = None,
                 abbott: bool = False, control_mode: str = CONTROL_MODE_POOLED,
                 noec_correction: str = NOEC_CORRECTION_HOLM,
                 with_timeseries: bool = False):
        super().__init__()
        self.obs_rows = obs_rows
        self.plate_layout = plate_layout
        self.concentrations = concentrations
        self.project_info = project_info
        self.day_selection = day_selection
        self.mode = mode
        self.bottom = bottom
        self.top = top
        self.abbott = abbott
        self.control_mode = control_mode
        self.noec_correction = noec_correction
        self.with_timeseries = with_timeseries
        self._cancelled = False

    def cancel(self) -> None:
        """Request early exit.  The worker checks this flag between pipeline stages."""
        self._cancelled = True

    def run(self):
        """The main execution method that will be run in the secondary thread."""
        try:
            results = {}
            df = self._create_results_dataframe()
            if self._cancelled:
                return
            if df.empty:
                self.finished.emit({"empty": True})
                return

            results['full_df'] = df
            day = int(self.day_selection)
            # The report renders every timepoint from this; without it the document
            # falls back to a hard-coded 96 hpf regardless of the test's length.
            results['analysis_day'] = day
            results['num_days'] = self.project_info.get("num_days")
            day_df = df[df['day'] == day]

            if day_df.empty:
                self.finished.emit({"empty": True, "day": day})
                return

            (summary_df, statistical_df, plot_data_mortality, count_data,
             control_counts, abbott_on, control_pct) = self._prepare_day(day_df, day)
            results['summary_df'] = summary_df
            if self._cancelled:
                return

            # A group with wells assigned but nothing scored (never observed, or
            # every well "Absent" with no majority to impute from) has total > 0
            # and n_scored == 0. Left in the statistical frame it reads as 0%
            # mortality and shifts the NOEC/LOEC, the curve fit and the trend
            # test; it is excluded in _prepare_day and still shown in the table.
            results['unevaluable_groups'] = sorted(
                summary_df.loc[
                    (summary_df['n_scored'] <= 0) & (summary_df['total'] > 0), 'conc_id'
                ].astype(str)
            )

            results['plate_control_failures'] = self._internal_plate_control_failures(day_df)

            # The Control-vs-Solvent-Control comparison is computed on every pass,
            # whatever reference is selected, so the operator always has the evidence
            # in front of them when choosing. ZebraFET never switches on its own.
            results['control_comparison'] = compare_control_groups(statistical_df)
            results['control_mode'] = self.control_mode
            results['abbott_applied'] = abbott_on
            results['control_pct'] = control_pct

            if abbott_on:
                # Positive Control is a sensitivity check, not a dose group — do not
                # background-correct it against the negative control.
                def _abbott_row(r):
                    if r["conc_type"] == "Positive Control":
                        return float("nan")
                    scored = evaluable_n(r)
                    obs = (r["dead"] / scored * 100) if scored else 0.0
                    return abbott_correct(obs, control_pct)
                summary_df["mortality_abbott"] = summary_df.apply(_abbott_row, axis=1)

            lc50_results = self._calculate_lc50(plot_data_mortality, count_data, control_counts)
            results['lc50_results'] = lc50_results
            results['tsk_results'] = self._calculate_tsk(
                statistical_df, abbott_on, control_pct
            )
            if self._cancelled:
                return

            results['noec_loec_results'] = self._calculate_noec_loec(statistical_df)
            results['trend_results'] = cochran_armitage_trend_test(statistical_df, self.control_mode)
            results['sublethal_stats'] = sublethal_endpoint_stats(
                statistical_df, control_mode=self.control_mode
            )
            lc50_numeric = lc50_results.get("lc50_numeric")
            # TI is LC50/EC50, so the two fits must be comparable: take the
            # asymptotes actually used for mortality (which under auto-selection
            # are the chosen model's, not the widget's) and match its correction.
            fitted_model = lc50_results.get("model_info") or {}
            results['teratogenic_index'] = calculate_teratogenic_index(
                statistical_df, lc50_numeric,
                bottom=fitted_model.get("bottom"),
                top=fitted_model.get("top"),
                abbott=abbott_on,
                control_mode=self.control_mode,
            )
            if self._cancelled:
                return

            conc_unit = self.project_info.get("concentration_unit", "unit")
            results['mortality_plot_figure'] = self._plot_mortality(plot_data_mortality, lc50_results, conc_unit, abbott_on)
            if self._cancelled:
                return

            results['hatching_plot_figure'] = self._plot_barchart(summary_df, "hatched", "Hatching Rate")
            if self._cancelled:
                return

            results['malformation_plot_figure'] = self._plot_malformation_details(summary_df)
            if self._cancelled:
                return

            results['timecourse_plot_figure'] = self._plot_timecourse(df, conc_unit)
            results['fate_plot_figure'] = self._plot_fate(day_df)
            if self._cancelled:
                return

            # Refitting every day costs roughly as much as the analysed day over
            # again, per day, so the series is computed only when asked for.
            if self.with_timeseries:
                series = self._compute_lc50_timeseries(df)
                if self._cancelled:
                    return
                results['lc50_timeseries'] = series
                results['lc50_timeseries_figure'] = self._plot_lc50_timeseries(
                    series, conc_unit
                )

            self.finished.emit(results)

        except Exception as e:
            log.error(f"Error during analysis in worker thread: {e}", exc_info=True)
            self.error.emit(f"An unexpected error occurred during analysis: {e}")

    def _create_results_dataframe(self) -> pd.DataFrame:
        records = []

        for row in self.obs_rows:
            if not row.get("conc_id"):
                continue
            sublethal_raw = row.get("sublethal_conditions") or ""
            sublethal_list = [s for s in sublethal_raw.split(",") if s] if sublethal_raw else []
            records.append({
                "day": row["day"],
                "plate": row["plate_index"],
                "well": row["well_id"],
                "conc_id": row["conc_id"],
                "conc_type": row.get("conc_type", "N/A"),
                "conc_value": row.get("conc_value", 0),
                "status": row.get("status", STATUS_EMBRYO_ALIVE),
                "sublethal": sublethal_list,
            })

        if not records:
            return pd.DataFrame()

        df = pd.DataFrame(records)
        df['sublethal'] = df['sublethal'].apply(lambda x: x if isinstance(x, list) else [])
        # Impute absent wells as the majority status of their group before scoring
        df = impute_absent_as_majority(df, STATUS_ABSENT, group_cols=("day", "conc_id"))
        df['dead'] = df['status'].isin(DEAD_STATUSES).astype(int)
        df['live'] = df['status'].isin(LIVE_STATUSES).astype(int)
        df['hatched'] = df['status'].isin(HATCHED_STATUSES).astype(int)
        df['malformed'] = df['sublethal'].apply(lambda x: 1 if x else 0)
        return df

    def _aggregate_daily_data(self, day_df: pd.DataFrame, day: int) -> pd.DataFrame:
        """Per-group counts for *day*, whose rows are *day_df*.

        The day is an argument rather than ``self.day_selection`` because this runs
        for the analysed day and again for every day of the LC50 time-series; the
        finalized/not-finalized distinction below is about the day being
        aggregated.
        """
        if day_df.empty: return pd.DataFrame()
        
        grouped_by_id = day_df.groupby('conc_id')
        summary = grouped_by_id.agg(
            dead=('dead', 'sum'),
            hatched=('hatched', 'sum'),
            live=('live', 'sum'),
            observed=('live', 'size'),
        ).reset_index()

        # Malformations are scored among surviving (live) embryos only — a dead
        # embryo cannot be assessed for morphological abnormalities (standard
        # zebrafish teratogenicity practice). Both the malformed count and the
        # per-endpoint Counter are therefore derived from live wells.
        live_df = day_df[day_df['live'] == 1]
        malformed_live = live_df.groupby('conc_id')['malformed'].sum()
        summary['malformed'] = summary['conc_id'].map(malformed_live).fillna(0).astype(int)

        conc_totals = Counter()
        for plate_id, wells in self.plate_layout.items():
            for well_id, conc_id in wells.items():
                conc_totals[conc_id] += 1

        summary['total'] = summary['conc_id'].map(conc_totals).fillna(0).astype(int)

        conc_info = day_df.groupby('conc_id').agg(
            conc_type=('conc_type', 'first'),
            conc_value=('conc_value', 'first')
        ).reset_index()
        summary = pd.merge(summary, conc_info, on='conc_id', how='left')

        # Per-group malformation counts (live wells only) as a plain
        # {conc_id: Counter} mapping. Built explicitly rather than via
        # groupby.apply(...).reset_index(), which some pandas versions expand
        # (a dict/Counter return becomes extra columns), leaving
        # malformation_details as a scalar instead of a Counter — which silently
        # empties both the malformation chart and the sublethal endpoint statistics.
        def safe_counter(series):
            flat_list = [item for sublist in series if isinstance(sublist, list) for item in sublist]
            return Counter(flat_list)

        malf_map = {cid: safe_counter(grp['sublethal']) for cid, grp in live_df.groupby('conc_id')}

        final_summary = summary
        all_conc_ids = pd.DataFrame(list(conc_totals.keys()), columns=['conc_id'])
        final_summary = pd.merge(all_conc_ids, final_summary, on='conc_id', how='left')

        final_summary['total'] = final_summary['conc_id'].map(conc_totals)
        final_summary[['dead', 'hatched', 'live', 'malformed', 'observed']] = \
            final_summary[['dead', 'hatched', 'live', 'malformed', 'observed']].fillna(0)

        concentrations_list = self.concentrations
        if concentrations_list:
            all_conc_info = pd.DataFrame(concentrations_list)[['id', 'type', 'value']].rename(
                columns={'id': 'conc_id', 'type': 'conc_type', 'value': 'conc_value'}
            ).drop_duplicates()
            final_summary = final_summary.drop(columns=['conc_type', 'conc_value'], errors='ignore')
            final_summary = pd.merge(final_summary, all_conc_info, on='conc_id', how='left')

        final_summary = final_summary.fillna(0)

        # Embryos actually scored, as distinct from wells assigned ('total').
        # This is the denominator for every mortality and hatching statistic; see
        # biostatistics.evaluable_n for why 'total' is unsafe for that purpose.
        #
        # A well with no observation row means one of two different things, and
        # whether the day was finalized tells them apart:
        #
        #   - Day not finalized: the well genuinely has not been scored yet, and
        #     must stay out of the denominator rather than read as a survivor.
        #   - Day finalized: the day was declared complete, so every assigned well
        #     was examined. Versions before the day-end materialization model only
        #     wrote a row when the operator *changed* a well, so a well left at the
        #     carried-forward live status has no row at all. Excluding those would
        #     discard most of a legacy finalized day — on a real 7-concentration
        #     project this moved the day-1 NOEC from 0.48 to 7.5 mg/L and dropped
        #     four groups entirely. Such wells are counted as observed and alive,
        #     which is what the absence of a change recorded.
        #
        # Wells that do carry a row but no scoreable status (all "Absent" with no
        # majority to impute from) remain excluded either way: they were examined
        # and could not be assessed.
        unrecorded = (final_summary['total'] - final_summary['observed']).clip(lower=0)
        if self._day_is_finalized(day):
            final_summary['live'] = final_summary['live'] + unrecorded
        final_summary['n_scored'] = final_summary['live'] + final_summary['dead']
        # Assigned last so the Counter objects are not coerced by fillna; groups
        # with no observations map to an empty Counter.
        final_summary['malformation_details'] = final_summary['conc_id'].map(
            lambda cid: malf_map.get(cid, Counter())
        )
        return final_summary

    @staticmethod
    def _prepare_plot_data(summary_df: pd.DataFrame, key: str) -> list:
        plot_data = []
        for _, row in summary_df.iterrows():
            scored = evaluable_n(row)
            if scored > 0:
                value = row.get(key, 0)
                percent = (value / scored) * 100
                plot_data.append({'id': row['conc_id'], 'type': row['conc_type'],
                                  'x': row['conc_value'], 'y': percent,
                                  'k': int(value), 'n': int(scored)})
        return plot_data

    def _day_is_finalized(self, day) -> bool:
        """True when *day* was finalized by the operator.

        Finalizing declares the day fully observed, which is what licenses
        treating a well with no observation row as an unchanged live embryo
        rather than as an unscored one. *day* is required: every caller aggregates
        a specific day, and the LC50 time-series runs this for all of them.
        """
        try:
            completed = self.project_info.get("completed_days") or []
            return int(day) in {int(d) for d in completed}
        except (TypeError, ValueError):
            return False

    #: OECD TG 236 §23 rejects a plate whose internal control exceeds this many deaths.
    _INTERNAL_CONTROL_MAX_DEATHS = 1

    def _internal_plate_control_failures(self, day_df: pd.DataFrame) -> list:
        """Plates whose internal (dilution-water) control exceeded TG 236 §23.

        A dilution-water control laid out on every plate is that plate's internal
        control, which is what the `per_plate` flag records. Solvent controls are
        excluded: §23 specifies dilution water for this role. The guideline rejects
        a plate carrying more than one dead embryo in it, so the analysis names the
        failing plates and leaves the decision to the operator — rejecting a plate
        removes whichever concentrations it held, which is a judgement about the
        study rather than an arithmetic consequence of it.

        Returns one entry per failing plate and group, ascending by plate.
        """
        per_plate_controls = {
            c["id"] for c in self.concentrations or []
            if c.get("per_plate") and c.get("type") == "Control"
        }
        if not per_plate_controls or day_df.empty:
            return []

        wells = day_df[day_df["conc_id"].isin(per_plate_controls)]
        if wells.empty:
            return []

        failures = []
        for (plate, conc_id), group in wells.groupby(["plate", "conc_id"]):
            dead = int(group["dead"].sum())
            if dead > self._INTERNAL_CONTROL_MAX_DEATHS:
                failures.append({
                    "plate": int(plate), "conc_id": str(conc_id),
                    "dead": dead, "n": int(len(group)),
                })
        return sorted(failures, key=lambda f: (f["plate"], f["conc_id"]))

    def _assigned_wells_per_group(self) -> Counter:
        """Wells laid out on the plate per concentration group."""
        totals: Counter = Counter()
        for wells in self.plate_layout.values():
            for conc_id in wells.values():
                totals[conc_id] += 1
        return totals

    def _calculate_lc50(self, plot_data: list, count_data=None, control_counts=None) -> dict:
        if self.mode == "auto":
            return select_best_model_lc50(plot_data, count_data=count_data,
                                          control_counts=control_counts)
        return calculate_lc50_robust(plot_data, bottom=self.bottom, top=self.top,
                                     count_data=count_data, control_counts=control_counts)

    def _prepare_day(self, day_df: pd.DataFrame, day: int):
        """Everything a single day's endpoints are computed from.

        Returns (summary_df, statistical_df, plot_data, count_data,
        control_counts, abbott_on, control_pct). Shared by the analysed day and
        by every day of the time-series so the series cannot be built on a
        different footing from the headline result — which is also why *day* is
        an argument: every day-dependent decision below must be made about the
        day being prepared, not about the analysed one.
        """
        summary_df = self._aggregate_daily_data(day_df, day)
        evaluable = summary_df['n_scored'] > 0
        statistical_df = summary_df[
            (summary_df['conc_type'] != 'Positive Control') & evaluable
        ].copy()

        control_pct = pooled_control_mortality_pct(statistical_df, self.control_mode)
        abbott_on = bool(
            self.abbott and control_pct is not None and control_pct < 100.0
        )

        plot_data = self._prepare_plot_data(statistical_df, "dead")
        count_data = None
        control_counts = None
        if abbott_on:
            plot_data = [
                {**p, "y": abbott_correct(p["y"], control_pct)} for p in plot_data
            ]
            subs = statistical_df[statistical_df["conc_type"] == "Substrate"]
            count_data = [
                {"x": float(r["conc_value"]), "k": int(r["dead"]), "n": int(evaluable_n(r))}
                for _, r in subs.iterrows()
                if evaluable_n(r) > 0 and r["conc_value"] > 0
            ]
            ctrl = select_control_rows(statistical_df, self.control_mode)
            control_counts = (int(ctrl["dead"].sum()), int(evaluable_n(ctrl).sum()))

        return (summary_df, statistical_df, plot_data, count_data,
                control_counts, abbott_on, control_pct)

    def _compute_lc50_timeseries(self, df: pd.DataFrame) -> list:
        """LC50 at each daily timepoint, under the settings of the main analysis.

        A single-day LC50 says how toxic the compound is; the series says how
        that changes with exposure time, which is the form OECD TG 236 results
        are normally presented in. Every day is fitted with the same model,
        asymptotes, correction and reference control as the analysed day, so the
        points are comparable with one another and with the headline value.
        """
        series = []
        num_days = int(self.project_info.get("num_days") or 0)
        for day in range(1, num_days + 1):
            if self._cancelled:
                return series
            day_df = df[df['day'] == day]
            if day_df.empty:
                continue

            (_summary, statistical_df, plot_data, count_data,
             control_counts, abbott_on, control_pct) = self._prepare_day(day_df, day)

            lc50 = self._calculate_lc50(plot_data, count_data, control_counts)
            tsk = self._calculate_tsk(statistical_df, abbott_on, control_pct)
            series.append({
                "day": day,
                "hpf": day * 24,
                "lc50_numeric": lc50.get("lc50_numeric"),
                "ci_low": lc50.get("ci_low"),
                "ci_high": lc50.get("ci_high"),
                "model": lc50.get("model_info", {}).get("display_name", "—"),
                "slope": lc50.get("slope"),
                "r_squared": lc50.get("r_squared"),
                "n_groups": int((statistical_df["conc_type"] == "Substrate").sum()),
                "abbott_applied": abbott_on,
                "tsk_numeric": tsk.get("lc50_numeric"),
                "tsk_ci_low": tsk.get("ci_low"),
                "tsk_ci_high": tsk.get("ci_high"),
                # A day that could not be fitted is carried with its reason rather
                # than dropped: an absent point and an unfittable one are different
                # facts, and only one of them is about the compound. A third case
                # sits between them — the curve fitted but tops out below 50%, so
                # the day has a dose-response and no LC50 within the tested range.
                "status": _timeseries_status(lc50),
                "message": "" if lc50.get("lc50_numeric") is not None else str(lc50.get("lc50", "")),
            })
        return series

    def _calculate_tsk(self, summary_df: pd.DataFrame, abbott_on: bool,
                       control_pct) -> Dict[str, Any]:
        """Non-parametric LC50 for the dose groups in *summary_df*.

        Abbott's correction is carried through when it was applied to the curve
        fit, so the two LC50 estimates are on the same footing; reporting a
        background-corrected model LC50 beside an uncorrected non-parametric one
        would invite the reader to compare quantities that differ by more than
        method. The correction is applied to the proportion and scaled back to a
        count, which is what the estimator consumes.
        """
        subs = summary_df[summary_df["conc_type"] == "Substrate"]
        x, k, n = [], [], []
        for _, row in subs.iterrows():
            scored = evaluable_n(row)
            if scored <= 0 or row["conc_value"] <= 0:
                continue
            dead = float(row["dead"])
            if abbott_on and control_pct is not None:
                dead = abbott_correct(dead / scored * 100.0, control_pct) / 100.0 * scored
            x.append(float(row["conc_value"]))
            k.append(dead)
            n.append(float(scored))
        return trimmed_spearman_karber(x, k, n)

    def _calculate_noec_loec(self, summary_df: pd.DataFrame) -> Dict[str, Any]:
        """Delegate to the standalone biostatistics module."""
        return calculate_noec_loec_with_correction(
            summary_df, self.control_mode, self.noec_correction
        )

    def _plot_mortality(self, plot_data, lc50_results, conc_unit, abbott_on=False) -> Figure:
        fig = Figure(figsize=(3.5, 3.0), layout='constrained')  # single-column width (~89 mm)
        ax = fig.add_subplot(111)

        substrates = [p for p in plot_data if p['type'] == 'Substrate' and p['x'] > 0]
        if substrates:
            x_data = np.array([p['x'] for p in substrates])
            y_data = np.array([p['y'] for p in substrates])
            # Wilson 95% CI error bars on the observed points. Skipped when Abbott
            # correction is on, since the plotted y is background-corrected and no
            # longer a raw binomial proportion.
            if not abbott_on:
                lo_err, hi_err = [], []
                for p in substrates:
                    _c, lo, hi = wilson_ci(p.get('k', 0), p.get('n', 0))
                    lo_err.append(max(0.0, p['y'] - lo))
                    hi_err.append(max(0.0, hi - p['y']))
                ax.errorbar(x_data, y_data, yerr=[lo_err, hi_err], fmt='none',
                            ecolor='black', elinewidth=0.6, capsize=2, zorder=9)
            ax.scatter(x_data, y_data, label="Observed", color=PUB_COLORS['Substrate'],
                       zorder=10, s=30, marker='o', edgecolors='black', linewidths=0.5)

            # Drawn from the parameters the analysis fitted, and only when it
            # fitted some: the figure must not assert a curve the statistics
            # declined to report.
            raw_params = lc50_results.get('_fitted_params')
            if raw_params:
                try:
                    params = np.array(raw_params)

                    min_x, max_x = min(x_data), max(x_data)
                    if min_x > 0 and max_x > 0:
                        x_fit = np.logspace(np.log10(min_x * 0.9), np.log10(max_x * 1.1), 200)
                        y_fit = logistic_function(x_fit, *params)
                        fit_label = lc50_results.get("model_info", {}).get("display_name", "Logistic Fit")
                        ax.plot(x_fit, y_fit, color=PUB_COLORS['Positive Control'],
                                linestyle='-', label=fit_label)
                except Exception as e:
                    log.warning(f"Could not plot logistic fit: {e}")

        control = next((p for p in plot_data if p['type'] == 'Control'), None)
        if control:
            ax.axhline(y=control['y'], color=PUB_COLORS['Control'], linestyle='--',
                       linewidth=1.0, label=f"Negative Control ({control['y']:.1f}%)")

        ax.set_xscale('log')
        ax.set_ylim(-5, 105)
        ax.set_xlabel(f"Concentration ({conc_unit})")
        ax.set_ylabel("Mortality (%)")
        if any([substrates, control]):
            fig.legend(loc='outside lower center', ncol=3, fontsize=8)
        return fig

    def _plot_barchart(self, summary_df, key, title) -> Figure:
        fig = Figure(figsize=(3.5, 3.0), layout='constrained')  # single-column width
        ax = fig.add_subplot(111)

        plot_df = summary_df.copy()
        plot_df['_denominator'] = evaluable_n(plot_df)
        plot_df['percent'] = (
            plot_df[key] / plot_df['_denominator'].replace(0, np.nan) * 100
        ).fillna(0)
        plot_df['color'] = plot_df['conc_type'].map(PUB_COLORS).fillna(PUB_COLORS['Substrate'])
        plot_df = plot_df.sort_values(by='conc_value')

        bars = ax.bar(plot_df['conc_id'], plot_df['percent'],
                      color=plot_df['color'], edgecolor='black', linewidth=0.5)

        # Wilson 95% CI error bars per group
        lo_err, hi_err = [], []
        for _, r in plot_df.iterrows():
            _c, lo, hi = wilson_ci(int(r[key]), int(r['_denominator']))
            lo_err.append(max(0.0, r['percent'] - lo))
            hi_err.append(max(0.0, hi - r['percent']))
        ax.errorbar(range(len(plot_df)), plot_df['percent'], yerr=[lo_err, hi_err],
                    fmt='none', ecolor='black', elinewidth=0.6, capsize=2, zorder=5)

        # Legend: one entry per unique group type present
        from matplotlib.patches import Patch
        seen = {}
        for _, row in plot_df.iterrows():
            ctype = row['conc_type']
            if ctype not in seen:
                seen[ctype] = row['color']
        legend_handles = [Patch(facecolor=col, edgecolor='black', linewidth=0.5, label=lbl)
                          for lbl, col in seen.items()]
        if legend_handles:
            fig.legend(handles=legend_handles, loc='outside lower center', ncol=3, fontsize=8)

        ax.set_ylim(0, 105)
        ax.set_ylabel(f"{title} (%)")
        ax.set_xlabel("Treatment Group")
        style_axes_ticks(ax, rotate_xticks=True)
        return fig

    def _plot_timecourse(self, full_df: pd.DataFrame, conc_unit: str) -> Figure:
        """Cumulative mortality (%) per group across the daily observation
        timepoints (24/48/72/96 hpf)."""
        fig = Figure(figsize=(3.5, 3.0), layout='constrained')
        ax = fig.add_subplot(111)
        if full_df is None or full_df.empty:
            return fig

        # Order groups control-first then ascending concentration
        type_rank = {'Control': 0, 'Solvent Control': 1, 'Substrate': 2, 'Positive Control': 3}
        order = full_df.drop_duplicates('conc_id').copy()
        order['_rank'] = order['conc_type'].map(type_rank).fillna(2)
        order = order.sort_values(['_rank', 'conc_value'])
        group_ids = list(order['conc_id'])
        colors = get_malformation_colors(max(1, len(group_ids)))

        # Denominators follow the same rule as the summary table — embryos scored,
        # with wells left unrecorded on a finalized day counted as survivors — so
        # the curve meets the table's mortality at the analysed day instead of
        # dividing by however many rows happen to exist.
        assigned_per_group = self._assigned_wells_per_group()

        for gid, color in zip(group_ids, colors):
            g = full_df[full_df['conc_id'] == gid]
            per_day = g.groupby('day').agg(
                dead=('dead', 'sum'), live=('live', 'sum'), observed=('dead', 'size'),
            ).reset_index()
            if per_day.empty:
                continue
            assigned = assigned_per_group.get(gid, 0)
            unrecorded = (assigned - per_day['observed']).clip(lower=0)
            finalized = per_day['day'].map(lambda d: self._day_is_finalized(d))
            per_day['n_scored'] = (
                per_day['live'] + per_day['dead'] + unrecorded.where(finalized, 0)
            )
            per_day = per_day[per_day['n_scored'] > 0].sort_values('day')
            if per_day.empty:
                continue
            hpf = per_day['day'] * 24
            pct = per_day['dead'] / per_day['n_scored'] * 100
            ax.plot(hpf, pct, marker='o', markersize=4, linewidth=1.2, color=color, label=str(gid))

        ax.set_ylim(-5, 105)
        ax.set_xlabel("Time (hpf)")
        ax.set_ylabel("Cumulative mortality (%)")
        days = sorted(full_df['day'].unique())
        ax.set_xticks([int(d) * 24 for d in days])
        if group_ids:
            fig.legend(loc='outside lower center', ncol=4, fontsize=7)
        return fig

    def _plot_lc50_timeseries(self, series: list, conc_unit: str) -> Figure:
        """LC50 against exposure time, with its bootstrap interval."""
        fig = Figure(figsize=(3.5, 3.0), layout='constrained')
        ax = fig.add_subplot(111)

        fitted = [e for e in series if e["lc50_numeric"] is not None]
        if not fitted:
            ax.text(0.5, 0.5, "No LC50 could be estimated at any timepoint.",
                    ha='center', va='center', transform=ax.transAxes)
            return fig

        hpf = np.array([e["hpf"] for e in fitted], dtype=float)
        lc50 = np.array([e["lc50_numeric"] for e in fitted], dtype=float)
        # Asymmetric on a log axis, and absent wherever the bootstrap could not
        # produce an interval; those points are drawn without a bar rather than
        # with a bar of zero length, which would imply a precision not measured.
        lo_err, hi_err = [], []
        for entry in fitted:
            low, high = entry.get("ci_low"), entry.get("ci_high")
            value = entry["lc50_numeric"]
            lo_err.append(max(0.0, value - low) if low is not None else 0.0)
            hi_err.append(max(0.0, high - value) if high is not None else 0.0)

        ax.errorbar(hpf, lc50, yerr=[lo_err, hi_err], fmt='none',
                    ecolor='black', elinewidth=0.6, capsize=2, zorder=9)
        ax.plot(hpf, lc50, marker='o', markersize=4, linewidth=1.2,
                color=PUB_COLORS['Substrate'], zorder=10, label="Model LC50")

        tsk = [(e["hpf"], e["tsk_numeric"]) for e in series if e.get("tsk_numeric")]
        if tsk:
            ax.plot([t[0] for t in tsk], [t[1] for t in tsk], marker='s', markersize=3,
                    linewidth=1.0, linestyle='--', color=PUB_COLORS['Control'],
                    label="Spearman-Kärber")

        ax.set_yscale('log')
        ax.set_xlabel("Time (hpf)")
        ax.set_ylabel(f"LC50 ({conc_unit})")
        ax.set_xticks([e["hpf"] for e in series])
        fig.legend(loc='outside lower center', ncol=2, fontsize=8)
        return fig

    def _plot_fate(self, day_df: pd.DataFrame) -> Figure:
        """Stacked fate composition (live embryo / hatched / dead / dead hatched)
        per group at the selected day."""
        fig = Figure(figsize=(3.5, 3.0), layout='constrained')
        ax = fig.add_subplot(111)
        if day_df is None or day_df.empty:
            return fig

        fate_order = [
            (STATUS_EMBRYO_ALIVE,  "Live embryo",  PUB_COLORS.get('Control', '#4d4d4d')),
            (STATUS_HATCHED_ALIVE, "Live hatched", PUB_COLORS.get('Substrate', '#2166ac')),
            (STATUS_EMBRYO_DEAD,   "Dead embryo",  '#b2182b'),
            (STATUS_HATCHED_DEAD,  "Dead hatched", '#f4a582'),
        ]
        # A well that stayed "Absent" with no majority to impute from belongs to
        # none of the four fates. Without a segment of its own it left the bar
        # short of 100% with nothing to explain the gap.
        _UNSCORED = "__unscored__"
        fate_order.append((_UNSCORED, "Not scored", '#d9d9d9'))

        groups = (day_df.drop_duplicates('conc_id').sort_values('conc_value')['conc_id'].tolist())
        counts = {st: [] for st, _, _ in fate_order}
        totals = []
        for gid in groups:
            g = day_df[day_df['conc_id'] == gid]
            n = len(g)
            totals.append(n)
            vc = g['status'].value_counts()
            scored = 0
            for st, _, _ in fate_order:
                if st == _UNSCORED:
                    continue
                value = vc.get(st, 0)
                scored += value
                counts[st].append(value)
            counts[_UNSCORED].append(max(0, n - scored))

        import numpy as _np
        bottoms = _np.zeros(len(groups))
        totals_arr = _np.array(totals, dtype=float)
        totals_arr[totals_arr == 0] = _np.nan
        for st, label, color in fate_order:
            # Carrying an always-empty "Not scored" entry into the legend of every
            # complete plate would be noise; it appears only when it has a value.
            if st == _UNSCORED and not any(counts[st]):
                continue
            pct = _np.array(counts[st], dtype=float) / totals_arr * 100
            pct = _np.nan_to_num(pct)
            ax.bar(range(len(groups)), pct, bottom=bottoms, color=color,
                   edgecolor='black', linewidth=0.4, label=label)
            bottoms += pct

        ax.set_xticks(range(len(groups)))
        ax.set_xticklabels(groups)
        ax.set_ylim(0, 105)
        ax.set_ylabel("Composition (%)")
        ax.set_xlabel("Treatment Group")
        style_axes_ticks(ax, rotate_xticks=True)
        fig.legend(loc='outside lower center', ncol=4, fontsize=7)
        return fig

    def _plot_malformation_details(self, summary_df: pd.DataFrame) -> Figure:
        fig = Figure(figsize=(5.0, 3.5), layout='constrained')  # wider to accommodate legend
        ax = fig.add_subplot(111)

        malf_data = {
            row['conc_id']: row['malformation_details']
            for _, row in summary_df.iterrows()
            if isinstance(row['malformation_details'], Counter) and row['malformation_details']
        }

        if not malf_data:
            ax.text(0.5, 0.5, "No malformations recorded for this day.",
                    ha='center', va='center', transform=ax.transAxes)
            return fig

        all_malformations = sorted(list(set(m for details in malf_data.values() for m in details.keys())))

        group_order = summary_df.sort_values('conc_value')['conc_id'].unique()
        group_labels = [g for g in group_order if g in malf_data]

        has_live = 'live' in summary_df.columns
        df_data = {}
        for malf in all_malformations:
            percentages = []
            for group in group_labels:
                grow = summary_df.loc[summary_df['conc_id'] == group]
                # Malformation incidence is expressed per surviving embryo.
                denom = grow['live'].iloc[0] if has_live else grow['total'].iloc[0]
                count = malf_data.get(group, {}).get(malf, 0)
                percentages.append((count / denom) * 100 if denom > 0 else 0)
            df_data[malf] = percentages

        plot_df = pd.DataFrame(df_data, index=group_labels)

        n_malfs = len(all_malformations)
        colors = get_malformation_colors(n_malfs)
        plot_df.plot(kind='bar', stacked=True, ax=ax, color=colors, edgecolor='black', linewidth=0.4)

        ax.set_ylim(0, 105)
        ax.set_ylabel("Incidence (%)")
        ax.set_xlabel("Treatment Group")
        ax.legend(title="Sublethal endpoint", bbox_to_anchor=(1.02, 1), loc='upper left',
                  fontsize=8, title_fontsize=8)
        style_axes_ticks(ax, rotate_xticks=True)
        return fig


class ReportWorker(QObject):
    finished = Signal(bool, str)

    def __init__(self, snapshot: dict, project_dir: str, file_path: str,
                 analysis_results: Dict, sections=None):
        super().__init__()
        self.snapshot = snapshot
        self.project_dir = project_dir
        self.file_path = file_path
        self.analysis_results = analysis_results
        self.sections = sections

    def run(self):
        try:
            generator = ReportGenerator(self.snapshot, self.project_dir,
                                        self.analysis_results, sections=self.sections)
            success = generator.generate_report(self.file_path)
            message = self.file_path if success else "An unknown error occurred during report generation."
            self.finished.emit(success, message)
        except Exception as e:
            log.error(f"Error in ReportWorker: {e}", exc_info=True)
            self.finished.emit(False, str(e))


class ResultsAnalysisWidget(QWidget):
    """
    Main widget for analyzing, visualizing, and exporting FET test results.
    """
    #: Text for the main window status bar, for actions that otherwise succeed silently.
    status_message = Signal(str)

    #: Summary table columns. Both denominators are shown: "N assigned" is the wells
    #: laid out on the plate, "N scored" is the embryos actually observed and the
    #: denominator behind every percentage in this table. _populate_table inserts
    #: "Abbott Mort. (%)" between the two halves when the correction applies; the
    #: table is built from these same lists before the first analysis so the headers
    #: on an empty table match the ones the results arrive under.
    SUMMARY_HEADERS_LEAD = ["ID", "Type", "N assigned", "N scored", "Dead", "Mortality (%)"]
    SUMMARY_HEADERS_TAIL = ["Hatched (%)", "Malformed (%)"]

    def __init__(self, manager: ProjectManager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.analysis_thread = None
        self.analysis_worker = None
        self.report_thread = None
        self.report_worker = None
        self.analysis_results = {}
        self._results_dirty = True  # start dirty so first visit auto-runs analysis
        self._init_ui()
        self._clear_results()

    def shutdown(self) -> None:
        """Stop all background threads. Call before deleteLater() to avoid use-after-free."""
        if self.analysis_worker is not None:
            self.analysis_worker.cancel()
        try:
            if self.analysis_thread is not None and self.analysis_thread.isRunning():
                self.analysis_thread.quit()
                self.analysis_thread.wait(2000)
        except RuntimeError:
            pass  # C++ object already deleted by deleteLater on finished signal
        self.analysis_thread = None
        try:
            if self.report_thread is not None and self.report_thread.isRunning():
                self.report_thread.quit()
                self.report_thread.wait(2000)
        except RuntimeError:
            pass
        self.report_thread = None

    def mark_dirty(self) -> None:
        """Mark analysis as stale.  Called whenever well data changes."""
        self._results_dirty = True

    def showEvent(self, event) -> None:
        """Auto-run analysis when this tab becomes visible and data has changed."""
        super().showEvent(event)
        if self._results_dirty:
            self._results_dirty = False
            self.run_analysis()
        
    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        self.control_layout = QHBoxLayout()
        self.day_selector = QComboBox()
        self.day_selector.setMinimumWidth(150)
        num_days = self.manager.get_project_info().get("num_days", 1)
        for i in range(1, num_days + 1):
            self.day_selector.addItem(f"Day {i}", str(i))
        if num_days > 0:
            self.day_selector.setCurrentIndex(num_days - 1)
        self.day_selector.currentIndexChanged.connect(self.run_analysis)
        
        self.recalculate_btn = QPushButton("Recalculate")
        self.recalculate_btn.clicked.connect(self.run_analysis)

        self.timeseries_btn = QPushButton("LC50 Time-Series")
        self.timeseries_btn.setToolTip(
            "Fit the LC50 at every daily timepoint. Each day costs about as much "
            "as one analysis, so this is computed only on request."
        )
        self.timeseries_btn.clicked.connect(self.run_timeseries)
        
        self.export_btn = QPushButton("Export Results")
        self.export_btn.clicked.connect(self._export_results)

        self.export_fig_btn = QPushButton("Export Figure")
        self.export_fig_btn.setToolTip("Save the figure in the current plot tab (PNG, SVG or PDF, 600 dpi).")
        self.export_fig_btn.clicked.connect(self._export_current_figure)
        self.copy_fig_btn = QPushButton("Copy Figure")
        self.copy_fig_btn.setToolTip("Copy the figure in the current plot tab to the clipboard.")
        self.copy_fig_btn.clicked.connect(self._copy_current_figure)

        self.control_layout.addWidget(QLabel("Analysis Day:"))
        self.control_layout.addWidget(self.day_selector)
        self.control_layout.addStretch()
        self.control_layout.addWidget(self.copy_fig_btn)
        self.control_layout.addWidget(self.export_fig_btn)
        self.control_layout.addWidget(self.timeseries_btn)
        self.control_layout.addWidget(self.recalculate_btn)
        self.control_layout.addWidget(self.export_btn)
        main_layout.addLayout(self.control_layout)

        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Model:"))
        self.model_combo = QComboBox()
        self.model_combo.addItem("Auto-select (AICc)", "auto")
        self.model_combo.addItem("4PL — all free", "LL4")
        self.model_combo.addItem("3PL — fix bottom", "LL3_bottom")
        self.model_combo.addItem("3PL — fix top", "LL3_top")
        self.model_combo.addItem("2PL — fix both", "LL2")
        model_row.addWidget(self.model_combo)
        model_row.addSpacing(16)
        self.bottom_label = QLabel("Bottom:")
        self.bottom_spin = QDoubleSpinBox()
        self.bottom_spin.setRange(0.0, 100.0)
        self.bottom_spin.setSingleStep(1.0)
        self.bottom_spin.setDecimals(1)
        self.bottom_spin.setSuffix(" %")
        self.bottom_spin.setValue(0.0)
        self.top_label = QLabel("Top:")
        self.top_spin = QDoubleSpinBox()
        self.top_spin.setRange(0.0, 100.0)
        self.top_spin.setSingleStep(1.0)
        self.top_spin.setDecimals(1)
        self.top_spin.setSuffix(" %")
        self.top_spin.setValue(100.0)
        model_row.addWidget(self.bottom_label)
        model_row.addWidget(self.bottom_spin)
        model_row.addSpacing(8)
        model_row.addWidget(self.top_label)
        model_row.addWidget(self.top_spin)
        model_row.addSpacing(16)
        self.abbott_check = QCheckBox("Abbott's correction")
        self.abbott_check.setToolTip(
            "Adjust observed mortality for control (background) mortality before curve fitting."
        )
        model_row.addWidget(self.abbott_check)
        model_row.addStretch()
        main_layout.addLayout(model_row)

        # Reference control. Which control the dose groups are compared against is
        # a scientific judgement, so it is presented as an explicit choice rather
        # than inferred: the app never switches reference on the operator's behalf.
        # The Control-vs-Solvent-Control comparison sits beside the selector and is
        # refreshed on every analysis, so the choice is never made blind.
        control_row = QHBoxLayout()
        control_row.addWidget(QLabel("Reference control:"))
        self.control_combo = QComboBox()
        self.control_combo.addItem("Pooled (control + solvent)", CONTROL_MODE_POOLED)
        self.control_combo.addItem("Negative control only", CONTROL_MODE_NEGATIVE)
        self.control_combo.addItem("Solvent control only", CONTROL_MODE_SOLVENT)
        self.control_combo.setToolTip(
            "The group that dose comparisons, Abbott's correction and the OECD "
            "validity check are measured against."
        )
        control_row.addWidget(self.control_combo)
        control_row.addSpacing(12)
        control_row.addWidget(QLabel("NOEC correction:"))
        self.correction_combo = QComboBox()
        # Short names here; the report and the CSV keep the full ones, which are
        # what a reader who is not looking at this window needs.
        self.correction_combo.addItem(
            NOEC_CORRECTION_SHORT_LABELS[NOEC_CORRECTION_HOLM], NOEC_CORRECTION_HOLM
        )
        self.correction_combo.addItem(
            NOEC_CORRECTION_SHORT_LABELS[NOEC_CORRECTION_BONFERRONI], NOEC_CORRECTION_BONFERRONI
        )
        self.correction_combo.setToolTip(
            "Multiplicity control for the dose-versus-control comparisons. "
            "Holm holds the same family-wise error rate as Bonferroni while "
            "rejecting at least as often, so it cannot miss an effect Bonferroni "
            "would have found."
        )
        control_row.addWidget(self.correction_combo)
        control_row.addStretch()
        main_layout.addLayout(control_row)

        # No item may be clipped, whatever the theme metrics or the system font.
        for combo in (self.model_combo, self.control_combo, self.correction_combo):
            combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)

        # Disable reference options this project has no groups for, then restore
        # the persisted choice, all before wiring the change signals so that
        # reopening a project does not fire a spurious analysis run per widget.
        self._configure_control_combo()
        self._restore_analysis_settings()

        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        self.model_combo.currentIndexChanged.connect(self._on_analysis_setting_changed)
        self.bottom_spin.editingFinished.connect(self._on_analysis_setting_changed)
        self.top_spin.editingFinished.connect(self._on_analysis_setting_changed)
        self.abbott_check.stateChanged.connect(self._on_analysis_setting_changed)
        self.control_combo.currentIndexChanged.connect(self._on_analysis_setting_changed)
        self.correction_combo.currentIndexChanged.connect(self._on_analysis_setting_changed)
        self._on_model_changed()

        results_layout = QHBoxLayout()
        left_side_layout = QVBoxLayout()
        self.table = QTableWidget()
        self._set_table_headers(self.SUMMARY_HEADERS_LEAD + self.SUMMARY_HEADERS_TAIL)

        left_side_layout.addWidget(self.table)
        
        endpoints_group = QGroupBox("Calculated Endpoints")
        endpoints_layout = QVBoxLayout(endpoints_group)
        self.model_label = QLabel("<b>Model:</b> \u2014")
        self.lc50_label = QLabel("<b>LC50:</b> Not calculated")
        self.tsk_label = QLabel("<b>LC50 (Spearman-Kärber):</b> Not calculated")
        self.slope_label = QLabel("<b>Slope:</b> Not calculated")
        self.r_squared_label = QLabel("<b>R\u00b2:</b> Not calculated")
        self.noec_label = QLabel("<b>NOEC:</b> Not calculated")
        self.loec_label = QLabel("<b>LOEC:</b> Not calculated")
        # Sits with the endpoints rather than beside the reference-control
        # selector, where its full sentence crowded the controls. The counts
        # behind the p-value move to the tooltip.
        self.control_comparison_label = QLabel("<b>Control vs solvent control:</b> Not calculated")
        self.trend_label = QLabel("<b>Trend (Cochran-Armitage):</b> Not calculated")
        self.sublethal_loec_label = QLabel("<b>Sublethal LOEC (≥1 abnormality):</b> Not calculated")
        self.ti_label = QLabel("<b>Teratogenic Index (LC50/EC50):</b> Not calculated")
        for label in [self.model_label, self.lc50_label, self.tsk_label, self.slope_label,
                      self.r_squared_label, self.noec_label, self.loec_label,
                      self.control_comparison_label, self.trend_label,
                      self.sublethal_loec_label, self.ti_label]:
            endpoints_layout.addWidget(label)
        left_side_layout.addWidget(endpoints_group)

        self.invalid_test_label = QLabel("")
        self.invalid_test_label.setObjectName("InvalidTestLabel")
        # Interface chrome follows the application font, not the serif reserved
        # for figures.
        _warning_font = self.font()
        _warning_font.setPointSizeF(scaled_pt(12))
        _warning_font.setBold(True)
        self.invalid_test_label.setFont(_warning_font)
        self.invalid_test_label.setAlignment(Qt.AlignCenter)
        self.invalid_test_label.hide()
        left_side_layout.addWidget(self.invalid_test_label)

        # Groups excluded from the statistics for want of a scored embryo. The
        # endpoints below rest on fewer groups than the design lays out, which the
        # operator cannot infer from the summary table — it lists those groups with
        # their assigned wells like any other.
        self.unevaluable_label = QLabel("")
        self.unevaluable_label.setObjectName("UnevaluableLabel")
        self.unevaluable_label.setWordWrap(True)
        self.unevaluable_label.setAlignment(Qt.AlignCenter)
        self.unevaluable_label.hide()
        left_side_layout.addWidget(self.unevaluable_label)
        results_layout.addLayout(left_side_layout, 2)

        self.plot_tabs = QTabWidget()
        self.plot_container_mortality = QWidget()
        self.plot_container_hatching = QWidget()
        self.plot_container_malformation = QWidget()
        self.plot_container_timecourse = QWidget()
        self.plot_container_fate = QWidget()
        self.plot_container_lc50_series = QWidget()

        for container in [self.plot_container_mortality, self.plot_container_hatching,
                          self.plot_container_malformation, self.plot_container_timecourse,
                          self.plot_container_fate, self.plot_container_lc50_series]:
            container.setLayout(QVBoxLayout())
            container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.plot_tabs.addTab(self.plot_container_mortality, "Mortality Dose-Response")
        self.plot_tabs.addTab(self.plot_container_lc50_series, "LC50 Time-Series")
        self.plot_tabs.addTab(self.plot_container_timecourse, "Mortality Time-Course")
        self.plot_tabs.addTab(self.plot_container_fate, "Fate Composition")
        self.plot_tabs.addTab(self.plot_container_hatching, "Hatching Rate")
        self.plot_tabs.addTab(self.plot_container_malformation, "Malformation Profile")
        results_layout.addWidget(self.plot_tabs, 3)

        self.loading_overlay = LoadingOverlay(self)
        
        main_layout.addLayout(results_layout)

    def resizeEvent(self, event: QResizeEvent):
        super().resizeEvent(event)
        self.loading_overlay.setGeometry(self.rect())

    def run_timeseries(self):
        """Recompute with the LC50 fitted at every timepoint."""
        self.run_analysis(with_timeseries=True)

    def run_analysis(self, *_args, with_timeseries: bool = False):
        day_selection = self.day_selector.currentData()
        if not day_selection:
            log.warning("Analysis skipped: No day selected or available.")
            self._clear_results()
            return

        if self.analysis_thread is not None and self.analysis_thread.isRunning():
            if self.analysis_worker is not None:
                self.analysis_worker.cancel()
                try:
                    self.analysis_worker.finished.disconnect()
                    self.analysis_worker.error.disconnect()
                except (TypeError, RuntimeError):
                    pass
            self.analysis_thread.quit()
            if not self.analysis_thread.wait(2000):
                log.warning("Previous analysis thread did not stop within 2s; abandoning it.")
        self.analysis_worker = None
        self.analysis_thread = None

        self._set_ui_enabled(
            False,
            "Fitting the LC50 at every timepoint..." if with_timeseries
            else "Calculating, please wait...",
        )
        mode = self.model_combo.currentData()
        bottom = self.bottom_spin.value() if mode in ("LL3_bottom", "LL2") else None
        top = self.top_spin.value() if mode in ("LL3_top", "LL2") else None
        abbott = self.abbott_check.isChecked()
        control_mode = self.control_combo.currentData() or CONTROL_MODE_POOLED
        noec_correction = self.correction_combo.currentData() or NOEC_CORRECTION_HOLM
        obs_rows = self.manager.get_all_well_observations_with_layout()
        plate_layout = self.manager.get_all_plate_layouts()
        concentrations = self.manager.get_concentrations()
        project_info = self.manager.get_project_info()
        self.analysis_thread = QThread()
        self.analysis_worker = AnalysisWorker(
            obs_rows, plate_layout, concentrations, project_info,
            day_selection, mode=mode, bottom=bottom, top=top, abbott=abbott,
            control_mode=control_mode, noec_correction=noec_correction,
            with_timeseries=with_timeseries,
        )
        self.analysis_worker.moveToThread(self.analysis_thread)
        self.analysis_worker.finished.connect(self._handle_analysis_results)
        self.analysis_worker.error.connect(self._handle_analysis_error)
        self.analysis_thread.started.connect(self.analysis_worker.run)
        self.analysis_thread.finished.connect(self.analysis_thread.deleteLater)
        self.analysis_thread.start()

    def _handle_analysis_results(self, results: Dict[str, Any]):
        if self.analysis_thread:
            self.analysis_thread.quit()
            self.analysis_thread.wait()
            self.analysis_thread = None
        self.analysis_worker = None

        self.analysis_results = results

        if results.get("empty"):
            self._clear_results(day=results.get("day"))
        else:
            summary_df = results['summary_df']
            self._update_control_comparison_label(results.get('control_comparison', {}))
            self._check_test_validity(summary_df, results.get('control_mode', CONTROL_MODE_POOLED),
                                      day=results.get('analysis_day'))
            self._update_unevaluable_label(results.get('unevaluable_groups', []))
            self._populate_table(summary_df)
            conc_unit = self.manager.get_project_info().get("concentration_unit", "unit")
            self._update_endpoint_labels(results.get('lc50_results', {}), results.get('noec_loec_results', {}),
                                         conc_unit, trend_results=results.get('trend_results', {}),
                                         sublethal_stats=results.get('sublethal_stats', {}),
                                         teratogenic_index=results.get('teratogenic_index', {}),
                                         tsk_results=results.get('tsk_results', {}))
            self._update_plots(results)
        
        self._set_ui_enabled(True)

    def _handle_analysis_error(self, message: str):
        if self.analysis_thread:
            self.analysis_thread.quit()
            self.analysis_thread.wait()
            self.analysis_thread = None
        self.analysis_worker = None
        QMessageBox.critical(self, "Analysis Error", message)
        self._clear_results()
        self._set_ui_enabled(True)
        
    def _set_table_headers(self, headers: List[str]) -> None:
        """Apply the summary table's column labels and their resize behavior."""
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        header = self.table.horizontalHeader()
        for i, name in enumerate(headers):
            stretch = ("%" in name) or (name == "Type")
            header.setSectionResizeMode(
                i, QHeaderView.Stretch if stretch else QHeaderView.ResizeToContents
            )

    def _populate_table(self, summary_df: pd.DataFrame):
        self.table.setRowCount(0)
        has_abbott = "mortality_abbott" in summary_df.columns
        headers = list(self.SUMMARY_HEADERS_LEAD)
        if has_abbott:
            headers.append("Abbott Mort. (%)")
        headers += self.SUMMARY_HEADERS_TAIL
        self._set_table_headers(headers)

        summary_df_sorted = summary_df.drop_duplicates(subset=['conc_id']).sort_values(by='conc_value').reset_index(drop=True)
        self.table.setRowCount(len(summary_df_sorted))

        for row_idx, data in summary_df_sorted.iterrows():
            total = data['total']
            scored = evaluable_n(data)
            live = data['live'] if 'live' in summary_df.columns else scored
            mortality = (data['dead'] / scored * 100) if scored > 0 else 0
            hatching = (data['hatched'] / scored * 100) if scored > 0 else 0
            # Malformation is expressed as a percentage of surviving embryos.
            malformed = (data['malformed'] / live * 100) if live > 0 else 0

            cells = [str(data['conc_id']), str(data['conc_type']),
                     str(int(total)), str(int(scored)), str(int(data['dead'])),
                     f"{mortality:.2f}"]
            if has_abbott:
                av = data['mortality_abbott']
                cells.append("" if pd.isna(av) else f"{av:.2f}")
            cells += [f"{hatching:.2f}", f"{malformed:.2f}"]

            items = [QTableWidgetItem(c) for c in cells]
            for i in range(2, len(items)):
                items[i].setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

            for col_idx, item in enumerate(items):
                self.table.setItem(row_idx, col_idx, item)

    def _update_plots(self, results: Dict[str, Any]):
        self._update_plot_tab(self.plot_container_mortality, results.get('mortality_plot_figure'))
        self._update_plot_tab(
            self.plot_container_lc50_series,
            results.get('lc50_timeseries_figure'),
            "Press 'LC50 Time-Series' to fit the LC50 at every timepoint.",
        )
        self._update_plot_tab(self.plot_container_timecourse, results.get('timecourse_plot_figure'))
        self._update_plot_tab(self.plot_container_fate, results.get('fate_plot_figure'))
        self._update_plot_tab(self.plot_container_hatching, results.get('hatching_plot_figure'))
        self._update_plot_tab(self.plot_container_malformation, results.get('malformation_plot_figure'))

    def _update_plot_tab(self, container: QWidget, fig: Figure, message: str = None):
        layout = container.layout()
        if layout is None:
            layout = QVBoxLayout(container)
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        # Keep a handle on the figure so it can be exported / copied later.
        container._figure = fig
        if fig:
            canvas = FigureCanvas(fig)
            canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            layout.addWidget(canvas)
        else:
            label = QLabel(message or "Plot not available.")
            label.setAlignment(Qt.AlignCenter)
            _placeholder_font = label.font()
            _placeholder_font.setPointSizeF(scaled_pt(12))
            label.setFont(_placeholder_font)
            layout.addWidget(label)

    def _current_plot_figure(self):
        """The matplotlib Figure of the currently selected plot tab, or None."""
        widget = self.plot_tabs.currentWidget()
        return getattr(widget, "_figure", None) if widget is not None else None

    def _current_plot_name(self) -> str:
        return self.plot_tabs.tabText(self.plot_tabs.currentIndex()).replace(" ", "_")

    def _export_current_figure(self):
        fig = self._current_plot_figure()
        if fig is None:
            QMessageBox.information(self, "No Figure", "There is no figure in the current tab to export.")
            return
        project_name = self.manager.get_project_name()
        default_name = f"{project_name}_{self._current_plot_name()}.png"
        reports_dir = os.path.join(self.manager.get_project_directory(), "reports")
        os.makedirs(reports_dir, exist_ok=True)
        default_path = os.path.join(reports_dir, default_name)
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Figure", default_path,
            "PNG image (*.png);;SVG vector (*.svg);;PDF document (*.pdf)",
        )
        if not file_path:
            return
        try:
            fig.savefig(file_path, dpi=600, bbox_inches="tight")
            QMessageBox.information(self, "Figure Exported", f"Figure saved to:\n{file_path}")
        except Exception as e:
            log.error(f"Figure export failed: {e}", exc_info=True)
            QMessageBox.critical(self, "Export Failed", f"Could not save the figure:\n{e}")

    def _copy_current_figure(self):
        fig = self._current_plot_figure()
        if fig is None:
            QMessageBox.information(self, "No Figure", "There is no figure in the current tab to copy.")
            return
        try:
            from PySide6.QtGui import QImage
            from PySide6.QtWidgets import QApplication
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=600, bbox_inches="tight")
            buf.seek(0)
            image = QImage.fromData(buf.getvalue(), "PNG")
            QApplication.clipboard().setImage(image)
            self.status_message.emit("Figure copied to the clipboard at 600 dpi.")
        except Exception as e:
            log.error(f"Copy figure failed: {e}", exc_info=True)
            QMessageBox.critical(self, "Copy Failed", f"Could not copy the figure:\n{e}")
            
    def _export_results(self):
        if not self.analysis_results or self.analysis_results.get("empty"):
            QMessageBox.warning(self, "No Data", "No data is available to export.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Export Options")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Choose an export format:"))
        btn_csv = QPushButton("Export Raw Data to CSV")
        btn_csv.setToolTip("One row per well observation — the input to the analysis.")
        btn_analysis = QPushButton("Export Analysis Tables (CSV folder)")
        btn_analysis.setToolTip(
            "The computed results: per-group summary, endpoints, sublethal tests "
            "and effect sizes, ready for a statistics package."
        )
        btn_docx = QPushButton("Generate Full Report (DOCX)")
        btn_box = QDialogButtonBox(QDialogButtonBox.Cancel)
        btn_csv.clicked.connect(lambda: [dialog.accept(), self._export_to_csv()])
        btn_analysis.clicked.connect(lambda: [dialog.accept(), self._export_analysis_tables()])
        btn_docx.clicked.connect(lambda: [dialog.accept(), self._export_to_docx()])
        btn_box.rejected.connect(dialog.reject)
        layout.addWidget(btn_csv); layout.addWidget(btn_analysis)
        layout.addWidget(btn_docx); layout.addWidget(btn_box)
        dialog.exec()

    def _export_analysis_tables(self):
        """Write the computed analysis as CSV tables into a chosen folder."""
        from src.export.analysis_export import export_analysis

        default_dir = os.path.join(self.manager.get_project_directory(), "reports")
        os.makedirs(default_dir, exist_ok=True)
        target = QFileDialog.getExistingDirectory(
            self, "Choose a folder for the analysis tables", default_dir
        )
        if not target:
            return

        project_name = self.manager.get_project_name()
        out_dir = os.path.join(
            target, f"FET_Analysis_{project_name}_{datetime.now().strftime('%Y-%m-%d')}"
        )
        try:
            written = export_analysis(
                self.analysis_results, self.manager.get_project_info(), out_dir
            )
        except Exception as e:
            log.error(f"Analysis export failed: {e}", exc_info=True)
            QMessageBox.critical(self, "Export Failed", f"Could not write the tables:\n{e}")
            return

        if not written:
            QMessageBox.warning(
                self, "Nothing to Export",
                "The analysis produced no tables. Run the analysis first.",
            )
            return
        QMessageBox.information(
            self, "Analysis Exported",
            f"{len(written)} table(s) written to:\n{out_dir}\n\n" + "\n".join(written),
        )

    def _export_to_csv(self):
        df = self.analysis_results.get('full_df')
        if df is None or df.empty: return

        default_filename = f"FET_RawData_{self.manager.get_project_name()}.csv"
        file_path, _ = QFileDialog.getSaveFileName(self, "Save CSV Data", default_filename, "CSV Files (*.csv)")
        
        if file_path:
            try:
                df.to_csv(file_path, index=False)
                QMessageBox.information(self, "Success", f"Data successfully exported to:\n{file_path}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save CSV file:\n{e}")

    def _export_to_docx(self):
        from src.core.utils import app_settings
        from src.ui.dialogs.report_sections_dialog import ReportSectionsDialog

        # The same INI-backed store the rest of the app uses; a bare QSettings()
        # resolves to NativeFormat and stranded these preferences in the
        # platform registry.
        picker = ReportSectionsDialog(app_settings(), self)
        if picker.exec() != QDialog.DialogCode.Accepted:
            return
        sections = picker.selected_sections()

        project_name = self.manager.get_project_name()
        current_date = datetime.now().strftime('%Y-%m-%d')
        suggested_filename = f"FET_Report_{project_name}_{current_date}.docx"

        reports_dir = os.path.join(self.manager.get_project_directory(), "reports")
        if not os.path.exists(reports_dir):
            os.makedirs(reports_dir)
        default_path = os.path.join(reports_dir, suggested_filename)
        
        file_path, _ = QFileDialog.getSaveFileName(
            self, 
            "Save Word Report", 
            default_path,
            "Word Documents (*.docx)"
        )
        
        if not file_path: 
            log.info("User cancelled the save dialog for the DOCX report.")
            return

        self._set_ui_enabled(False, "Generating report...")
        snapshot = self.manager.get_report_snapshot()
        project_dir = self.manager.get_project_directory()
        # Each Figure in analysis_results is still bound to the live
        # FigureCanvasQTAgg displayed in the Results tab, and Figure.savefig()
        # renders through that very canvas (FigureCanvasQTAgg inherits print_png,
        # so matplotlib keeps the existing canvas instead of creating an Agg one).
        # Rasterizing on the worker thread would therefore drive draw() on a widget
        # the GUI thread may be painting. Do it here, on the GUI thread, and give
        # the worker plain PNG buffers, which _add_plot_to_document already accepts.
        report_payload = self._rasterize_figures(self.analysis_results)
        self.report_thread = QThread()
        self.report_worker = ReportWorker(snapshot, project_dir, file_path,
                                          report_payload, sections=sections)
        self.report_worker.moveToThread(self.report_thread)
        self.report_thread.started.connect(self.report_worker.run)
        self.report_worker.finished.connect(self._handle_report_finished)
        self.report_thread.finished.connect(self.report_thread.deleteLater)
        self.report_thread.start()

    @staticmethod
    def _rasterize_figures(results: Dict[str, Any]) -> Dict[str, Any]:
        """Shallow copy of *results* with every Figure replaced by a PNG buffer."""
        payload = dict(results)
        for key, value in results.items():
            if isinstance(value, Figure):
                buffer = io.BytesIO()
                try:
                    value.savefig(buffer, format="png", dpi=600, bbox_inches="tight")
                    buffer.seek(0)
                    payload[key] = buffer
                except Exception as e:
                    log.error(f"Could not rasterize figure '{key}': {e}", exc_info=True)
                    payload[key] = None
        return payload

    def _handle_report_finished(self, success: bool, message: str):
        if self.report_thread:
            self.report_thread.quit()
            self.report_thread.wait()
        self.report_worker = None

        self._set_ui_enabled(True)
        if success:
            QMessageBox.information(self, "Success", f"Report successfully saved to:\n{message}")
        else:
            QMessageBox.critical(self, "Error", f"Failed to generate report:\n{message}")

    def _clear_results(self, day=None):
        self.table.setRowCount(0)
        day_str = f" for Day {day}" if day else ""
        no_data_msg = f"No data recorded{day_str}. Press 'Recalculate' to begin."
        self._update_endpoint_labels({}, {}, message="Press 'Recalculate' to begin.")
        # Cleared with the rest of the endpoints it now sits among, so a stale
        # p-value cannot outlive the results it came from.
        self.control_comparison_label.setText(
            "<b>Control vs solvent control:</b> Not calculated"
        )
        self.control_comparison_label.setToolTip("")

        self._update_plot_tab(self.plot_container_mortality, None, no_data_msg)
        self._update_plot_tab(self.plot_container_lc50_series, None, no_data_msg)
        self._update_plot_tab(self.plot_container_timecourse, None, no_data_msg)
        self._update_plot_tab(self.plot_container_fate, None, no_data_msg)
        self._update_plot_tab(self.plot_container_hatching, None, no_data_msg)
        self._update_plot_tab(self.plot_container_malformation, None, no_data_msg)
        
        self.invalid_test_label.hide()
        self.unevaluable_label.hide()

    def _on_model_changed(self):
        mode = self.model_combo.currentData()
        show_bottom = mode in ("LL3_bottom", "LL2")
        show_top = mode in ("LL3_top", "LL2")
        self.bottom_label.setVisible(show_bottom)
        self.bottom_spin.setVisible(show_bottom)
        self.top_label.setVisible(show_top)
        self.top_spin.setVisible(show_top)

    def _configure_control_combo_enabled_state(self) -> None:
        """Re-enable the selector only if more than one reference is available."""
        model = self.control_combo.model()
        available = sum(
            1 for i in range(self.control_combo.count())
            if model.item(i) is not None and model.item(i).isEnabled()
        )
        self.control_combo.setEnabled(available > 1)

    def _configure_control_combo(self) -> None:
        """Offer only the reference controls this project actually defines.

        Solvent controls are optional in project setup, so "solvent control only"
        is frequently a choice the data cannot support. Rather than let it be
        selected and silently fall back, the unavailable options are disabled and
        the selector is pinned when only one reference exists.
        """
        try:
            types = {c.get("type") for c in self.manager.get_concentrations()}
        except Exception as e:
            log.warning(f"Could not determine control groups: {e}")
            return

        availability = {
            CONTROL_MODE_POOLED: {"Control", "Solvent Control"} & types,
            CONTROL_MODE_NEGATIVE: "Control" in types,
            CONTROL_MODE_SOLVENT: "Solvent Control" in types,
        }
        # Pooling is only a distinct option when both controls are present.
        availability[CONTROL_MODE_POOLED] = (
            "Control" in types and "Solvent Control" in types
        )

        model = self.control_combo.model()
        first_available = None
        for index in range(self.control_combo.count()):
            mode = self.control_combo.itemData(index)
            enabled = bool(availability.get(mode))
            item = model.item(index)
            if item is not None:
                item.setEnabled(enabled)
            if enabled and first_available is None:
                first_available = index

        enabled_count = sum(1 for v in availability.values() if v)
        if first_available is not None:
            self.control_combo.setCurrentIndex(first_available)
        # With a single possible reference there is no choice left to make.
        self.control_combo.setEnabled(enabled_count > 1)
        self.control_combo.setToolTip(
            "The group that dose comparisons, Abbott's correction and the OECD "
            "validity check are measured against."
            if enabled_count > 1 else
            "This project defines only one control group, so there is no "
            "alternative reference to choose."
        )

    def _restore_analysis_settings(self) -> None:
        """Apply the project's persisted analysis settings to the controls.

        Called before the change signals are connected; see _init_ui.
        """
        settings = self.manager.get_analysis_settings()
        index = self.model_combo.findData(settings.get("model_mode"))
        if index >= 0:
            self.model_combo.setCurrentIndex(index)
        self.bottom_spin.setValue(float(settings.get("bottom", 0.0)))
        self.top_spin.setValue(float(settings.get("top", 100.0)))
        self.abbott_check.setChecked(bool(settings.get("abbott", False)))
        index = self.control_combo.findData(settings.get("control_mode"))
        item = self.control_combo.model().item(index) if index >= 0 else None
        if index >= 0 and (item is None or item.isEnabled()):
            self.control_combo.setCurrentIndex(index)
        index = self.correction_combo.findData(settings.get("noec_correction"))
        if index >= 0:
            self.correction_combo.setCurrentIndex(index)

    def _persist_analysis_settings(self) -> None:
        """Save the current control values so the analysis is reproducible."""
        try:
            self.manager.save_analysis_settings(
                model_mode=self.model_combo.currentData(),
                bottom=self.bottom_spin.value(),
                top=self.top_spin.value(),
                abbott=self.abbott_check.isChecked(),
                control_mode=self.control_combo.currentData(),
                noec_correction=self.correction_combo.currentData(),
            )
        except Exception as e:  # persistence must never block the analysis
            log.warning(f"Could not persist analysis settings: {e}")

    def _on_analysis_setting_changed(self, *_args) -> None:
        """Persist the changed setting, then recompute with it."""
        self._persist_analysis_settings()
        self.run_analysis()

    def _update_unevaluable_label(self, groups) -> None:
        """Name the groups the statistics had to leave out, if any."""
        if not groups:
            self.unevaluable_label.hide()
            self.unevaluable_label.clear()
            return
        names = ", ".join(str(g) for g in groups)
        plural = "group" if len(groups) == 1 else "groups"
        self.unevaluable_label.setText(
            f"Excluded from the statistics — no embryos scored in {plural} {names}. "
            f"The endpoints below are based on the remaining groups."
        )
        self.unevaluable_label.show()

    def _update_control_comparison_label(self, comparison: Dict[str, Any]) -> None:
        """Show the Control-vs-Solvent-Control result among the endpoints.

        Only the p-value is shown, since that is what the reference-control
        decision turns on; the counts and percentages behind it go to the
        tooltip. Highlighted when the two differ, because that is precisely the
        case in which pooling them would fold a solvent effect into the baseline.
        """
        comparison = comparison or {}
        label = "<b>Control vs solvent control:</b>"

        if not comparison.get("applicable"):
            # Not a result but a reason — "no solvent control in this project"
            # and the like, already phrased as a sentence.
            reason = comparison.get("summary", "Not calculated").rstrip(".")
            reason = reason[0].lower() + reason[1:] if reason else "not calculated"
            self.control_comparison_label.setText(
                f"{label} <span style='color:gray'>{reason}</span>"
            )
            self.control_comparison_label.setToolTip("")
            return

        p_value = comparison.get("p_value")
        p_text = f"p = {p_value:.3g}" if p_value is not None else "not calculated"
        color = "#B36B00" if comparison.get("differ") else "gray"
        self.control_comparison_label.setText(
            f"{label} <span style='color:{color}'>{p_text}</span>"
        )
        self.control_comparison_label.setToolTip(comparison.get("summary", ""))

    def _set_ui_enabled(self, enabled: bool, message: str = "Processing..."):
        self.day_selector.setEnabled(enabled)
        self.recalculate_btn.setEnabled(enabled)
        self.export_btn.setEnabled(enabled)
        self.timeseries_btn.setEnabled(enabled)
        self.model_combo.setEnabled(enabled)
        self.bottom_spin.setEnabled(enabled)
        self.top_spin.setEnabled(enabled)
        self.abbott_check.setEnabled(enabled)
        self.correction_combo.setEnabled(enabled)
        # Left disabled when the project defines a single control group; see
        # _configure_control_combo.
        if self.control_combo.count() and enabled:
            self._configure_control_combo_enabled_state()
        else:
            self.control_combo.setEnabled(False)

        if not enabled:
            self.loading_overlay.setText(message)
            self.loading_overlay.show()
        else:
            self.loading_overlay.hide()

    def _check_test_validity(self, summary_df: pd.DataFrame,
                             control_mode: str = CONTROL_MODE_POOLED,
                             day: int = None):
        """Banner warning when an assessed OECD validity criterion is not met.

        Carries the same two criteria the report's validity section assesses —
        control mortality and, at the end of the test, control hatching rate — so
        the on-screen banner cannot pass a test the report will fail. Hatching is
        judged only on the final day, for the reason given in
        ReportGenerator._is_end_of_test.
        """
        self.invalid_test_label.hide()
        threshold = self.manager.get_test_conditions().get("acceptable_mortality", 10.0)
        # Judged against the same reference the dose comparisons use, so the
        # validity verdict and the NOEC can never disagree about the baseline.
        control_groups = select_control_rows(summary_df, control_mode)
        if control_groups.empty:
            return
        total_control = evaluable_n(control_groups).sum()
        if total_control <= 0:
            return

        problems = []
        mortality = (control_groups['dead'].sum() / total_control) * 100
        if mortality > threshold:
            problems.append(
                f"Control mortality ({mortality:.2f}%) exceeds threshold ({threshold:.0f}%)"
            )

        minimum = ReportGenerator.CONTROL_HATCHING_MINIMUM_PCT
        if self._is_final_day(day) and 'hatched' in control_groups.columns:
            hatching = (control_groups['hatched'].sum() / total_control) * 100
            if hatching < minimum:
                problems.append(
                    f"Control hatching ({hatching:.2f}%) is below the OECD minimum ({minimum:.0f}%)"
                )

        if problems:
            self.invalid_test_label.setText("TEST INVALID: " + "; ".join(problems) + "!")
            self.invalid_test_label.show()

    def _is_final_day(self, day) -> bool:
        """True when *day* is the last day of the test."""
        try:
            num_days = self.manager.get_project_info().get("num_days")
            return num_days is not None and int(day) >= int(num_days)
        except (TypeError, ValueError, AttributeError):
            return False

    def _update_endpoint_labels(self, lc50_results, noec_loec_results, conc_unit="unit",
                                message=None, trend_results=None, sublethal_stats=None,
                                teratogenic_index=None, tsk_results=None):
        trend_results = trend_results or {}
        sublethal_stats = sublethal_stats or {}
        teratogenic_index = teratogenic_index or {}
        tsk_results = tsk_results or {}
        if message:
            self.tsk_label.setText(f"<b>LC50 (Spearman-Kärber):</b> {message}")
            base_text = f"<b>{'{label}'}:</b> {message}"
            self.lc50_label.setText(base_text.format(label="LC50"))
            self.slope_label.setText(base_text.format(label="Slope"))
            self.r_squared_label.setText(base_text.format(label="R\u00b2"))
            self.noec_label.setText(base_text.format(label="NOEC"))
            self.loec_label.setText(base_text.format(label="LOEC"))
            self.trend_label.setText(base_text.format(label="Trend (Cochran-Armitage)"))
            self.sublethal_loec_label.setText(base_text.format(label="Sublethal LOEC (≥1 abnormality)"))
            self.ti_label.setText(base_text.format(label="Teratogenic Index (LC50/EC50)"))
            self.model_label.setText("<b>Model:</b> —")
            return
        
        model_info = lc50_results.get("model_info") or {}
        model_text = model_info.get("display_name") or "—"
        self.model_label.setText(f"<b>Model:</b> {model_text}")

        lc50_val = lc50_results.get('lc50', 'N/A')
        noec_val = noec_loec_results.get('noec', 'N/A')
        loec_val = noec_loec_results.get('loec', 'N/A')
        
        conc_unit_str = f" {conc_unit}"

        # Numeric-ness comes from the fit and from each endpoint's numeric mirror,
        # never from the first character of the display string: the LC50 failure
        # message "100% mortality at all concentrations..." begins with a digit.
        if lc50_results.get('_fitted_params'):
            parts = lc50_val.split('(', 1)
            number_part = parts[0].strip()
            ci_part = f" ({parts[1]}" if len(parts) > 1 else ""
            lc50_display = f"{number_part}{conc_unit_str}{ci_part}"
        else:
            lc50_display = lc50_val
        noec_display = format_with_unit(noec_loec_results.get('noec_numeric'), noec_val, conc_unit)
        loec_display = format_with_unit(noec_loec_results.get('loec_numeric'), loec_val, conc_unit)
            
        # The threshold shown is the Bonferroni one; under Holm the decision is
        # made on adjusted p-values against a fixed alpha, so only the method is
        # named rather than a single cut-off that does not exist.
        correction_label = noec_loec_results.get('correction_label')
        if correction_label == NOEC_CORRECTION_LABELS[NOEC_CORRECTION_BONFERRONI]:
            alpha = noec_loec_results.get('alpha_adjusted', 0.05)
            alpha_str = f"  <span style='color:gray;font-size:small'>(α={alpha:.4f}, {correction_label})</span>"
        elif correction_label:
            alpha_str = f"  <span style='color:gray;font-size:small'>(α=0.05, {correction_label})</span>"
        else:
            alpha_str = ""
        self.lc50_label.setText(f"<b>LC50:</b> {lc50_display}")

        # The non-parametric estimate needs no optimiser, so it survives data the
        # curve fit cannot handle. It sits beside the model LC50 as a cross-check,
        # and is called out as the only available estimate when the fit failed.
        tsk_numeric = tsk_results.get('lc50_numeric')
        tsk_display = format_with_unit(
            tsk_numeric, tsk_results.get('lc50', 'Not calculated'), conc_unit
        )
        if tsk_numeric is not None and not lc50_results.get('_fitted_params'):
            tsk_display += (
                "  <span style='color:#B36B00;font-size:small'>"
                "(curve fitting failed; this is the reported LC50)</span>"
            )
        self.tsk_label.setText(f"<b>LC50 (Spearman-Kärber):</b> {tsk_display}")

        self.slope_label.setText(f"<b>Slope:</b> {lc50_results.get('slope', 'N/A')}")
        self.r_squared_label.setText(f"<b>R\u00b2:</b> {lc50_results.get('r_squared', 'N/A')}")
        self.noec_label.setText(f"<b>NOEC:</b> {noec_display}{alpha_str}")
        self.loec_label.setText(f"<b>LOEC:</b> {loec_display}")

        trend_text = trend_results.get('trend', 'N/A')
        p_value = trend_results.get('p_value')
        if p_value and p_value not in ("Not Calculated",):
            trend_text = f"{trend_text}  <span style='color:gray;font-size:small'>(p={p_value})</span>"
        self.trend_label.setText(f"<b>Trend (Cochran-Armitage):</b> {trend_text}")

        # Pooled sublethal NOEC/LOEC (Fisher's + Benjamini-Hochberg)
        pooled = sublethal_stats.get('pooled', {}) if sublethal_stats else {}
        sub_loec = pooled.get('loec', 'Not calculated')
        if pooled.get('no_events'):
            sub_display = "No abnormalities observed in any group"
        else:
            sub_display = format_with_unit(pooled.get('loec_numeric'), sub_loec, conc_unit)
        self.sublethal_loec_label.setText(f"<b>Sublethal LOEC (≥1 abnormality):</b> {sub_display}")

        # Teratogenic Index (LC50 / EC50-malformation)
        ti_val = teratogenic_index.get('teratogenic_index', 'Not calculated') if teratogenic_index else 'Not calculated'
        ec50_malf = teratogenic_index.get('ec50_malformation', '') if teratogenic_index else ''
        if (teratogenic_index.get('ti_numeric') is not None
                and teratogenic_index.get('ec50_numeric') is not None):
            ti_text = f"{ti_val}  <span style='color:gray;font-size:small'>(EC50 malf. = {ec50_malf}{conc_unit_str})</span>"
        else:
            ti_text = ti_val
        self.ti_label.setText(f"<b>Teratogenic Index (LC50/EC50):</b> {ti_text}")