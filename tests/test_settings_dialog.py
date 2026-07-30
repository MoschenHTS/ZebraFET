"""
test_settings_dialog.py — The unsaved-changes prompt must mean something.

_is_dirty() compared widget values against raw database rows. The two shapes can
never match: the rows carry an id column, store purity as text and omit species,
while an unset start_date reads back from the date widget as its default. Every
Cancel or close therefore raised "You have unsaved changes", which trains the
operator to dismiss the one prompt that protects their edits.
"""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from src.core.project_manager import ProjectManager
from src.ui.dialogs.project_settings_dialog import ProjectSettingsDialog


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def fresh_project(tmp_path):
    m = ProjectManager.create_new(
        str(tmp_path / "Fresh"), {"project_name": "Fresh", "num_days": 2, "num_plates": 1}
    )
    yield m
    m.close()


@pytest.fixture
def populated_project(tmp_path):
    m = ProjectManager.create_new(
        str(tmp_path / "Populated"),
        {
            "project_name": "Populated",
            "num_days": 4,
            "num_plates": 1,
            "substance": "Copper sulfate",
            "start_date": "2025-06-01",
            "substance_details": {"cas_number": "7758-98-7", "purity": "98"},
            "test_conditions": {"ph": "7.4", "temperature": "26", "acceptable_mortality": 10.0},
            "test_organisms": {"strain": "AB", "source": "In-house"},
            "methodology": {"test_procedure": "Static", "selection_criteria": "<3 hpf"},
        },
    )
    yield m
    m.close()


class TestDirtyState:
    def test_untouched_dialog_on_a_fresh_project_is_clean(self, app, fresh_project):
        dialog = ProjectSettingsDialog(fresh_project)
        assert dialog._is_dirty() is False

    def test_untouched_dialog_on_a_populated_project_is_clean(self, app, populated_project):
        dialog = ProjectSettingsDialog(populated_project)
        assert dialog._is_dirty() is False

    def test_reopening_after_a_save_is_clean(self, app, populated_project):
        first = ProjectSettingsDialog(populated_project)
        first.researcher_input.setText("Dr. Edited")
        first.accept()
        second = ProjectSettingsDialog(populated_project)
        assert second._is_dirty() is False

    @pytest.mark.parametrize(
        "field, value",
        [
            ("researcher_input", "Dr. Someone"),
            ("substance_input", "Zinc"),
            ("conc_unit_input", "µM"),
            ("cas_input", "1234-56-7"),
            ("ph_input", "8.1"),
            ("strain_input", "TU"),
            ("species_input", "Oryzias latipes"),
            ("fertilization_input", "88 %"),
        ],
    )
    def test_editing_a_text_field_is_detected(self, app, populated_project, field, value):
        dialog = ProjectSettingsDialog(populated_project)
        getattr(dialog, field).setText(value)
        assert dialog._is_dirty() is True

    def test_editing_a_spinbox_is_detected(self, app, populated_project):
        dialog = ProjectSettingsDialog(populated_project)
        dialog.days_input.setValue(dialog.days_input.value() + 1)
        assert dialog._is_dirty() is True

    def test_editing_notes_is_detected(self, app, populated_project):
        dialog = ProjectSettingsDialog(populated_project)
        dialog.notes_input.setText("Observed precipitate at the top concentration.")
        assert dialog._is_dirty() is True


class TestSingletonRows:
    """A project created without optional sections still has its rows."""

    @pytest.mark.parametrize(
        "getter", ["get_test_conditions", "get_test_organisms", "get_methodology"]
    )
    def test_row_exists_on_a_fresh_project(self, fresh_project, getter):
        assert getattr(fresh_project, getter)() != {}

    def test_schema_defaults_are_readable(self, fresh_project):
        assert fresh_project.get_test_conditions()["acceptable_mortality"] == 10.0
        assert fresh_project.get_methodology()["test_procedure"] == "Static"


class TestTG236ReportFields:
    """Fields OECD TG 236 §42 requires a report to carry.

    The batch fertilization rate is also a validity criterion (§9a, ≥70%). Both
    are optional: blank means not recorded, and the report omits the criterion
    rather than reporting an absence.
    """

    def test_species_defaults_to_zebrafish(self, app, fresh_project):
        dialog = ProjectSettingsDialog(fresh_project)
        assert dialog.species_input.text() == "Danio rerio"

    def test_fertilization_rate_starts_empty(self, app, fresh_project):
        """Blank is meaningful — it is what keeps the criterion out of the report."""
        dialog = ProjectSettingsDialog(fresh_project)
        assert dialog.fertilization_input.text() == ""

    def test_both_survive_a_save_and_reopen(self, app, populated_project):
        dialog = ProjectSettingsDialog(populated_project)
        dialog.species_input.setText("Oryzias latipes")
        dialog.fertilization_input.setText("88 %")
        dialog.accept()

        reopened = ProjectSettingsDialog(populated_project)
        assert reopened.species_input.text() == "Oryzias latipes"
        assert reopened.fertilization_input.text() == "88 %"
        assert reopened._is_dirty() is False

    def test_a_blank_fertilization_rate_is_stored_as_blank(self, app, populated_project):
        """It must not acquire a default on the way through the dialog."""
        ProjectSettingsDialog(populated_project).accept()
        assert populated_project.get_test_conditions()["fertilization_rate"] == ""
