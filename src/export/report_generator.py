import io
import os
import logging
import tempfile
from collections import Counter
from datetime import datetime
from typing import Dict, Any, List, Tuple, Union, Optional

import pandas as pd
import matplotlib.pyplot as plt
from docx import Document
from docx.table import Table
from docx.shared import Inches, Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from PIL import Image, ImageDraw, ImageFont, ImageOps

from src.core.project_manager import ProjectManager
from src.core.constants import HATCHED_STATUSES
from src.core.utils import resource_path

log = logging.getLogger(__name__)

# Style Constants 
FONT_NAME = "Times New Roman"
FONT_SIZE_BODY = 12
FONT_SIZE_TABLE = 10
FONT_SIZE_TITLE = 14
MARGIN_CM = 2.54

class ReportGenerator:
    """
    Generates a publication-ready Word document report for a FET test,
    closely following the OECD TG 236 guidelines.
    """

    def __init__(self, manager: ProjectManager, analysis_results: Dict[str, Any]) -> None:
        self.manager = manager
        self.project_data = manager.get_full_project_data()
        self.analysis_results = analysis_results
        self.document: Document = Document()
        self.figure_count: int = 0
        self.table_count: int = 0
        self._setup_document_properties()

    def _setup_document_properties(self) -> None:
        """Sets up the basic properties of the Word document, like margins and fonts."""
        for section in self.document.sections:
            section.top_margin = Cm(MARGIN_CM)
            section.bottom_margin = Cm(MARGIN_CM)
            section.left_margin = Cm(MARGIN_CM)
            section.right_margin = Cm(MARGIN_CM)

        style = self.document.styles['Normal']
        style.font.name = FONT_NAME
        style.font.size = Pt(FONT_SIZE_BODY)
        style.paragraph_format.line_spacing = 1.5
        style.paragraph_format.space_after = Pt(0)
        style.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    def generate_report(self, output_path: str) -> bool:
        """
        Orchestrates the generation of the full report by calling section-specific methods.
        """
        current_section = "Initialization"
        try:
            current_section = "Title Page"
            self._create_title_page()
            
            current_section = "Materials and Methods"
            self._create_materials_and_methods()

            current_section = "Results"
            self._create_results_section()
            
            current_section = "Appendices"
            self._create_appendix_section()

            self.document.save(output_path)
            log.info(f"Report successfully generated at: {output_path}")
            return True
        except Exception as e:
            log.error(f"Failed to generate report at section '{current_section}': {e}", exc_info=True)
            return False
        finally:
            plt.close('all')

    # Title Page #
    def _create_title_page(self) -> None:
        """Creates the main title page of the report."""
        substance = self.project_data.get('substance') or '[Insert Substance Name]'
        title_text = f"Fish Embryo Acute Toxicity (FET) Test (OECD TG 236) Final Report: Toxicity Assessment of {substance}"
        p_title = self.document.add_paragraph()
        p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run_title = p_title.add_run(title_text)
        run_title.font.name = FONT_NAME
        run_title.font.size = Pt(FONT_SIZE_TITLE)
        run_title.bold = True
        p_title.paragraph_format.space_after = Pt(24)
        p_title.paragraph_format.line_spacing = 1.0

        author = self.project_data.get('main_researcher') or '[Insert Researcher Name]'
        p_author = self.document.add_paragraph(f"Principal Investigator: {author}")
        p_author.alignment = WD_ALIGN_PARAGRAPH.CENTER

        try:
            date_str = self.project_data.get("start_date")
            report_date = datetime.fromisoformat(date_str).strftime('%B %d, %Y')
        except (ValueError, TypeError):
            report_date = "[Insert Completion Date]"
        p_date = self.document.add_paragraph(f"Date of Study Completion: {report_date}")
        p_date.alignment = WD_ALIGN_PARAGRAPH.CENTER

        generation_date = datetime.now().strftime('%B %d, %Y')
        p_gen = self.document.add_paragraph(f"Report Generated: {generation_date}")
        p_gen.alignment = WD_ALIGN_PARAGRAPH.CENTER

        self.document.add_page_break()

    # Materials & Methods #
    def _create_materials_and_methods(self) -> None:
        """Creates the comprehensive Materials and Methods section with clear subdivisions."""
        self.document.add_heading("1. Materials and Methods", level=1)
        
        self.document.add_heading("1.1 Test Substance", level=2)
        substance_details = self.project_data.get("substance_details", {})
        details = {
            "Common Name": self.project_data.get("substance") or "[Not specified]",
            "CAS Number": substance_details.get("cas_number") or "[Not specified]",
            "IUPAC Name": substance_details.get("iupac_name") or "[Not specified]",
            "Molecular Weight (g/mol)": substance_details.get("molecular_weight") or "[Not specified]",
            "Purity (%)": substance_details.get("purity") or "[Not specified]",
            "Water Solubility": substance_details.get("water_solubility") or "[Not specified]",
            "Supplier": substance_details.get("supplier") or "[Not specified]",
            "Physical Appearance": substance_details.get("physical_appearance") or "[Not specified]",
        }
        self._add_key_value_table("Physicochemical properties of the test substance.", details)
        
        self.document.add_heading("1.2 Test Organism", level=2)
        organisms = self.project_data.get("test_organisms", {})
        org_details = {
            "Species": "Danio rerio (Zebrafish)",
            "Strain": organisms.get("strain") or "[Not specified]",
            "Source of Brood Stock": organisms.get("source") or "[Not specified]",
        }
        self._add_key_value_table("Details of the test organism.", org_details)
        p = self.document.add_paragraph()
        p.add_run("Egg Collection and Maintenance: ").bold = True
        p.add_run(organisms.get('collection_method') or "[Not specified]")

        self.document.add_heading("1.3 Test Conditions", level=2)
        conditions = self.project_data.get("test_conditions", {})
        cond_details = {
            "Dilution Medium": conditions.get("water_type") or "[Not specified]",
            "Temperature (°C)": conditions.get("temperature") or "[Insert temperature]",
            "pH": conditions.get("ph") or "[Insert pH]",
            "Total Hardness (mg/L CaCO₃)": conditions.get("hardness") or "[Insert hardness]",
            "Conductivity (µS/cm)": conditions.get("conductivity") or "[Insert conductivity]",
            "Dissolved Oxygen": conditions.get("dissolved_oxygen") or "[Insert DO]",
            "Photoperiod (L:D)": conditions.get("photoperiod") or "[Insert photoperiod]",
            "Acceptable Control Mortality (%)": conditions.get("acceptable_mortality", "[Not specified]"),
        }
        self._add_key_value_table("Summary of test conditions.", cond_details)

        self.document.add_heading("1.4 Experimental Design and Exposure", level=2)
        self.document.add_heading("Test Concentrations and Controls", level=3)
        concentrations = self.manager.get_concentrations()
        substrate_concs = [str(c['value']) for c in concentrations if c['type'] == 'Substrate']
        conc_text = ", ".join(substrate_concs) if substrate_concs else "[Not specified]"
        conc_unit = self.project_data.get("concentration_unit", "unit")
        self.document.add_paragraph(f"{len(substrate_concs)} nominal concentrations of the test substance were used: {conc_text} {conc_unit}.")
        
        solvent = self.project_data.get("substance_details", {}).get("solvent_used")
        if solvent:
             self.document.add_paragraph(f"Both a negative control (dilution water only) and a solvent control ({solvent or '[Not specified]'}) were run in parallel.")
        else:
             self.document.add_paragraph(f"A negative control (dilution water only) was run in parallel.")

        positive_controls = [c for c in concentrations if c['type'] == 'Positive Control']
        if positive_controls:
            pc_substance = self.project_data.get("substance_details", {}).get("positive_control_substance", "[Not specified]")
            pc_conc = positive_controls[0]['value']
            self.document.add_paragraph(
                f"A positive control using {pc_substance} at a concentration of {pc_conc} {conc_unit} was included to validate test sensitivity."
            )

        self.document.add_heading("Test Procedure and Exposure System", level=3)
        if concentrations:
            num_replicates = concentrations[0].get('replicates', '[N/A]')
            embryos_per_well = concentrations[0].get('wells', 1)
            design_text = (f"The experimental design consisted of {num_replicates} replicates for each treatment group. "
                           f"Each replicate comprised {embryos_per_well} embryo(s) per well.")
            self.document.add_paragraph(design_text)

        plate_format = self.project_data.get("plate_format", "96-well")
        from src.core.constants import PLATE_FORMATS
        p_rows, p_cols = PLATE_FORMATS.get(plate_format, (8, 12))
        p = self.document.add_paragraph()
        p.add_run("Test Container: ").bold = True
        p.add_run(
            f"{plate_format} multi-well plates "
            f"({p_rows}\u00d7{p_cols} grid, {p_rows * p_cols} wells per plate) were used."
        )

        methodology = self.project_data.get("methodology", {})
        test_procedure = methodology.get('test_procedure', '[Not specified]')
        renewal_text = "Solutions were renewed every 24 hours." if "renewal" in test_procedure else "No solution renewal was performed."
        p = self.document.add_paragraph()
        p.add_run("Exposure Type: ").bold = True
        p.add_run(f"The test was conducted under {test_procedure} conditions. {renewal_text}")

        p = self.document.add_paragraph()
        p.add_run("Solution Preparation: ").bold = True
        p.add_run(methodology.get('solution_preparation') or "[Not specified]")
             
        self.document.add_heading("1.5 Endpoints Assessed", level=2)
        self.document.add_paragraph(
            "Endpoints were assessed daily up to 96 hours post-fertilization (hpf) according to OECD TG 236 criteria. "
            "Lethal endpoints included coagulation of the embryo, lack of somite formation, non-detachment of the tail, and absence of heartbeat. "
            "Sub-lethal endpoints observed included hatching rate and the incidence of morphological abnormalities (e.g., pericardial edema, notochord deformities, body curvature)."
        )

        self.document.add_heading("1.6 Statistical Analysis and Validity Criteria", level=2)
        self.document.add_paragraph(
            "The test was conducted following the OECD Guideline for the Testing of Chemicals, No. 236: Fish Embryo Acute Toxicity (FET) Test. "
            "Data acquisition and analysis were performed using the ZebraFET — Standardized Zebrafish Embryo Toxicity Test Assistant software. "
            "The validity of the test is contingent upon the mortality in the control group(s) not exceeding 10% at the end of the test, as specified in the guideline. "
            "Statistical analyses were performed using Fisher's Exact Test with Bonferroni correction for multiple comparisons to determine the No Observed Effect Concentration (NOEC) and Lowest Observed Effect Concentration (LOEC). "
            "LC50 values were estimated by fitting mortality data to a four-parameter logistic (4PL) model using non-linear regression."
        )

    # Results #
    @staticmethod
    def get_test_validity_message(summary_df: pd.DataFrame, project_data: Dict) -> str:
        """Generates a text statement on the validity of the test based on control mortality."""
        threshold = project_data.get("test_conditions", {}).get("acceptable_mortality", 10.0)
        if summary_df.empty:
            return "Test validity could not be determined due to missing data."

        control_groups = summary_df[summary_df['conc_type'].isin(["Control", "Solvent Control"])]
        if control_groups.empty:
            return "Test validity could not be determined: No control group data found."

        total_control = control_groups['total'].sum()
        if total_control == 0:
            return "Test validity could not be determined: No organisms found in control groups."

        control_mortality = (control_groups['dead'].sum() / total_control) * 100
        if control_mortality <= threshold:
            return (f"The test is considered valid. Mortality in the control group(s) was "
                    f"{control_mortality:.2f}%, which is within the acceptable limit of {threshold:.0f}% specified by OECD TG 236.")
        else:
            return (f"The test is considered INVALID. Mortality in the control group(s) was "
                    f"{control_mortality:.2f}%, exceeding the acceptable limit of {threshold:.0f}% specified by OECD TG 236.")

    def _create_results_section(self) -> None:
        """Creates the Results section, including tables and plots."""
        self.document.add_heading("2. Results", level=1)
        
        self.document.add_heading("2.1 Test Validity Criteria", level=2)
        summary_df = self.analysis_results.get("summary_df", pd.DataFrame())
        validity_text = self.get_test_validity_message(summary_df, self.project_data)
        self.document.add_paragraph(validity_text, style='Normal')

        self.document.add_heading("2.2 Lethal Effects and Calculated Endpoints", level=2)
        self.document.add_paragraph(
            "Cumulative mortality and other sublethal effects were recorded at 96 hours post-fertilization. "
            "The data are summarized in the table below."
        )
        try:
            self._add_results_summary_table(summary_df)
        except Exception as e:
            log.warning(f"Could not create results summary table: {e}")
            self.document.add_paragraph(
                "The results summary table could not be generated due to missing or corrupt data.", style='Normal'
            )

        conc_unit = self.project_data.get("concentration_unit", "unit")
        lc50_results = self.analysis_results.get("lc50_results", {})
        lc50 = lc50_results.get("lc50", "not calculated")
        slope = lc50_results.get("slope", "not calculated")
        r_squared = lc50_results.get("r_squared", "N/A")
        noec = self.analysis_results.get("noec_loec_results", {}).get("noec", "not calculated")
        loec = self.analysis_results.get("noec_loec_results", {}).get("loec", "not calculated")
        # Only write a clean sentence when LC50 was actually calculated (starts with a digit)
        lc50_is_numeric = isinstance(lc50, str) and lc50[:1].isdigit()
        if lc50_is_numeric:
            self.document.add_paragraph(
                f"Based on the 96-hour mortality data, the LC50 was determined to be {lc50} {conc_unit} "
                f"(slope: {slope}; R\u00b2 = {r_squared}). "
                f"The No Observed Effect Concentration (NOEC) was {noec} {conc_unit}, "
                f"and the Lowest Observed Effect Concentration (LOEC) was {loec} {conc_unit}."
            )
        else:
            self.document.add_paragraph(
                f"LC50 calculation: {lc50}. "
                f"The No Observed Effect Concentration (NOEC) was {noec}, "
                f"and the Lowest Observed Effect Concentration (LOEC) was {loec}."
            )

        mortality_fig = self.analysis_results.get("mortality_plot_figure")
        if mortality_fig:
            self._add_plot_to_document(
                mortality_fig,
                caption="Concentration-response curve for mortality of Danio rerio embryos after 96 hours of exposure.",
                width=Inches(3.5),
            )

        hatching_fig = self.analysis_results.get("hatching_plot_figure")
        if hatching_fig:
            self.document.add_heading("2.3 Hatching Rate", level=2)
            self.document.add_paragraph(
                "The hatching rate of embryos was assessed at 96 hours post-fertilization across all treatment groups."
            )
            self._add_plot_to_document(
                hatching_fig,
                caption="Hatching rate per treatment group at 96 hours post-fertilization.",
                width=Inches(3.5),
            )

        malformation_fig = self.analysis_results.get("malformation_plot_figure")
        if malformation_fig:
            self.document.add_heading("2.4 Malformation Profile", level=2)
            self.document.add_paragraph(
                "The incidence of specific morphological abnormalities was recorded to generate a malformation profile. A detailed breakdown of malformation frequencies is provided in the Appendices."
            )
            self._add_plot_to_document(
                malformation_fig,
                caption="Malformation profile detailing the incidence of sublethal effects per group.",
                width=Inches(5.0),
            )

    # Appendix Section #
    def _create_appendix_section(self) -> None:
        """Creates the appendix section with supplementary data."""
        self.document.add_page_break()
        self.document.add_heading("3. Appendices", level=1)

        self.document.add_heading("Appendix A: Experimental Plate Layout", level=2)
        self._add_plate_layout_appendix()

        self.document.add_heading("Appendix B: Raw Observational Data", level=2)
        self._add_raw_data_appendix()

        self.document.add_heading("Appendix C: Malformation Frequency", level=2)
        self._add_malformation_frequency_appendix()

        self.document.add_heading("Appendix D: Photographic Documentation", level=2)
        self._create_photo_appendix()
        
    def _add_plate_layout_appendix(self) -> None:
        """Adds the plate layout map to the appendix."""
        plate_layouts = self.manager.get_all_plate_layouts()
        if not plate_layouts:
            self.document.add_paragraph("No plate layout data available.", style='Normal')
            return

        p_rows, p_cols = self.manager.get_plate_dimensions()

        conc_map = self.manager.get_concentration_map()
        conc_unit = self.project_data.get("concentration_unit", "")

        for plate_idx, layout in plate_layouts.items():
            self.table_count += 1
            p = self.document.add_paragraph(f"Table {self.table_count}. Well assignment map for Plate {int(plate_idx) + 1}.")
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_after = Pt(6)

            table = self.document.add_table(rows=p_rows + 1, cols=p_cols + 1)
            table.style = 'Table Grid'

            for j in range(1, p_cols + 1): table.cell(0, j).text = str(j)
            for i in range(1, p_rows + 1): table.cell(i, 0).text = chr(64 + i)

            for well_id, group_id in layout.items():
                row_char = well_id[0]
                col_num = int(well_id[1:])
                row_idx = ord(row_char) - 64
                conc = conc_map.get(group_id, {})
                value = conc.get("value", "")
                display = f"{group_id}\n{value} {conc_unit}".strip() if value != "" else group_id
                table.cell(row_idx, col_num).text = display

            self._style_table(table, font_size=Pt(8))

    def _add_raw_data_appendix(self) -> None:
        """Adds a table with the raw, day-by-day observational data."""
        well_data = self.project_data.get("well_data", {})
        if not well_data:
            self.document.add_paragraph("No raw observational data available.", style='Normal')
            return

        all_data = []
        conc_map = self.manager.get_concentration_map()
        plate_layout = self.manager.get_all_plate_layouts()

        for day, plates in well_data.items():
            for plate_idx, wells in plates.items():
                for well_id, data in wells.items():
                    group_id = plate_layout.get(str(plate_idx), {}).get(well_id, 'N/A')
                    conc_info = conc_map.get(group_id, {})
                    row = {
                        "Day": int(day), "Plate": int(plate_idx) + 1, "Well": well_id,
                        "Group": group_id, "Conc.": conc_info.get('value', 'N/A'),
                        "Status": data.get("status", "Normal"),
                        "Hatched": "Yes" if data.get("status") in HATCHED_STATUSES else "No",
                        "Malformations": ", ".join(data.get("sublethal_conditions", [])),
                        "Notes": data.get("notes", "")
                    }
                    all_data.append(row)
        
        if not all_data:
            log.warning("No valid observational data found for raw data appendix.")
            return

        df = pd.DataFrame(all_data).sort_values(by=["Day", "Plate", "Well"])
        
        self.table_count += 1
        p = self.document.add_paragraph(f"Table {self.table_count}. Raw observational data recorded daily for each well.")
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(6)

        table = self.document.add_table(rows=1, cols=len(df.columns))
        table.style = 'Table Grid'
        for i, col_name in enumerate(df.columns): table.cell(0, i).text = col_name

        for _, row in df.iterrows():
            row_cells = table.add_row().cells
            for i, val in enumerate(row): row_cells[i].text = str(val)

        self._style_table(table, font_size=Pt(8))

    def _add_malformation_frequency_appendix(self) -> None:
        """Adds a table detailing the frequency of specific malformations per group."""
        final_day = str(self.project_data.get("num_days", 4))
        well_data_final_day = self.project_data.get("well_data", {}).get(final_day, {})
        
        if not well_data_final_day:
            self.document.add_paragraph("No malformation data available for the final observation day.", style='Normal')
            return

        malformation_counts: Dict[str, Counter] = {}
        plate_layout = self.manager.get_all_plate_layouts()
        all_malformations = set()

        for plate_idx, wells in well_data_final_day.items():
            for well_id, data in wells.items():
                group_id = plate_layout.get(str(plate_idx), {}).get(well_id, 'N/A')
                if group_id not in malformation_counts: malformation_counts[group_id] = Counter()
                
                malformations = data.get("sublethal_conditions", [])
                malformation_counts[group_id].update(malformations)
                all_malformations.update(malformations)

        if not all_malformations:
            self.document.add_paragraph("No specific malformations were recorded during the experiment.", style='Normal')
            return
            
        sorted_malformations = sorted(list(all_malformations))
        df_data = []
        for group_id, counts in sorted(malformation_counts.items()):
            row = {"Group": group_id}
            row.update({malf: counts.get(malf, 0) for malf in sorted_malformations})
            df_data.append(row)
        
        df = pd.DataFrame(df_data).set_index("Group")

        self.table_count += 1
        p = self.document.add_paragraph(f"Table {self.table_count}. Frequency of specific malformations observed at 96 hpf.")
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(6)

        table = self.document.add_table(rows=1, cols=len(df.columns) + 1)
        table.style = 'Table Grid'
        table.cell(0, 0).text = "Group ID"
        for i, col_name in enumerate(df.columns): table.cell(0, i + 1).text = col_name

        for group_id, row_data in df.iterrows():
            row_cells = table.add_row().cells
            row_cells[0].text = str(group_id)
            for i, val in enumerate(row_data): row_cells[i + 1].text = str(val)

        self._style_table(table, font_size=Pt(9))
        
    def _create_photo_appendix(self) -> None:
        """Adds day-separated panels of representative photos to the appendix."""
        photos_with_metadata = self.manager.get_all_photos_with_metadata()
        if not photos_with_metadata:
            self.document.add_paragraph("No photographic documentation was attached to this project.", style='Normal')
            return

        # Group photos by day
        photos_by_day: Dict[int, List[Dict[str, Any]]] = {}
        for meta in photos_with_metadata:
            day = int(meta.get("day", 0))
            photos_by_day.setdefault(day, []).append(meta)

        conc_map = self.manager.get_concentration_map()
        plate_layouts = self.manager.get_all_plate_layouts()

        # Generate one panel per day
        for day, day_photos in sorted(photos_by_day.items()):
            panel_buffer, processed_photos = self._create_photo_panel(day_photos)
            if not panel_buffer:
                continue

            # Add the figure
            base_caption = f"Representative images of embryos recorded at Day {day}. "
            self._add_plot_to_document(panel_buffer, caption=base_caption, width=Inches(6.5))

            # Add the detailed, continuous legend
            legend_p = self.document.paragraphs[-1] # Get the caption paragraph just added
            for i, meta in enumerate(processed_photos):
                label = chr(65 + i)
                group_id = plate_layouts.get(str(meta['plate']), {}).get(meta['well'], "N/A")
                conc_info = conc_map.get(group_id, {})
                conc_value = conc_info.get('value', 'N/A')
                conc_unit = self.project_data.get("concentration_unit", "unit")

                run_label = legend_p.add_run(f"{label}: ")
                run_label.bold = True
                run_label.font.size = Pt(9)
                run_label.font.name = FONT_NAME

                well_info = (
                    self.project_data.get("well_data", {})
                    .get(str(meta["day"]), {})
                    .get(str(meta["plate"]), {})
                    .get(meta["well"], {})
                )
                status = well_info.get("status", "")
                conditions = well_info.get("sublethal_conditions", [])
                status_str = f"; Status: {status}" if status else ""
                cond_str = f"; Conditions: {', '.join(conditions)}" if conditions else ""

                legend_text = f"Plate {meta['plate']+1}, Well {meta['well']} ({group_id}, {conc_value} {conc_unit}){status_str}{cond_str}. "
                run_details = legend_p.add_run(legend_text)
                run_details.font.size = Pt(9)
                run_details.font.name = FONT_NAME
                
            # Final styling for the legend paragraph
            legend_p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            legend_p.paragraph_format.space_before = Pt(0)
            legend_p.paragraph_format.space_after = Pt(12)


    # Tables #
    def _style_table(self, table: Table, font_size: Pt = Pt(FONT_SIZE_TABLE)) -> None:
        """Applies consistent styling (font, alignment, bold header) to a table."""
        for i, row in enumerate(table.rows):
            for cell in row.cells:
                cell.vertical_alignment = WD_ALIGN_PARAGRAPH.CENTER
                for paragraph in cell.paragraphs:
                    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    for run in paragraph.runs:
                        run.font.name = FONT_NAME
                        run.font.size = font_size
                        if i == 0:
                            run.bold = True

    def _add_key_value_table(self, title: str, data_dict: Dict[str, Any]) -> None:
        """Helper to create a simple two-column key-value table."""
        self.table_count += 1
        p = self.document.add_paragraph(f"Table {self.table_count}. {title}")
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(6)

        table = self.document.add_table(rows=1, cols=2)
        table.style = 'Table Grid'
        hdr_cells = table.rows[0].cells
        hdr_cells[0].text, hdr_cells[1].text = 'Parameter', 'Value'
        
        for key, value in data_dict.items():
            row_cells = table.add_row().cells
            row_cells[0].paragraphs[0].text = str(key)
            
            p_val = row_cells[1].paragraphs[0]
            run = p_val.add_run(str(value))
            if key == "Species":
                run.italic = True
        
        self._style_table(table)

    def _add_results_summary_table(self, summary_df: pd.DataFrame) -> None:
        """Creates the main results summary table."""
        if summary_df is None or summary_df.empty:
            self.document.add_paragraph(
                "Results summary data is unavailable. Ensure all experiment days have been "
                "recorded and analysis has been run before generating the report."
            )
            return

        self.table_count += 1
        p = self.document.add_paragraph(
            f"Table {self.table_count}. Summary of lethal and sublethal effects at 96 hours post-fertilization."
        )
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(6)
        
        report_df = summary_df.copy()
        report_df["Mortality (%)"] = (report_df["dead"] / report_df["total"] * 100).fillna(0)
        report_df["Hatched (%)"] = (report_df["hatched"] / report_df["total"] * 100).fillna(0)
        report_df["Malformed (%)"] = (report_df["malformed"] / report_df["total"] * 100).fillna(0)

        conc_unit = self.project_data.get("concentration_unit", "unit")
        report_df = report_df.rename(columns={"conc_id": "Group ID"})
        cols_to_show = ["Group ID", "conc_value", "total", "dead", "Mortality (%)", "Hatched (%)", "Malformed (%)"]
        report_df = report_df[cols_to_show]


        table = self.document.add_table(rows=1, cols=len(cols_to_show))
        table.style = 'Table Grid'
        header_map = {"conc_value": f"Conc. ({conc_unit})", "total": "N (embryos)"}
        for i, col_name in enumerate(report_df.columns):
            table.cell(0, i).text = header_map.get(col_name, col_name)

        for _, row in report_df.iterrows():
            row_cells = table.add_row().cells
            for i, (col_name, val) in enumerate(row.items()):
                if isinstance(val, float) and '%' in report_df.columns[i]:
                    row_cells[i].text = f"{val:.2f}"
                elif col_name == 'conc_value':
                    row_cells[i].text = f"{val:.4f}".rstrip('0').rstrip('.')
                elif isinstance(val, (int, float)):
                    row_cells[i].text = str(int(val))
                else:
                    row_cells[i].text = str(val)
                    
        self._style_table(table)

    def _set_table_font(self, table: Table, font_size: Pt = Pt(FONT_SIZE_TABLE)) -> None:
        """Applies consistent font settings to all cells in a table."""
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:
                        run.font.name = FONT_NAME
                        run.font.size = font_size

    # Figures #
    def _add_plot_to_document(self, figure: Union[plt.Figure, io.BytesIO], caption: str, width=Inches(6.0)) -> None:
        """Adds a matplotlib figure to the document with a caption."""
        self.figure_count += 1
        mem_fig = io.BytesIO()
        if isinstance(figure, plt.Figure):
            figure.savefig(mem_fig, format='png', dpi=300, bbox_inches='tight')
            plt.close(figure) # Close the figure to free up memory
            mem_fig.seek(0)
        else:
            mem_fig.seek(0) # Ensure buffer is at the start
            mem_fig = figure

        p = self.document.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run().add_picture(mem_fig, width=width)
        
        p_caption = self.document.add_paragraph() # Empty paragraph for the caption
        p_caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_caption.paragraph_format.space_before = Pt(6)
        p_caption.paragraph_format.space_after = Pt(12)
        
        # Add "Figure X." in bold, then the rest of the caption
        run_fig_num = p_caption.add_run(f"Figure {self.figure_count}. ")
        run_fig_num.bold = True
        run_fig_num.font.size = Pt(10)
        run_fig_num.font.name = FONT_NAME
        
        run_caption = p_caption.add_run(caption)
        run_caption.font.size = Pt(10)
        run_caption.font.name = FONT_NAME
        
    # Photo Panel #
    def _create_photo_panel(self, photos_meta: List[Dict[str, Any]], max_images: int = 12, columns: int = 3
                        ) -> Tuple[Optional[io.BytesIO], List[Dict[str, Any]]]:
        """Creates a composite image panel from individual well photos, maintaining aspect ratio."""
        paths_to_process = photos_meta[:max_images]
        if not paths_to_process:
            return None, []

        processed_images = []
        temp_files = []
        thumb_width = 400 
        font = None
        font_candidates = [
            resource_path("resources/fonts/Inter/static/Inter_18pt-Bold.ttf"),
            "arialbd.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]
        for font_path in font_candidates:
            try:
                font = ImageFont.truetype(font_path, 50)
                break
            except (IOError, OSError):
                continue
        if font is None:
            log.warning("No suitable bold font found. Using default font.")
            font = ImageFont.load_default(size=50)

        # 1. Load, resize, and label all images first
        for i, meta in enumerate(paths_to_process):
            try:
                img_path = self.manager.get_full_photo_path(meta['path'])
                
                # Handle TIFF conversion
                ext = os.path.splitext(img_path)[1].lower()
                if ext in ['.tif', '.tiff']:
                    with Image.open(img_path) as im:
                        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                            im.save(tmp.name, "PNG")
                            img_path_to_open = tmp.name
                            temp_files.append(tmp.name)
                else:
                    img_path_to_open = img_path
                
                with Image.open(img_path_to_open) as img:
                    img = img.convert("RGBA")
                    
                    # Resize proportionally to fixed width
                    w_percent = (thumb_width / float(img.size[0]))
                    h_size = int((float(img.size[1]) * float(w_percent)))
                    img = img.resize((thumb_width, h_size), Image.Resampling.LANCZOS)

                    # Draw label directly on the resized image
                    draw = ImageDraw.Draw(img)
                    label = chr(65 + i)
                    padding = 15
                    x, y = padding, padding
                    
                    outline_color = (255, 255, 255)
                    fill_color = (0, 0, 0)
                    
                    # Create a 2px outline for better contrast
                    for offset in [(-2,-2), (-2,2), (2,-2), (2,2)]:
                        draw.text((x+offset[0], y+offset[1]), label, font=font, fill=outline_color)
                    draw.text((x, y), label, font=font, fill=fill_color)
                    
                    processed_images.append(img)

            except Exception as e:
                log.warning(f"Could not process image {meta['path']}: {e}")
                continue
        
        # Cleanup temporary files
        for f in temp_files:
            try: os.remove(f)
            except OSError as e: log.warning(f"Could not remove temp file {f}: {e}")

        if not processed_images:
            return None, []

        # 2. Calculate panel dimensions
        rows = (len(processed_images) + columns - 1) // columns
        panel_width = columns * thumb_width
        
        row_heights = []
        for r in range(rows):
            images_in_row = processed_images[r*columns : (r+1)*columns]
            if images_in_row:
                max_h = max(img.height for img in images_in_row)
                row_heights.append(max_h)
        
        panel_height = sum(row_heights)

        # 3. Create panel and paste images
        panel = Image.new('RGBA', (panel_width, panel_height), (255, 255, 255, 255))
        
        current_y = 0
        for r in range(rows):
            images_in_row = processed_images[r*columns : (r+1)*columns]
            current_x = 0
            for img in images_in_row:
                panel.paste(img, (current_x, current_y))
                current_x += img.width
            if images_in_row:
                current_y += row_heights[r]

        # 4. Save to buffer and return
        buffer = io.BytesIO()
        panel.save(buffer, format='PNG')
        buffer.seek(0)
        return buffer, paths_to_process