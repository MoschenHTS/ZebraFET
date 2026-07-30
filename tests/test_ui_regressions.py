"""
test_ui_regressions.py — Defects that presented as silence or as a wrong label.

Each test here stands for something that looked like it worked: a settings file
that moved and took the data directory with it, a group identity carried only in
a color, a summary table headed with columns it would never show, and a species
that could not be set until after the project existed.
"""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

import src.core.utils as utils
from src.core.constants import DEFAULT_SPECIES
from src.core.project_manager import ProjectManager


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class TestSettingsMigration:
    """2.1.4 filed settings under "ZebraFET Hub"; 2.2.0 files them under "ZebraFET".

    Without the carry-over an upgrade lost the configured data directory and
    re-ran the setup wizard on a machine that had already completed it.
    """

    @pytest.fixture
    def redirected_settings(self, qapp, tmp_path, monkeypatch):
        """Point UserScope INI settings at tmp_path so the real names can be used.

        The migration reads the legacy store by org and application name, so it
        can only be exercised through those names; redirecting the search path
        keeps the developer's own settings file out of it.
        """
        original = QSettings(QSettings.Format.IniFormat,
                             QSettings.Scope.UserScope, "x", "y").fileName()
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                          str(tmp_path))
        monkeypatch.setattr(utils, "_settings_migrated", False)
        yield tmp_path
        # Restore the platform default: two levels up from …/org/app.ini
        import os
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                          os.path.dirname(os.path.dirname(original)))

    def test_legacy_keys_are_carried_over(self, redirected_settings):
        legacy = QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                           utils.SETTINGS_ORG, utils._LEGACY_SETTINGS_APP)
        legacy.setValue("setup/data_dir", "/somewhere/custom")
        legacy.setValue("setup/completed", True)
        legacy.sync()

        migrated = utils.app_settings()
        assert migrated.value("setup/data_dir") == "/somewhere/custom"
        assert migrated.fileName() != legacy.fileName()

    def test_existing_keys_are_not_overwritten(self, tmp_path, monkeypatch):
        """A completed setup wins: the copy is a one-time backfill, not a sync."""
        monkeypatch.setattr(utils, "_settings_migrated", False)
        current = QSettings(str(tmp_path / "current.ini"), QSettings.Format.IniFormat)
        current.setValue("setup/completed", True)
        current.setValue("setup/data_dir", "/the/new/one")
        utils._migrate_legacy_settings(current)
        assert current.value("setup/data_dir") == "/the/new/one"

    def test_settings_are_ini_backed(self, qapp):
        """A bare QSettings() resolves to NativeFormat and reads a different file
        from the one the setup wizard writes."""
        assert utils.app_settings().format() == QSettings.Format.IniFormat


class TestWellIdentity:
    """Group membership was conveyed by fill color alone, so two groups were
    indistinguishable to a reader who cannot separate their colors."""

    def test_tooltip_names_the_group(self, qapp):
        from src.ui.components import WellWidget

        well = WellWidget("B7", 1, 1)
        well.update_well_details({"status": "Live Embryo", "notes": "",
                                  "sublethal_conditions": []})
        well.set_concentration(QColor("#E67E22"), "C3")
        assert "B7" in well.toolTip()
        assert "Group: C3" in well.toolTip()

    def test_tooltip_marks_an_unassigned_well(self, qapp):
        from src.ui.components import WellWidget

        well = WellWidget("A1", 1, 1)
        well.update_well_details({"status": "Live Embryo", "notes": "",
                                  "sublethal_conditions": []})
        well.set_concentration(None, None)
        assert "unassigned" in well.toolTip()

    def test_group_survives_an_observation_update(self, qapp):
        """The two halves of the tooltip are written by different callers; a
        later observation must not drop the group line."""
        from src.ui.components import WellWidget

        well = WellWidget("C2", 1, 1)
        well.set_concentration(QColor("#0078D4"), "Co1")
        well.update_well_details({"status": "Dead Embryo", "notes": "coagulated",
                                  "sublethal_conditions": ["Yolk sac oedema"]})
        assert "Group: Co1" in well.toolTip()
        assert "coagulated" in well.toolTip()
        assert "Yolk sac oedema" in well.toolTip()


class TestSummaryTableHeaders:
    def test_headers_before_analysis_match_the_populated_ones(self, qapp, tmp_path):
        """_init_ui built a 7-column table that _populate_table discarded, so
        the operator saw stale headers until the first analysis ran."""
        from src.ui.widgets.results_analysis_widget import ResultsAnalysisWidget

        manager = ProjectManager.create_new(str(tmp_path / "Headers"), {
            "project_name": "Headers", "num_days": 4, "num_plates": 1,
        })
        widget = ResultsAnalysisWidget(manager)
        shown = [widget.table.horizontalHeaderItem(i).text()
                 for i in range(widget.table.columnCount())]
        expected = (ResultsAnalysisWidget.SUMMARY_HEADERS_LEAD
                    + ResultsAnalysisWidget.SUMMARY_HEADERS_TAIL)
        assert shown == expected
        widget.close()

    def test_abbott_column_sits_between_the_two_halves(self, qapp):
        from src.ui.widgets.results_analysis_widget import ResultsAnalysisWidget

        lead = ResultsAnalysisWidget.SUMMARY_HEADERS_LEAD
        tail = ResultsAnalysisWidget.SUMMARY_HEADERS_TAIL
        assert lead[-1] == "Mortality (%)"
        assert tail[0] == "Hatched (%)"
        # Both denominators are shown; they differ whenever a well was empty.
        assert "N assigned" in lead and "N scored" in lead


class TestSpeciesAtCreation:
    """test_organisms was hardcoded empty, so a project silently defaulted to
    Danio rerio and the operator had to find Project Settings to correct it."""

    def test_species_reaches_the_database(self, tmp_path):
        manager = ProjectManager.create_new(str(tmp_path / "Species"), {
            "project_name": "Species", "num_days": 4, "num_plates": 1,
            "test_organisms": {"species": "Oryzias latipes", "strain": "AB",
                               "source": "ZIRC"},
        })
        organisms = manager.get_test_organisms()
        assert organisms["species"] == "Oryzias latipes"
        assert organisms["strain"] == "AB"
        assert organisms["source"] == "ZIRC"

    def test_default_is_still_the_guideline_species(self, tmp_path):
        manager = ProjectManager.create_new(str(tmp_path / "Default"), {
            "project_name": "Default", "num_days": 4, "num_plates": 1,
            "test_organisms": {},
        })
        assert manager.get_test_organisms()["species"] == DEFAULT_SPECIES

    def test_creation_page_offers_the_field(self, qapp):
        from src.ui.pages.project_creation_page import ProjectCreationPage

        page = ProjectCreationPage()
        assert page.species_input.text() == DEFAULT_SPECIES
        page.name_input.setText("N")
        page.user_input.setText("U")
        page.substance_input.setText("S")
        page.species_input.setText("Oryzias latipes")

        emitted = {}
        page.project_created.connect(emitted.update)
        page._finalize_project()
        assert emitted["test_organisms"]["species"] == "Oryzias latipes"
        page.close()


class TestCollapsedPanelsAreOutOfTheTabChain:
    """Panels collapsed by setMaximumHeight(0) stayed in the focus chain, so Tab
    walked through seventeen invisible fields."""

    @pytest.mark.parametrize("section_attr", [
        "substance_section", "conditions_section", "organisms_section",
    ])
    def test_collapsed_content_is_hidden(self, qapp, section_attr):
        from src.ui.pages.project_creation_page import ProjectCreationPage

        page = ProjectCreationPage()
        page.show()
        qapp.processEvents()
        section = getattr(page, section_attr)
        assert not section.isExpanded()
        assert section.content_widget.isHidden()

        section.setExpanded(True)
        qapp.processEvents()
        assert not section.content_widget.isHidden()
        page.close()


class TestCorrectionLabels:
    """The dropdown clipped "Holm-Bonferroni", but the report needs the full name."""

    def test_ui_label_is_short_and_report_label_is_full(self):
        from src.core.biostatistics import (NOEC_CORRECTION_HOLM,
                                            NOEC_CORRECTION_LABELS,
                                            NOEC_CORRECTION_SHORT_LABELS)

        assert NOEC_CORRECTION_SHORT_LABELS[NOEC_CORRECTION_HOLM] == "Holm"
        assert NOEC_CORRECTION_LABELS[NOEC_CORRECTION_HOLM] == "Holm-Bonferroni"

    def test_both_maps_cover_every_correction(self):
        from src.core.biostatistics import (NOEC_CORRECTIONS, NOEC_CORRECTION_LABELS,
                                            NOEC_CORRECTION_SHORT_LABELS)

        assert set(NOEC_CORRECTION_LABELS) == set(NOEC_CORRECTIONS)
        assert set(NOEC_CORRECTION_SHORT_LABELS) == set(NOEC_CORRECTIONS)

    def test_results_dropdown_shows_the_short_names(self, qapp, tmp_path):
        from src.ui.widgets.results_analysis_widget import ResultsAnalysisWidget

        manager = ProjectManager.create_new(str(tmp_path / "Corr"), {
            "project_name": "Corr", "num_days": 4, "num_plates": 1,
        })
        widget = ResultsAnalysisWidget(manager)
        shown = [widget.correction_combo.itemText(i)
                 for i in range(widget.correction_combo.count())]
        assert shown == ["Holm", "Bonferroni"]
        # The stored value is the key, never the display text, so shortening the
        # label cannot change which correction is applied.
        assert widget.correction_combo.currentData() == "holm"
        widget.close()


class TestControlComparisonLabel:
    """The full sentence crowded the controls row; only the p-value belongs on screen."""

    @pytest.fixture
    def widget(self, qapp, tmp_path):
        from src.ui.widgets.results_analysis_widget import ResultsAnalysisWidget

        manager = ProjectManager.create_new(str(tmp_path / "Cmp"), {
            "project_name": "Cmp", "num_days": 4, "num_plates": 1,
        })
        w = ResultsAnalysisWidget(manager)
        yield w
        w.close()

    def test_it_sits_with_the_endpoints(self, widget):
        parent = widget.control_comparison_label.parent()
        assert parent.title() == "Calculated Endpoints"

    def test_shows_the_p_value_and_keeps_the_counts_in_the_tooltip(self, widget):
        summary = ("Control 2/40 (5.0%) vs solvent control 3/40 (7.5%) — "
                   "Fisher p = 0.646, no significant difference")
        widget._update_control_comparison_label(
            {"applicable": True, "p_value": 0.6461, "differ": False, "summary": summary}
        )
        assert "p = 0.646" in widget.control_comparison_label.text()
        assert "5.0%" not in widget.control_comparison_label.text()
        assert widget.control_comparison_label.toolTip() == summary

    def test_differing_controls_are_highlighted(self, widget):
        """Pooling differing controls folds a solvent effect into the baseline."""
        widget._update_control_comparison_label(
            {"applicable": True, "p_value": 0.001, "differ": True, "summary": "x"}
        )
        highlighted = widget.control_comparison_label.text()
        widget._update_control_comparison_label(
            {"applicable": True, "p_value": 0.9, "differ": False, "summary": "x"}
        )
        assert highlighted != widget.control_comparison_label.text()

    def test_inapplicable_shows_the_reason_without_a_tooltip(self, widget):
        widget._update_control_comparison_label(
            {"applicable": False, "summary": "No solvent control in this project."}
        )
        assert "no solvent control in this project" in widget.control_comparison_label.text()
        assert widget.control_comparison_label.toolTip() == ""

    def test_clearing_results_drops_a_stale_p_value(self, widget):
        widget._update_control_comparison_label(
            {"applicable": True, "p_value": 0.001, "differ": True, "summary": "x"}
        )
        widget._clear_results()
        assert "p =" not in widget.control_comparison_label.text()
        assert widget.control_comparison_label.toolTip() == ""


class TestPlateCounters:
    """The counters carried a mark and a trailing phrase that said nothing the
    two numbers do not."""

    @pytest.fixture
    def page(self, qapp, tmp_path):
        from src.ui.pages.plate_layout_page import PlateLayoutPage

        manager = ProjectManager.create_new(str(tmp_path / "Counters"), {
            "project_name": "Counters", "num_days": 4, "num_plates": 1,
            "plate_format": "96-well",
            "concentration_settings": {"concentrations": [
                {"id": "Co1", "type": "Control", "value": 0.0, "wells": 2, "color": "#0078D4"},
            ], "required_embryos": 2, "required_plates": 1},
        })
        p = PlateLayoutPage(manager)
        yield p
        p.close()

    @pytest.mark.parametrize("assigned,expected_status", [
        (0, "warning"), (1, "warning"), (2, "ok"), (3, "error"),
    ])
    def test_text_is_just_the_counts_and_status_drives_the_color(
        self, page, assigned, expected_status
    ):
        page.temp_layout = {"1": {f"A{i + 1}": "Co1" for i in range(assigned)}}
        page.load_counters()
        label = page._counter_labels["Co1"]
        assert label.text() == f"Co1: {assigned} / 2"
        assert label.property("status") == expected_status

    def test_no_marks_or_phrases_survive(self, page):
        page.temp_layout = {"1": {"A1": "Co1", "A2": "Co1"}}
        page.load_counters()
        text = page._counter_labels["Co1"].text()
        for noise in ("complete", "over plan", "still to assign", "✓", "•", "!"):
            assert noise not in text
