# ZebraFET

**ZebraFET** is a desktop application for conducting, managing, and analysing acute zebrafish embryo toxicity tests (Fish Embryo Toxicity — FET) following [OECD TG 236](https://www.oecd.org/en/publications/test-no-236-fish-embryo-acute-toxicity-fet-test_9789264203709-en.html).

It handles the full experimental workflow: project setup, daily well scoring, photo management, statistical analysis (LC50, NOEC/LOEC), OECD validity checks, and Word/PDF report generation — all from a single interface.

---

## Installation

### Option A — standalone installer (recommended)

Pre-built installers for macOS (.pkg), Windows (.exe), and Linux (.AppImage) are available on the [Releases](https://github.com/MoschenHTS/ZebraFET/releases) page — no Python required.

### Option B — clone and run (developers)

```bash
git clone https://github.com/MoschenHTS/ZebraFET.git
cd ZebraFET
pip install -r requirements.txt
python main.py
```

Requires **Python 3.10+**.

---

## Quick Start

On first launch, a setup wizard guides you through choosing a data directory. From there:

1. Create a new project (chemical name, concentrations, replicates, start date).
2. Score wells daily (Days 0–4) — live/dead/coagulated/hatched status plus sublethal observations.
3. Optionally add photos per well.
4. Run the analysis to compute LC50, NOEC, LOEC, and OECD validity criteria.
5. Export a formatted Word report.

The full User Manual is available inside the app under **Help → About → User Guide**.

---

## Requirements

| Package | Version |
|---------|---------|
| PySide6 | ≥ 6.9 |
| NumPy | ≥ 2.2 |
| SciPy | ≥ 1.15 |
| pandas | ≥ 2.3 |
| matplotlib | ≥ 3.10 |
| Pillow | ≥ 11.2 |
| python-docx | ≥ 1.2 |
| openpyxl | ≥ 3.1 |
| Markdown | ≥ 3.5 |

---

## Citation

If you use ZebraFET in your research, please cite:

> Moschen, H. T. S. et al. (2026). ZebraFET — Standardised Zebrafish Embryo Toxicity Test Assistant (OECD TG 236). *SoftwareX*.

Or use the metadata in [CITATION.cff](CITATION.cff).

---

## License

ZebraFET is released under the [GNU General Public License v3.0](LICENSE).
