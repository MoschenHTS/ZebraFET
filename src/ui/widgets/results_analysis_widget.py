import logging
import os
from collections import Counter
from typing import Dict, Any
from datetime import datetime

import numpy as np
import pandas as pd
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             QTableWidget, QTableWidgetItem, QHeaderView, QComboBox,
                             QGroupBox, QTabWidget, QFileDialog, QMessageBox, QDialog,
                             QDialogButtonBox, QFrame, QSizePolicy)
from PySide6.QtCore import Qt, QObject, Signal, QThread
from PySide6.QtGui import QFont, QIcon, QResizeEvent

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit
from scipy.stats import t, fisher_exact

from src.core.project_manager import ProjectManager
from src.export.report_generator import ReportGenerator
from src.core.utils import resource_path
from src.ui.components import SpinningIcon, LoadingOverlay
from src.core.biostatistics import logistic_function, calculate_lc50_robust, calculate_noec_loec_with_correction
from src.core.constants import (
    STATUS_DEAD_EMBRYO as STATUS_EMBRYO_DEAD,
    STATUS_DEAD_HATCHED as STATUS_HATCHED_DEAD,
    STATUS_LIVE_EMBRYO as STATUS_EMBRYO_ALIVE,
    STATUS_LIVE_HATCHED as STATUS_HATCHED_ALIVE,
    DEAD_STATUSES, LIVE_STATUSES, HATCHED_STATUSES,
)

log = logging.getLogger(__name__)

# Plotting Style — publication-quality (high-impact journal settings)
try:
    plt.style.use('seaborn-v0_8-ticks')
except OSError:
    try:
        plt.style.use('seaborn-ticks')
    except OSError:
        plt.style.use('default')
PLOT_PARAMS = {
    'font.family': 'Times New Roman',
    'font.size': 11,
    'axes.labelsize': 11,
    'axes.titlesize': 12,
    'axes.titleweight': 'bold',
    'xtick.labelsize': 9,
    'ytick.labelsize': 9,
    'legend.fontsize': 9,
    'legend.frameon': True,
    'legend.edgecolor': '0.8',
    'axes.linewidth': 0.8,
    'xtick.major.width': 0.8,
    'ytick.major.width': 0.8,
    'lines.linewidth': 1.5,
    'figure.dpi': 300,
    'savefig.dpi': 300,
    'savefig.bbox': 'tight',
}
# Publication color palette (ColorBrewer-derived, colorblind-safe, print-friendly)
PUB_COLORS = {
    'Control':          '#4d4d4d',   # dark grey
    'Solvent Control':  '#878787',   # mid grey
    'Substrate':        '#2166ac',   # deep blue
    'Positive Control': '#d73027',   # red
}
plt.rcParams.update(PLOT_PARAMS)


class AnalysisWorker(QObject):
    """
    Performs all heavy data processing and plot generation in a separate thread
    to keep the main UI responsive and ensure analytical consistency.

    Supports cooperative cancellation via cancel(): the worker checks
    self._cancelled between each pipeline stage and exits early if set.
    """
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, manager: ProjectManager, day_selection: str):
        super().__init__()
        self.manager = manager
        self.day_selection = day_selection
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
            day_df = df[df['day'] == day]

            if day_df.empty:
                self.finished.emit({"empty": True, "day": day})
                return

            summary_df = self._aggregate_daily_data(day_df)
            results['summary_df'] = summary_df
            if self._cancelled:
                return

            statistical_df = summary_df[summary_df['conc_type'] != 'Positive Control'].copy()

            plot_data_mortality = self._prepare_plot_data(statistical_df, "dead")
            lc50_results = self._calculate_lc50(plot_data_mortality)
            results['lc50_results'] = lc50_results
            if self._cancelled:
                return

            results['noec_loec_results'] = self._calculate_noec_loec(statistical_df)
            if self._cancelled:
                return

            conc_unit = self.manager.get_project_info().get("concentration_unit", "unit")
            results['mortality_plot_figure'] = self._plot_mortality(plot_data_mortality, lc50_results, conc_unit)
            if self._cancelled:
                return

            results['hatching_plot_figure'] = self._plot_barchart(summary_df, "hatched", "Hatching Rate")
            if self._cancelled:
                return

            results['malformation_plot_figure'] = self._plot_malformation_details(summary_df)
            if self._cancelled:
                return

            self.finished.emit(results)

        except Exception as e:
            log.error(f"Error during analysis in worker thread: {e}", exc_info=True)
            self.error.emit(f"An unexpected error occurred during analysis: {e}")

    def _create_results_dataframe(self) -> pd.DataFrame:
        obs_rows = self.manager.get_all_well_observations_with_layout()
        records = []

        for row in obs_rows:
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
        df['dead'] = df['status'].isin(DEAD_STATUSES).astype(int)
        df['hatched'] = df['status'].isin(HATCHED_STATUSES).astype(int)
        df['malformed'] = df['sublethal'].apply(lambda x: 1 if x else 0)
        return df

    def _aggregate_daily_data(self, day_df: pd.DataFrame) -> pd.DataFrame:
        if day_df.empty: return pd.DataFrame()
        
        grouped_by_id = day_df.groupby('conc_id')
        summary = grouped_by_id.agg(
            dead=('dead', 'sum'), 
            hatched=('hatched', 'sum'), 
            malformed=('malformed', 'sum')
        ).reset_index()

        conc_totals = Counter()
        plate_layout = self.manager.get_all_plate_layouts()
        for plate_id, wells in plate_layout.items():
            for well_id, conc_id in wells.items():
                conc_totals[conc_id] += 1
        
        summary['total'] = summary['conc_id'].map(conc_totals).fillna(0).astype(int)

        conc_info = day_df.groupby('conc_id').agg(
            conc_type=('conc_type', 'first'),
            conc_value=('conc_value', 'first')
        ).reset_index()
        summary = pd.merge(summary, conc_info, on='conc_id', how='left')
        
        def safe_counter(series):
            flat_list = [item for sublist in series if isinstance(sublist, list) for item in sublist]
            return Counter(flat_list)
        
        malf_details = grouped_by_id['sublethal'].apply(safe_counter).reset_index()
        malf_details = malf_details.rename(columns={'sublethal': 'malformation_details'})
        
        final_summary = pd.merge(summary, malf_details, on='conc_id', how='left')

        all_conc_ids = pd.DataFrame(list(conc_totals.keys()), columns=['conc_id'])
        final_summary = pd.merge(all_conc_ids, final_summary, on='conc_id', how='left')
        
        final_summary['total'] = final_summary['conc_id'].map(conc_totals)
        final_summary[['dead', 'hatched', 'malformed']] = final_summary[['dead', 'hatched', 'malformed']].fillna(0)

        concentrations_list = self.manager.get_concentrations()
        if concentrations_list:
            all_conc_info = pd.DataFrame(concentrations_list)[['id', 'type', 'value']].rename(
                columns={'id': 'conc_id', 'type': 'conc_type', 'value': 'conc_value'}
            ).drop_duplicates()
            final_summary = final_summary.drop(columns=['conc_type', 'conc_value'], errors='ignore')
            final_summary = pd.merge(final_summary, all_conc_info, on='conc_id', how='left')

        return final_summary.fillna(0)

    @staticmethod
    def _prepare_plot_data(summary_df: pd.DataFrame, key: str) -> list:
        plot_data = []
        for _, row in summary_df.iterrows():
            if row['total'] > 0:
                value = row.get(key, 0)
                percent = (value / row['total']) * 100
                plot_data.append({'id': row['conc_id'], 'type': row['conc_type'], 'x': row['conc_value'], 'y': percent})
        return plot_data

    def _calculate_lc50(self, plot_data: list) -> dict:
        """Delegate to the standalone biostatistics module."""
        return calculate_lc50_robust(plot_data)

    def _calculate_noec_loec(self, summary_df: pd.DataFrame) -> Dict[str, Any]:
        """Delegate to the standalone biostatistics module."""
        return calculate_noec_loec_with_correction(summary_df)

    def _plot_mortality(self, plot_data, lc50_results, conc_unit) -> Figure:
        fig = Figure(figsize=(3.5, 3.0))  # single-column width (~89 mm)
        ax = fig.add_subplot(111)

        substrates = [p for p in plot_data if p['type'] == 'Substrate' and p['x'] > 0]
        if substrates:
            x_data = np.array([p['x'] for p in substrates])
            y_data = np.array([p['y'] for p in substrates])
            ax.scatter(x_data, y_data, label="Observed", color=PUB_COLORS['Substrate'],
                       zorder=10, s=30, marker='o', edgecolors='black', linewidths=0.5)

            is_calculable = "failed" not in str(lc50_results.get('lc50', '')) and \
                            "Not enough" not in str(lc50_results.get('lc50', '')) and \
                            "error" not in str(lc50_results.get('lc50', ''))

            if is_calculable and len(substrates) >= 4:
                try:
                    raw_params = lc50_results.get('_fitted_params')
                    if raw_params:
                        params = np.array(raw_params)
                    else:
                        bounds = ([0, 0, -np.inf, 0], [100, 100, np.inf, np.inf])
                        p0 = [min(y_data), max(y_data), -1, np.median(x_data)]
                        params, _ = curve_fit(logistic_function, x_data, y_data, p0=p0, bounds=bounds, maxfev=15000)

                    min_x, max_x = min(x_data), max(x_data)
                    if min_x > 0 and max_x > 0:
                        x_fit = np.logspace(np.log10(min_x * 0.9), np.log10(max_x * 1.1), 200)
                        y_fit = logistic_function(x_fit, *params)
                        ax.plot(x_fit, y_fit, color=PUB_COLORS['Positive Control'],
                                linestyle='-', label="4PL Logistic Fit")
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
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        if any([substrates, control]):
            ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.15), ncol=3, fontsize=8)
        fig.tight_layout(pad=1.0)
        return fig

    def _plot_barchart(self, summary_df, key, title) -> Figure:
        fig = Figure(figsize=(3.5, 3.0))  # single-column width
        ax = fig.add_subplot(111)

        plot_df = summary_df.copy()
        plot_df['percent'] = (plot_df[key] / plot_df['total'].replace(0, np.nan) * 100).fillna(0)
        plot_df['color'] = plot_df['conc_type'].map(PUB_COLORS).fillna(PUB_COLORS['Substrate'])
        plot_df = plot_df.sort_values(by='conc_value')

        bars = ax.bar(plot_df['conc_id'], plot_df['percent'],
                      color=plot_df['color'], edgecolor='black', linewidth=0.5)

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
            ax.legend(handles=legend_handles, loc='upper center', bbox_to_anchor=(0.5, -0.15), ncol=3, fontsize=8)

        ax.set_ylim(0, 105)
        ax.set_ylabel(f"{title} (%)")
        ax.set_xlabel("Treatment Group")
        ax.tick_params(axis='x', rotation=45)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        fig.tight_layout(pad=1.0)
        return fig

    def _plot_malformation_details(self, summary_df: pd.DataFrame) -> Figure:
        fig = Figure(figsize=(5.0, 3.5))  # wider to accommodate legend
        ax = fig.add_subplot(111)

        malf_data = {
            row['conc_id']: row['malformation_details']
            for _, row in summary_df.iterrows()
            if isinstance(row['malformation_details'], Counter) and row['malformation_details']
        }

        if not malf_data:
            ax.text(0.5, 0.5, "No malformations recorded for this day.",
                    ha='center', va='center', transform=ax.transAxes)
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)
            fig.tight_layout(pad=1.0)
            return fig

        all_malformations = sorted(list(set(m for details in malf_data.values() for m in details.keys())))

        group_order = summary_df.sort_values('conc_value')['conc_id'].unique()
        group_labels = [g for g in group_order if g in malf_data]

        df_data = {}
        for malf in all_malformations:
            percentages = []
            for group in group_labels:
                total = summary_df.loc[summary_df['conc_id'] == group, 'total'].iloc[0]
                count = malf_data.get(group, {}).get(malf, 0)
                percentages.append((count / total) * 100 if total > 0 else 0)
            df_data[malf] = percentages

        plot_df = pd.DataFrame(df_data, index=group_labels)

        # Use a perceptually-uniform, print-safe discrete palette
        import matplotlib
        n_malfs = len(all_malformations)
        colors = [matplotlib.colormaps['tab10'](i / max(n_malfs, 10)) for i in range(n_malfs)]
        plot_df.plot(kind='bar', stacked=True, ax=ax, color=colors, edgecolor='black', linewidth=0.4)

        ax.set_ylim(0, 105)
        ax.set_ylabel("Incidence (%)")
        ax.set_xlabel("Treatment Group")
        ax.legend(title="Sublethal endpoint", bbox_to_anchor=(1.02, 1), loc='upper left',
                  fontsize=8, title_fontsize=8)
        ax.tick_params(axis='x', rotation=45)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        fig.tight_layout(pad=1.0)
        return fig


class ReportWorker(QObject):
    finished = Signal(bool, str)

    def __init__(self, manager: ProjectManager, file_path: str, analysis_results: Dict):
        super().__init__()
        self.manager = manager
        self.file_path = file_path
        self.analysis_results = analysis_results

    def run(self):
        try:
            generator = ReportGenerator(self.manager, self.analysis_results)
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
        self.recalculate_btn.setIcon(QIcon(resource_path("resources/icons/save.svg")))
        self.recalculate_btn.clicked.connect(self.run_analysis)
        
        self.export_btn = QPushButton("Export Results")
        self.export_btn.setIcon(QIcon(resource_path("resources/icons/folder-down.svg")))
        self.export_btn.clicked.connect(self._export_results)
        
        self.control_layout.addWidget(QLabel("Analysis Day:"))
        self.control_layout.addWidget(self.day_selector)
        self.control_layout.addStretch()
        self.control_layout.addWidget(self.recalculate_btn)
        self.control_layout.addWidget(self.export_btn)
        main_layout.addLayout(self.control_layout)

        results_layout = QHBoxLayout()
        left_side_layout = QVBoxLayout()
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(["ID", "Type", "Total (N)", "Dead", "Mortality (%)", "Hatched (%)", "Malformed (%)"])
        
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents) # ID
        header.setSectionResizeMode(1, QHeaderView.Stretch)          # Type
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents) # Total (N)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents) # Dead
        header.setSectionResizeMode(4, QHeaderView.Stretch)          # Mortality (%)
        header.setSectionResizeMode(5, QHeaderView.Stretch)          # Hatched (%)
        header.setSectionResizeMode(6, QHeaderView.Stretch)          # Malformed (%)
        
        left_side_layout.addWidget(self.table)
        
        endpoints_group = QGroupBox("Calculated Endpoints")
        endpoints_layout = QVBoxLayout(endpoints_group)
        self.lc50_label = QLabel("<b>LC50:</b> Not calculated")
        self.slope_label = QLabel("<b>Slope:</b> Not calculated")
        self.r_squared_label = QLabel("<b>R\u00b2:</b> Not calculated")
        self.noec_label = QLabel("<b>NOEC:</b> Not calculated")
        self.loec_label = QLabel("<b>LOEC:</b> Not calculated")
        for label in [self.lc50_label, self.slope_label, self.r_squared_label, self.noec_label, self.loec_label]:
            endpoints_layout.addWidget(label)
        left_side_layout.addWidget(endpoints_group)

        self.invalid_test_label = QLabel("")
        self.invalid_test_label.setFont(QFont(PLOT_PARAMS['font.family'], 12, QFont.Bold))
        self.invalid_test_label.setStyleSheet("color: #d9534f;")
        self.invalid_test_label.setAlignment(Qt.AlignCenter)
        self.invalid_test_label.hide()
        left_side_layout.addWidget(self.invalid_test_label)
        results_layout.addLayout(left_side_layout, 2)

        self.plot_tabs = QTabWidget()
        self.plot_container_mortality = QWidget()
        self.plot_container_hatching = QWidget()
        self.plot_container_malformation = QWidget()
        

        for container in [self.plot_container_mortality, self.plot_container_hatching, self.plot_container_malformation]:
            container.setLayout(QVBoxLayout())
            container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.plot_tabs.addTab(self.plot_container_mortality, "Mortality Dose-Response")
        self.plot_tabs.addTab(self.plot_container_hatching, "Hatching Rate")
        self.plot_tabs.addTab(self.plot_container_malformation, "Malformation Profile")
        results_layout.addWidget(self.plot_tabs, 3)

        self.loading_overlay = LoadingOverlay(self)
        
        main_layout.addLayout(results_layout)

    def resizeEvent(self, event: QResizeEvent):
        super().resizeEvent(event)
        self.loading_overlay.setGeometry(self.rect())

    def run_analysis(self):
        day_selection = self.day_selector.currentData()
        if not day_selection:
            log.warning("Analysis skipped: No day selected or available.")
            self._clear_results()
            return

        # Cancel any in-flight analysis so its result doesn't overwrite this one
        if self.analysis_worker is not None:
            self.analysis_worker.cancel()

        self._set_ui_enabled(False, "Calculating, please wait...")
        self.analysis_thread = QThread()
        self.analysis_worker = AnalysisWorker(self.manager, day_selection)
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
            self._check_test_validity(summary_df)
            self._populate_table(summary_df)
            conc_unit = self.manager.get_project_info().get("concentration_unit", "unit")
            self._update_endpoint_labels(results.get('lc50_results', {}), results.get('noec_loec_results', {}), conc_unit)
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
        
    def _populate_table(self, summary_df: pd.DataFrame):
        self.table.setRowCount(0)
        summary_df_sorted = summary_df.drop_duplicates(subset=['conc_id']).sort_values(by='conc_value').reset_index(drop=True)
        self.table.setRowCount(len(summary_df_sorted))

        for row_idx, data in summary_df_sorted.iterrows():
            total = data['total']
            mortality = (data['dead'] / total * 100) if total > 0 else 0
            hatching = (data['hatched'] / total * 100) if total > 0 else 0
            malformed = (data['malformed'] / total * 100) if total > 0 else 0
            
            items = [
                QTableWidgetItem(str(data['conc_id'])),
                QTableWidgetItem(str(data['conc_type'])),
                QTableWidgetItem(str(int(total))),
                QTableWidgetItem(str(int(data['dead']))),
                QTableWidgetItem(f"{mortality:.2f}"),
                QTableWidgetItem(f"{hatching:.2f}"),
                QTableWidgetItem(f"{malformed:.2f}")
            ]

            for i in range(2, 7):
                items[i].setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

            for col_idx, item in enumerate(items):
                self.table.setItem(row_idx, col_idx, item)

    def _update_plots(self, results: Dict[str, Any]):
        self._update_plot_tab(self.plot_container_mortality, results.get('mortality_plot_figure'))
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

        if fig:
            canvas = FigureCanvas(fig)
            canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            layout.addWidget(canvas)
        else:
            label = QLabel(message or "Plot not available.")
            label.setAlignment(Qt.AlignCenter)
            label.setFont(QFont(PLOT_PARAMS['font.family'], 12))
            layout.addWidget(label)
            
    def _export_results(self):
        if not self.analysis_results or self.analysis_results.get("empty"):
            QMessageBox.warning(self, "No Data", "No data is available to export.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Export Options")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Choose an export format:"))
        btn_csv = QPushButton("Export Raw Data to CSV")
        btn_docx = QPushButton("Generate Full Report (DOCX)")
        btn_box = QDialogButtonBox(QDialogButtonBox.Cancel)
        btn_csv.clicked.connect(lambda: [dialog.accept(), self._export_to_csv()])
        btn_docx.clicked.connect(lambda: [dialog.accept(), self._export_to_docx()])
        btn_box.rejected.connect(dialog.reject)
        layout.addWidget(btn_csv); layout.addWidget(btn_docx); layout.addWidget(btn_box)
        dialog.exec()

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
        self.report_thread = QThread()
        self.report_worker = ReportWorker(self.manager, file_path, self.analysis_results)
        self.report_worker.moveToThread(self.report_thread)
        self.report_thread.started.connect(self.report_worker.run)
        self.report_worker.finished.connect(self._handle_report_finished)
        self.report_thread.finished.connect(self.report_thread.deleteLater)
        self.report_thread.start()

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
        
        self._update_plot_tab(self.plot_container_mortality, None, no_data_msg)
        self._update_plot_tab(self.plot_container_hatching, None, no_data_msg)
        self._update_plot_tab(self.plot_container_malformation, None, no_data_msg)
        
        self.invalid_test_label.hide()

    def _set_ui_enabled(self, enabled: bool, message: str = "Processing..."):
        """Enables or disables UI elements and toggles the loading overlay."""
        self.day_selector.setEnabled(enabled)
        self.recalculate_btn.setEnabled(enabled)
        self.export_btn.setEnabled(enabled)
        
        if not enabled:
            self.loading_overlay.setText(message)
            self.loading_overlay.show()
        else:
            self.loading_overlay.hide()

    def _check_test_validity(self, summary_df: pd.DataFrame):
        self.invalid_test_label.hide()
        threshold = self.manager.get_test_conditions().get("acceptable_mortality", 10.0)
        control_groups = summary_df[summary_df['conc_type'].isin(["Control", "Solvent Control"])]
        if not control_groups.empty:
            total_control = control_groups['total'].sum()
            if total_control > 0:
                mortality = (control_groups['dead'].sum() / total_control) * 100
                if mortality > threshold:
                    self.invalid_test_label.setText(f"TEST INVALID: Control mortality ({mortality:.2f}%) exceeds threshold ({threshold:.0f}%)!")
                    self.invalid_test_label.show()

    def _update_endpoint_labels(self, lc50_results, noec_loec_results, conc_unit="unit", message=None):
        if message:
            base_text = f"<b>{'{label}'}:</b> {message}"
            self.lc50_label.setText(base_text.format(label="LC50"))
            self.slope_label.setText(base_text.format(label="Slope"))
            self.r_squared_label.setText(base_text.format(label="R\u00b2"))
            self.noec_label.setText(base_text.format(label="NOEC"))
            self.loec_label.setText(base_text.format(label="LOEC"))
            return
        
        lc50_val = lc50_results.get('lc50', 'N/A')
        noec_val = noec_loec_results.get('noec', 'N/A')
        loec_val = noec_loec_results.get('loec', 'N/A')
        
        conc_unit_str = f" {conc_unit}"

        lc50_display = lc50_val if isinstance(lc50_val, str) and lc50_val and not lc50_val[0].isdigit() else f"{lc50_val}{conc_unit_str}"
        noec_display = noec_val if isinstance(noec_val, str) and noec_val and not noec_val[0].isdigit() else f"{noec_val}{conc_unit_str}"
        loec_display = loec_val if isinstance(loec_val, str) and loec_val and not loec_val[0].isdigit() else f"{loec_val}{conc_unit_str}"
            
        alpha = noec_loec_results.get('alpha_adjusted', 0.05)
        alpha_str = f"  <span style='color:gray;font-size:small'>(α={alpha:.4f}, Bonferroni)</span>"
        self.lc50_label.setText(f"<b>LC50:</b> {lc50_display}")
        self.slope_label.setText(f"<b>Slope:</b> {lc50_results.get('slope', 'N/A')}")
        self.r_squared_label.setText(f"<b>R\u00b2:</b> {lc50_results.get('r_squared', 'N/A')}")
        self.noec_label.setText(f"<b>NOEC:</b> {noec_display}{alpha_str}")
        self.loec_label.setText(f"<b>LOEC:</b> {loec_display}")