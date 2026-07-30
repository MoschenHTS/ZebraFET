# ZebraFET

**ZebraFET** is a free, open-source desktop application for conducting, managing, and analyzing acute zebrafish (*Danio rerio*) embryo toxicity tests — the Fish Embryo Acute Toxicity (FET) test — following [OECD Test Guideline 236](https://www.oecd.org/en/publications/test-no-236-fish-embryo-acute-toxicity-fet-test_9789264203709-en.html). It runs on Windows, macOS, and Linux.

Developed by **[Henrique Tamanini S. Moschen](https://orcid.org/0000-0002-1920-8915)** at the University of Brasília (UnB), Brazil.

It handles the full experimental workflow: project setup, daily well scoring, photo management, statistical analysis, OECD validity checks, and Word (.docx) report generation — all from a single interface. Test validity is assessed against every OECD TG 236 §9 criterion the recorded data supports, and each is reported only when its inputs exist.

Analysis covers lethal endpoints (LC50 by logistic regression with bootstrap confidence intervals, a distribution-free trimmed Spearman-Karber LC50, the LC50 refitted at every daily timepoint, NOEC/LOEC with Holm-Bonferroni correction, Cochran-Armitage trend) and sublethal ones (per-endpoint malformation testing with false-discovery-rate control, a sublethal NOEC/LOEC, and a teratogenic index), with odds ratios and Wilson confidence intervals throughout. Results export as CSV tables as well as a Word report.

---

## Installation

### Option A — standalone installer (recommended)

Pre-built installers for macOS (.pkg), Windows (.exe), and Linux (.AppImage) are available on the [Releases](https://github.com/MoschenHTS/ZebraFET/releases) page — no Python required.

| Platform | Minimum | Notes |
|---|---|---|
| macOS | **13 (Ventura)** | Apple Silicon only — see below |
| Windows | **10 version 1809 (build 17763)**, 64-bit | |
| Linux | **glibc 2.35** — Ubuntu 22.04+, Debian 12+, RHEL 9+, Fedora 36+ | x86_64 |

These floors come from Qt 6.11, which PySide6 6.11 binds. Intel Macs are not
covered by the installer because the build runs on Apple Silicon; use Option B.

#### The installers are not code-signed

ZebraFET is released without an Apple Developer certificate or a Windows
code-signing certificate, so each operating system will warn on first launch.
The binaries are built in the open by [GitHub Actions](.github/workflows/build-installers.yml)
from the tagged source, and the workflow log shows exactly what produced them.

- **macOS** — right-click the `.pkg` and choose **Open** (double-clicking gives
  no bypass), or clear the quarantine flag:
  `xattr -dr com.apple.quarantine /path/to/ZebraFET-macOS.pkg`
- **Windows** — SmartScreen shows "Windows protected your PC": choose
  **More info → Run anyway**. Some antivirus products flag PyInstaller output as
  a false positive.
- **Linux** — make the AppImage executable: `chmod +x ZebraFET-x86_64.AppImage`

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
2. Score wells daily (Day 1 onwards, for as many days as the test runs) — live/dead/coagulated/hatched status plus sublethal observations. Finalize each day when it is fully scored; undo and redo are available throughout.
3. Optionally add photos per well.
4. Optionally record daily water-quality readings (temperature, dissolved oxygen, pH, conductivity).
5. Run the analysis to compute LC50, NOEC/LOEC, sublethal endpoints, and OECD validity criteria — choosing which control the dose groups are compared against.
6. Export a formatted Word report (choosing which sections to include), the analysis as CSV tables, or individual figures as PNG, SVG or PDF.

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
| Markdown | ≥ 3.5 |

---

## Citation

If you use ZebraFET in your research, please cite:

> Moschen, H. T. S., Zimerman, J., Matos, V. F., Liberal, C. H. C., Bellozi, P. M. Q., de Bem, A. F., & Goulart, J. T. (2026). *ZebraFET — Standardized Zebrafish Embryo Toxicity Test Assistant (OECD TG 236)* (v2.2.0). Zenodo. https://doi.org/10.5281/zenodo.20183712

Or use the metadata in [CITATION.cff](CITATION.cff) and [codemeta.json](codemeta.json). The DOI
above is the Zenodo concept DOI and always resolves to the current release.

---

## Authors

ZebraFET is developed and maintained by **Henrique Tamanini S. Moschen**
([ORCID 0000-0002-1920-8915](https://orcid.org/0000-0002-1920-8915)), Toxinology Laboratory,
Center for Molecular Biotechnology, University of Brasília (UnB), Brazil.

Co-authors: Jeanini Zimerman, Vinicius de Faria Matos, Clara Helena Cubas Liberal,
Paula Maria Quaglio Bellozi (Laboratory of Bioenergetics and Metabolism, Institute of Biology,
University of Brasília), Andreza Fabro de Bem (Brazilian National Institute of Science and
Technology on Neuroimmunomodulation, Oswaldo Cruz Foundation), and Jair Trapé Goulart
(Laboratory of Bioenergetics and Metabolism, Institute of Biology, University of Brasília).

---

## License

ZebraFET is released under the [GNU General Public License v3.0](LICENSE).
