"""
test_report_generator.py — Structural and functional tests for ReportGenerator.

Verifies that generate_report() produces a valid .docx with the expected document
structure (headings, tables, validity text) using a snapshot dict — no database
or Qt dependency required.
"""
import os

import matplotlib
matplotlib.use("Agg")

import pandas as pd
import pytest
from docx import Document

from src.export.report_generator import ReportGenerator


@pytest.fixture
def minimal_snapshot():
    return {
        "project_name": "Test Project",
        "main_researcher": "Dr. Test",
        "substance": "TestChem",
        "concentration_unit": "mg/L",
        "start_date": "2025-01-01",
        "num_days": 4,
        "num_plates": 1,
        "plate_format": "96-well",
        "report_notes": "",
        "substance_details": {
            "cas_number": "1234-56-7",
            "molecular_weight": "200",
            "purity": "99",
            "supplier": "Sigma",
            "physical_appearance": "White powder",
            "water_solubility": "500 mg/L",
            "iupac_name": "test-iupac",
            "solvent_used": "",
            "positive_control_substance": "",
        },
        "test_organisms": {
            "strain": "AB",
            "source": "In-house",
            "collection_method": "Standard protocol",
        },
        "test_conditions": {
            "water_type": "ISO water",
            "temperature": "26",
            "ph": "7.2",
            "hardness": "250",
            "conductivity": "500",
            "dissolved_oxygen": "8",
            "photoperiod": "16:8",
            "acceptable_mortality": 10.0,
        },
        "methodology": {
            "test_procedure": "Static",
            "solution_preparation": "Serial dilution",
            "selection_criteria": "Standard",
        },
        "concentration_settings": {
            "concentrations": [
                {"id": "ctrl", "type": "Control",   "value": 0.0, "replicates": 1, "wells": 4, "per_plate": True, "color": "#4d4d4d", "sort_order": 0},
                {"id": "s1",   "type": "Substrate", "value": 1.0, "replicates": 1, "wells": 4, "per_plate": True, "color": "#2166ac", "sort_order": 1},
                {"id": "s2",   "type": "Substrate", "value": 2.0, "replicates": 1, "wells": 4, "per_plate": True, "color": "#2166ac", "sort_order": 2},
            ],
            "required_embryos": 20,
            "required_plates": 1,
        },
        "plate_layout": {
            "0": {
                "A1": "ctrl", "A2": "ctrl",
                "B1": "s1",   "B2": "s1",
                "C1": "s2",   "C2": "s2",
            }
        },
        "concentration_map": {
            "ctrl": {"id": "ctrl", "type": "Control",   "value": 0.0, "replicates": 1, "wells": 4, "per_plate": True, "color": "#4d4d4d", "sort_order": 0},
            "s1":   {"id": "s1",   "type": "Substrate", "value": 1.0, "replicates": 1, "wells": 4, "per_plate": True, "color": "#2166ac", "sort_order": 1},
            "s2":   {"id": "s2",   "type": "Substrate", "value": 2.0, "replicates": 1, "wells": 4, "per_plate": True, "color": "#2166ac", "sort_order": 2},
        },
        "plate_dimensions": (8, 12),
        "photos_with_metadata": [],
        "well_data": {},
        "completed_days": [],
    }


@pytest.fixture
def minimal_analysis():
    return {
        "lc50_results": {
            "lc50": "Not Calculated",
            "slope": "Not Calculated",
            "r_squared": "Not Calculated",
            "model_info": {
                "display_name": "4PL (all free)",
                "mode": "manual",
                "bottom": None,
                "top": None,
                "n_free": 4,
                "aic_table": None,
            },
        },
        "noec_loec_results": {"noec": "Not Calculated", "loec": "Not Calculated"},
        "summary_df": pd.DataFrame(),
        "mortality_plot_figure": None,
        "hatching_plot_figure": None,
        "malformation_plot_figure": None,
    }


@pytest.fixture
def full_analysis():
    """Analysis results with a calculated LC50 and non-empty summary table."""
    summary_df = pd.DataFrame([
        {"conc_id": "ctrl", "conc_type": "Control",   "conc_value": 0.0, "total": 4, "dead": 0, "hatched": 2, "malformed": 0},
        {"conc_id": "s1",   "conc_type": "Substrate", "conc_value": 1.0, "total": 4, "dead": 1, "hatched": 1, "malformed": 0},
        {"conc_id": "s2",   "conc_type": "Substrate", "conc_value": 2.0, "total": 4, "dead": 3, "hatched": 0, "malformed": 1},
    ])
    return {
        "lc50_results": {
            "lc50": "1.5000 (95% CI: 1.2000 – 1.8000)",
            "slope": "-2.5000",
            "r_squared": "0.9800",
            "model_info": {
                "display_name": "2PL (bottom=0.0%, top=100.0%)",
                "mode": "manual",
                "bottom": 0.0,
                "top": 100.0,
                "n_free": 2,
                "aic_table": None,
            },
            "_fitted_params": [0.0, 100.0, -2.5, 1.5],
        },
        "noec_loec_results": {"noec": "1.0000", "loec": "2.0000"},
        "summary_df": summary_df,
        "mortality_plot_figure": None,
        "hatching_plot_figure": None,
        "malformation_plot_figure": None,
    }


class TestReportGeneratorStructure:
    def test_generate_report_returns_true(self, tmp_path, minimal_snapshot, minimal_analysis):
        out = str(tmp_path / "report.docx")
        assert ReportGenerator(minimal_snapshot, str(tmp_path), minimal_analysis).generate_report(out)

    def test_output_file_is_created(self, tmp_path, minimal_snapshot, minimal_analysis):
        out = str(tmp_path / "report.docx")
        ReportGenerator(minimal_snapshot, str(tmp_path), minimal_analysis).generate_report(out)
        assert os.path.isfile(out)

    def test_document_has_materials_and_methods_heading(self, tmp_path, minimal_snapshot, minimal_analysis):
        out = str(tmp_path / "report.docx")
        ReportGenerator(minimal_snapshot, str(tmp_path), minimal_analysis).generate_report(out)
        headings = [p.text for p in Document(out).paragraphs if "Heading" in p.style.name]
        assert any("Materials and Methods" in h for h in headings)

    def test_document_has_results_heading(self, tmp_path, minimal_snapshot, minimal_analysis):
        out = str(tmp_path / "report.docx")
        ReportGenerator(minimal_snapshot, str(tmp_path), minimal_analysis).generate_report(out)
        headings = [p.text for p in Document(out).paragraphs if "Heading" in p.style.name]
        assert any("Results" in h for h in headings)

    def test_document_has_appendices_heading(self, tmp_path, minimal_snapshot, minimal_analysis):
        out = str(tmp_path / "report.docx")
        ReportGenerator(minimal_snapshot, str(tmp_path), minimal_analysis).generate_report(out)
        headings = [p.text for p in Document(out).paragraphs if "Heading" in p.style.name]
        assert any("Appendix" in h or "Appendices" in h for h in headings)

    def test_document_has_substance_name_in_title(self, tmp_path, minimal_snapshot, minimal_analysis):
        out = str(tmp_path / "report.docx")
        ReportGenerator(minimal_snapshot, str(tmp_path), minimal_analysis).generate_report(out)
        all_text = " ".join(p.text for p in Document(out).paragraphs)
        assert "TestChem" in all_text

    def test_minimum_table_count(self, tmp_path, minimal_snapshot, minimal_analysis):
        out = str(tmp_path / "report.docx")
        ReportGenerator(minimal_snapshot, str(tmp_path), minimal_analysis).generate_report(out)
        # M&M: substance (1), organisms (2), conditions (3) + plate layout appendix (4)
        assert len(Document(out).tables) >= 4

    def test_no_photos_produces_correct_message(self, tmp_path, minimal_snapshot, minimal_analysis):
        out = str(tmp_path / "report.docx")
        ReportGenerator(minimal_snapshot, str(tmp_path), minimal_analysis).generate_report(out)
        all_text = " ".join(p.text for p in Document(out).paragraphs)
        assert "No photographic documentation" in all_text

    def test_full_analysis_adds_results_and_curve_tables(self, tmp_path, minimal_snapshot, full_analysis):
        out = str(tmp_path / "report.docx")
        ReportGenerator(minimal_snapshot, str(tmp_path), full_analysis).generate_report(out)
        assert len(Document(out).tables) >= 5

    def test_lc50_value_present_in_document_when_calculated(self, tmp_path, minimal_snapshot, full_analysis):
        out = str(tmp_path / "report.docx")
        ReportGenerator(minimal_snapshot, str(tmp_path), full_analysis).generate_report(out)
        all_text = " ".join(p.text for p in Document(out).paragraphs)
        assert "1.5000" in all_text

    def test_noec_loec_present_in_document_when_calculated(self, tmp_path, minimal_snapshot, full_analysis):
        out = str(tmp_path / "report.docx")
        ReportGenerator(minimal_snapshot, str(tmp_path), full_analysis).generate_report(out)
        all_text = " ".join(p.text for p in Document(out).paragraphs)
        assert "NOEC" in all_text
        assert "LOEC" in all_text

    def test_plate_layout_table_uses_snapshot_data(self, tmp_path, minimal_snapshot, minimal_analysis):
        out = str(tmp_path / "report.docx")
        ReportGenerator(minimal_snapshot, str(tmp_path), minimal_analysis).generate_report(out)
        doc = Document(out)
        all_cells = [cell.text for table in doc.tables for row in table.rows for cell in row.cells]
        assert any("ctrl" in c for c in all_cells)


class TestPhotoAppendix:
    """Tests for the TIFF conversion and photo panel compositor."""

    @pytest.fixture
    def snapshot_with_tiff(self, tmp_path, minimal_snapshot):
        """Snapshot wired to a real TIFF placed inside the project directory tree."""
        from PIL import Image

        photo_dir = tmp_path / "photos" / "Day_1"
        photo_dir.mkdir(parents=True)
        tif_path = photo_dir / "A1_Plate1_test.tif"

        # Synthetic RGB TIFF — same mode and format as a real microscopy image
        img = Image.new("RGB", (320, 240), color=(40, 80, 120))
        img.save(str(tif_path), format="TIFF")

        snap = dict(minimal_snapshot)
        snap["photos_with_metadata"] = [
            {"path": "photos/Day_1/A1_Plate1_test.tif", "day": 1, "plate": 0, "well": "A1"}
        ]
        snap["well_data"] = {
            "1": {"0": {"A1": {"status": "Live Embryo", "sublethal_conditions": [], "notes": ""}}}
        }
        return snap

    def test_generate_report_succeeds_with_tiff_photo(self, tmp_path, snapshot_with_tiff, minimal_analysis):
        out = str(tmp_path / "report.docx")
        assert ReportGenerator(snapshot_with_tiff, str(tmp_path), minimal_analysis).generate_report(out)

    def test_photo_appendix_replaces_no_photo_message(self, tmp_path, snapshot_with_tiff, minimal_analysis):
        out = str(tmp_path / "report.docx")
        ReportGenerator(snapshot_with_tiff, str(tmp_path), minimal_analysis).generate_report(out)
        all_text = " ".join(p.text for p in Document(out).paragraphs)
        assert "No photographic documentation" not in all_text

    def test_photo_appendix_includes_day_caption(self, tmp_path, snapshot_with_tiff, minimal_analysis):
        out = str(tmp_path / "report.docx")
        ReportGenerator(snapshot_with_tiff, str(tmp_path), minimal_analysis).generate_report(out)
        all_text = " ".join(p.text for p in Document(out).paragraphs)
        assert "Day 1" in all_text

    def test_tiff_conversion_does_not_leave_temp_files(self, tmp_path, snapshot_with_tiff, minimal_analysis):
        import tempfile
        tmp_before = set(os.listdir(tempfile.gettempdir()))
        out = str(tmp_path / "report.docx")
        ReportGenerator(snapshot_with_tiff, str(tmp_path), minimal_analysis).generate_report(out)
        tmp_after = set(os.listdir(tempfile.gettempdir()))
        new_tmp_pngs = [f for f in (tmp_after - tmp_before) if f.endswith(".png")]
        assert not new_tmp_pngs


class TestGetTestValidityMessage:
    def _make_df(self, control_dead, control_total, treatments=None):
        rows = [{"conc_id": "ctrl", "conc_type": "Control", "conc_value": 0.0,
                 "total": control_total, "dead": control_dead}]
        for i, (cv, d, t) in enumerate(treatments or []):
            rows.append({"conc_id": f"s{i}", "conc_type": "Substrate",
                         "conc_value": cv, "total": t, "dead": d})
        return pd.DataFrame(rows)

    def test_valid_test_at_zero_mortality(self):
        df = self._make_df(0, 10)
        msg = ReportGenerator.get_test_validity_message(df, {"test_conditions": {"acceptable_mortality": 10.0}})
        assert "valid" in msg.lower()
        assert "INVALID" not in msg

    def test_valid_test_at_exact_threshold(self):
        df = self._make_df(1, 10)  # 10.0%
        msg = ReportGenerator.get_test_validity_message(df, {"test_conditions": {"acceptable_mortality": 10.0}})
        assert "valid" in msg.lower()
        assert "INVALID" not in msg

    def test_invalid_test_above_threshold(self):
        df = self._make_df(2, 10)  # 20%
        msg = ReportGenerator.get_test_validity_message(df, {"test_conditions": {"acceptable_mortality": 10.0}})
        assert "INVALID" in msg

    def test_custom_threshold_respected(self):
        df = self._make_df(1, 10)  # 10% > 5% threshold
        msg = ReportGenerator.get_test_validity_message(df, {"test_conditions": {"acceptable_mortality": 5.0}})
        assert "INVALID" in msg

    def test_empty_dataframe_returns_undetermined_message(self):
        msg = ReportGenerator.get_test_validity_message(pd.DataFrame(), {})
        assert "could not be determined" in msg.lower()

    def test_no_control_group_returns_undetermined_message(self):
        df = pd.DataFrame([{"conc_id": "s1", "conc_type": "Substrate",
                             "conc_value": 1.0, "total": 10, "dead": 2}])
        msg = ReportGenerator.get_test_validity_message(df, {})
        assert "could not be determined" in msg.lower()

    def test_solvent_control_counts_as_control(self):
        rows = [
            {"conc_id": "sc", "conc_type": "Solvent Control", "conc_value": 0.0, "total": 10, "dead": 0},
            {"conc_id": "s1", "conc_type": "Substrate",       "conc_value": 1.0, "total": 10, "dead": 2},
        ]
        msg = ReportGenerator.get_test_validity_message(
            pd.DataFrame(rows), {"test_conditions": {"acceptable_mortality": 10.0}}
        )
        assert "valid" in msg.lower()
        assert "INVALID" not in msg

    def test_mortality_percentage_included_in_message(self):
        df = self._make_df(0, 10)
        msg = ReportGenerator.get_test_validity_message(df, {"test_conditions": {"acceptable_mortality": 10.0}})
        assert "0.00%" in msg

    def test_control_and_solvent_control_combined(self):
        rows = [
            {"conc_id": "ctrl", "conc_type": "Control",        "conc_value": 0.0, "total": 10, "dead": 1},
            {"conc_id": "sc",   "conc_type": "Solvent Control", "conc_value": 0.0, "total": 10, "dead": 1},
        ]
        msg = ReportGenerator.get_test_validity_message(
            pd.DataFrame(rows), {"test_conditions": {"acceptable_mortality": 10.0}}
        )
        # Combined: 2 dead / 20 total = 10% → still valid
        assert "valid" in msg.lower()
        assert "INVALID" not in msg
