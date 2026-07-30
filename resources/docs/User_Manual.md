<!--
ZebraFET User Guide — source document.
The About dialog serves this file and the PDF beside it from the same tab, so
regenerate the PDF after every edit:

    python tools/build_manual.py
-->

# ZebraFET User Guide

**Software version 2.2.0 — guideline: OECD Test Guideline 236 (2025 edition)**

This guide follows a Fish Embryo Acute Toxicity (FET) test from the first
concentration calculation to the finished report. Each section covers one stage
of the assay and introduces the parts of the software you need at that point.
If you are looking for a particular screen, the stages map one-to-one onto the
entries in the **Go** menu.

The software records observations and computes endpoints. It does not replace
the guideline: read OECD TG 236 itself for the biological requirements. The full
text is available from **Help > OECD TG 236**.

---

## 1. Before you start

The first launch opens a short setup wizard. It asks you to accept the license
and to choose where ZebraFET keeps its data. Your work lives under that folder:

- `projects/` — one subfolder per project, each holding the project database and
  its photographs. This is the folder to back up.
- `registry.db` — the index behind the Projects Hub. It is rebuilt from the
  project folders, so losing it costs you nothing but the recent list.

You can move the data folder later from **File > Change Data Folder**. Existing
projects are not moved; they stay where they are until you import them or move
them yourself.

The diagnostic log, `zebrafet.log`, is kept separately in the location your
operating system reserves for application data, so it survives a change of data
folder. **Help > Open Log Folder** reveals it; you never need its path otherwise.

---

## 2. Planning the test

Start from the Projects Hub — the screen ZebraFET opens on. Choose **Create New
Project** (or **File > New Project**, Ctrl+N).

### Identify the experiment

Fill in the project name, the researcher, the test substance and the unit its
concentrations are expressed in (mg/L, µM, and so on). The unit you enter here
labels every axis, table and endpoint from this point on.

Set the **number of days** the test runs. OECD TG 236 specifies 96 h, which is
four days of observations.

Two collapsible panels, **Substance Details** and **Water Quality &
Conditions**, hold the information the report's Materials and Methods section
draws on: CAS number, molar mass, purity, dilution water, hardness, pH,
temperature, photoperiod. None of it is required to proceed, and all of it can
be added later from **Edit > Project Settings**, but a report is only as
complete as the record behind it.

### Define the concentration groups

Below the identification fields, set how many groups of each kind the test uses:

| Group | Purpose |
|---|---|
| Negative control | Dilution water only. Required — TG 236 validity is judged against it. |
| Concentration | The test substance dilution series. |
| Solvent control | Required whenever a carrier solvent is used. |
| Positive control | 4 mg/L 3,4-dichloroaniline, run at least twice a year. |

The concentration plan table grows as you change these counts. Each row carries
a group ID, its type, its nominal concentration, the number of wells it needs
and a color used to identify it on the plate.

To build a dilution series, enter the highest concentration and a dilution
factor, then press **Generate Series**. The table fills from the top
concentration downward. TG 236 asks for a geometric series with a separation
factor no greater than 2.2; the software does not enforce this, so check the
spacing the generator produced.

Set the wells per group — TG 236 requires 20 embryos per test concentration and
20 in the control — and the number of plates. ZebraFET reports the total embryos
and the minimum plate count the plan needs, and blocks creation if the plates
you asked for cannot hold the plan.

Press **Create Project**. The project folder and its database are created, and
the software moves on to the plate layout.

> The concentration plan stays editable after creation, from the Concentration
> Plan screen. Changing it rebuilds the plate layout and the daily scoring
> sheets, so make structural changes before you start scoring.

---

## 3. Laying out the plates

**Go > Plate Layout** assigns each concentration group to physical wells. The
plate grid matches the format chosen at creation — 96-, 48-, 24-, 12- or 6-well.

Working method:

1. Click a group in the list on the left. It becomes the active brush.
2. Click a well to assign it, or drag across the grid to assign a run of wells.
   Dragging on empty space draws a rectangular selection and assigns everything
   inside it.
3. **Unassign Well** turns the brush into an eraser.
4. Use the plate tabs to move between plates. **Duplicate Plate** copies the
   current layout onto the next plate, which is the fastest way to lay out
   replicate plates.

The counter panel shows assigned wells against planned wells for every group.
The layout is complete when each counter reads its target.

Press **Save Layout** to commit. The status bar confirms how many wells were
written. Until you save, the layout exists only on screen.

Layout changes are undoable: **Edit > Undo** (Ctrl+Z) reverses a plate clear,
duplicate, or assignment.

---

## 4. Scoring each day

**Go > Experiment View** is where the daily observations are recorded. It has a
tab per day, and within a day a tab per plate.

### Recording a well

Click a well in the grid. The editor on the right shows its current record.
Assign one of the five statuses:

| Key | Status | Meaning |
|---|---|---|
| 1 | Live Embryo | Alive, not yet hatched. |
| 2 | Dead Embryo | Dead before hatching. |
| 3 | Live Hatched | Alive and hatched. |
| 4 | Dead Hatched | Dead after hatching. |
| 5 | Absent (use majority) | No embryo present — excluded from the denominator. |

Arrow keys move between wells, so a plate can be scored without touching the
mouse: press the number, press the arrow, repeat.

**Dead Embryo** and **Dead Hatched** reveal the four lethal endpoints of TG 236 —
coagulation of the embryo, lack of somite formation, non-detachment of the tail
and lack of heartbeat. Record every one you observed; the report tabulates them
separately.

For any live embryo, record the sublethal observations that apply. The nine
morphological endpoints are offered as checkboxes:

- Yolk sac oedema
- Pericardial oedema
- Spinal curvature (scoliosis)
- Tail malformation
- Head / jaw malformation
- Fin malformation or absence
- Pigmentation abnormalities
- Uninflated swim bladder
- Developmental delay

The notes field takes free text for anything the checkboxes do not cover.

Every edit is written to the project database as you make it. There is no unsaved
state to lose.

### Water quality

The **Water Quality** button on each day records the physicochemical
measurements TG 236 asks you to monitor: temperature, dissolved oxygen, pH and
conductivity. These appear in the report's monitoring table. Leaving every field
blank clears the day rather than storing an empty row.

### Closing the day

**Review Day** summarizes what changed since the previous day — a check on
transcription before you commit.

**Finalize Day** locks the day and carries irreversible states forward:
an embryo dead on day 2 is dead on days 3 and 4, and hatching does not reverse.
Days must be finalized in order, and the software will not finalize a day whose
records contradict the day before it — a well that was dead and is now alive, for
instance. Correct the inconsistencies it lists, then finalize.

**Reopen Day for Editing** unlocks a finalized day. Because later days were
filled from this one, reopening a day also reopens every finalized day after it;
the confirmation names them.

---

## 5. Documenting with photographs

**Go > Photo Documentation** proposes what is worth photographing on a given
day, based on what you scored. Suggestions come in three kinds:

- **Malformation** — wells showing a recorded sublethal endpoint.
- **Representative Status** — a well typical of its concentration group.
- **Significant Status** — a status that appears in a notable share of a group.

Select a suggestion, then a well within it, and use **Attach Photo** to import
one or more image files. The files are copied into the project folder, so the
project stays self-contained and can be moved or exported without breaking the
links. Thumbnails appear in the gallery below; a photograph can be removed from
there, which deletes the copied file.

Photographs reach the report through the **Appendix: photographic
documentation** section.

---

## 6. Analyzing the results

**Go > Results and Analysis** computes the endpoints. Choose the analysis day —
normally the last — and press **Recalculate**. The analysis runs off the
interface thread; the controls re-enable when it finishes.

### What the software checks first

A red **TEST INVALID** banner appears when an assessed validity criterion of
TG 236 fails: control mortality above the acceptable threshold set in project
settings (10% by default), or, on the final day, control hatching below 80%.
The banner carries the same criteria the report's validity section applies, so
what you see on screen is what the report will say. A failed criterion does not
stop the analysis — the numbers are still computed — but the test does not meet
the guideline.

### Fitting options

- **Model** — auto-selection by AICc, or a fixed 4PL, 3PL (bottom or top fixed)
  or 2PL log-logistic fit.
- **Abbott's correction** — corrects mortality for control response. When it
  applies, the summary table gains an Abbott-corrected column.
- **Control comparison** — whether the reference is the pooled controls, the
  negative control alone, or the solvent control alone. The same reference is
  used for the validity verdict and for the NOEC, so the two cannot disagree.
- **Multiplicity correction** — the adjustment applied to the pairwise
  comparisons behind NOEC and LOEC.

Changing any of these marks the results stale; press **Recalculate** again.

### What you get

The summary table gives, per group: the wells assigned, the embryos actually
scored, deaths, mortality, hatching and malformation rates. Both denominators
are shown because they differ whenever a well was empty — the scored count is
the denominator behind every percentage.

Ten endpoints are reported:

| Endpoint | Note |
|---|---|
| Model | The curve selected or fixed. |
| LC50 | From the fitted curve, with confidence interval. |
| LC50 (Spearman-Kärber) | Trimmed non-parametric estimate, independent of the fit. |
| Slope | Hill slope of the fitted curve. |
| R² | Fit quality. |
| NOEC | Highest concentration not differing from control. |
| LOEC | Lowest concentration differing from control. |
| Trend (Cochran-Armitage) | Test for a monotone dose-response. |
| Sublethal LOEC | Lowest concentration with a raised rate of ≥1 abnormality. |
| Teratogenic Index | LC50/EC50 — separation of lethal from sublethal effect. |

Six figures are available as tabs: Mortality Dose-Response, LC50 Time-Series,
Mortality Time-Course, Fate Composition, Hatching Rate and Malformation Profile.
**LC50 Time-Series** fits the LC50 at every daily timepoint and is computed on
demand from **Analysis > LC50 Time-Series**.

Any group that cannot be evaluated — no scored embryos — is named below the
table rather than silently dropped.

---

## 7. Reporting and export

Everything below is on the **File > Export** menu, and the figures also have
buttons above the plot area.

- **Word Report** — the formatted report: methods, validity assessment, results
  tables, figures and appendices. A picker lets you choose which optional
  sections to include; the test validity assessment and the lethal endpoint
  results are always present. Your selection is remembered for the next report.
- **Analysis Tables (CSV)** — a folder of the computed results: per-group
  summary, endpoints, sublethal tests and effect sizes, ready for a statistics
  package.
- **Raw Data (CSV)** — one row per well observation. This is the input to the
  analysis, not its output, and is the right export for reanalysis elsewhere.
- **Current Figure** — the visible figure as PNG, SVG or PDF at 600 dpi.
  **Edit > Copy Figure** puts the same image on the clipboard.
- **Project Archive (.zfet)** — the whole project, photographs included, as one
  file for sharing or archiving. **File > Import** opens one on another machine.

---

## 8. Keyboard reference

ZebraFET uses each platform's standard sequences, so a few differ between
Windows, Linux and macOS. The menus always show the sequence in force on the
machine you are running; where the table below gives two, the second is macOS.
On macOS, read Ctrl as Command throughout.

| Shortcut | Action |
|---|---|
| Ctrl+N | New project |
| Ctrl+O | Open project |
| Ctrl+W | Close project, return to the Hub |
| Ctrl+S | Flush the database to disk |
| Ctrl+Z | Undo |
| Ctrl+Y / Cmd+Shift+Z | Redo |
| Ctrl+1 … Ctrl+6 | Jump to a stage: Hub, Concentration Plan, Plate Layout, Experiment View, Photo Documentation, Results |
| F5 / Cmd+R | Recalculate the analysis |
| F1 / Cmd+? | This guide |
| Cmd+, | Project settings (macOS; use the Edit menu elsewhere) |
| 1 … 5 | Set the selected well's status |
| Arrow keys | Move between wells |

**About Ctrl+S.** Edits are committed as you make them, so there is no unsaved
work. What Ctrl+S does is flush the database's write-ahead log, leaving the
`.db` file on disk fully up to date. Use it before copying or backing up a
project folder. If the flush fails, the software says so — it will not report a
save it did not perform.

---

## 9. Where things are

| Item | Location |
|---|---|
| Projects | The data folder chosen in setup, under `projects/` |
| Log file | **Help > Open Log Folder** (`zebrafet.log`) |
| Data folder | Shown in the setup summary; change from **File > Change Data Folder** |
| This guide | **Help > User Guide**, also as a PDF beside it |
| OECD TG 236 | **Help > OECD TG 236** |
| Licenses | **Help > Licenses** |

---

## 10. Citing ZebraFET

ZebraFET is released under the GNU General Public License v3.0. Each release is
archived with a DOI; the **About** tab carries the DOI and the repository
address for the version you are running.
