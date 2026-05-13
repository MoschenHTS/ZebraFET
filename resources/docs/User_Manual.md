# User Manual – ZebraFET

## 1. Introduction: Welcome to ZebraFET

ZebraFET is a complete software application designed to assist researchers in the conduct, management, and analysis of **acute toxicity tests in fish embryos (FET)**, following OECD TG 236 guidelines.

The goal of the software is to **simplify the workflow**, from initial project setup and concentration planning, through daily data entry and photographic documentation, to statistical analysis and professional report generation.

---

## 2. Getting Started: Main Interface

Upon launching ZebraFET, the first screen is the **Project Hub**, divided into two areas:

- **Navigation Bar (Left):** Switch between software sections. Expanded view shows names, collapsed view shows only icons.  
- **Content Area (Right):** Displays the content of the selected section.

Note: Navigation is disabled until a project is loaded to ensure you always work within the context of a specific experiment.

---

## 3. Workflow: From Start to Finish

Following the order of the navigation bar is the most intuitive way to use the software.

### Step 1: Managing Projects in the Hub

The Project Hub is your starting point. Here you can:

- **Create New Project:** Click the “Create New Project” card.  
- **Browse Existing Project:** Use “Browse for Project” to locate a project on your computer.  
- **Open Recent Project:** Projects in the default `projects` folder appear as interactive cards showing name, researcher, substance, and progress.  
- **Delete Project:** Right-click a project card and select “Delete”.

---

### Step 2: Creating a New Project

Clicking **Create New Project** opens the project setup page.

#### A. Project Metadata

Enter key experiment information:

- **Project Name:** Main project name (required).  
- **User:** Name of the principal investigator.  
- **Substance:** Name of the test substance.  
- **Concentration Unit:** Unit for concentrations (mg/L, µM).  
- **Number of Days:** Duration of the experiment in days.

#### B. Advanced Details (Optional)

Click **Substance Details** or **Water Quality & Conditions** to add more information: CAS number, molecular weight, pH, water hardness, etc. Recommended for a complete report.

#### C. Defining Test Groups

- **Controls (Co):** Number of negative control groups.  
- **Concentrations (C):** Number of test substance concentrations.  
- **Solvent Controls (SC):** Solvent control groups (if applicable).  
- **Positive Controls (PC):** Positive control groups (if applicable).

The **Concentration Plan** table updates automatically.

#### D. Generating Concentrations (Serial Dilution Calculator)

- **Highest Concentration:** Enter the highest concentration in the series.  
- **Dilution Factor:** Enter the dilution factor.  
- Click **Generate Series** to fill the table automatically.

#### E. Replicates and Number of Plates

- **Replicates per Group:** Number of wells per group.  
- **Number of Plates:** Number of 96-well plates.  
- Click **Create Project** when all fields are completed.

---

### Step 3: Designing Plate Layouts

Assign test groups to wells visually.

- **Select Brush:** Click a group (e.g., C1, Co1) on the left.  
- **Paint Wells:** Click and drag across the plate grid. Rectangular selection is supported.  
- **Erase:** Use “Unassign Well” to clear wells.  
- **Switch Between Plates:** Use tabs at the top.  
- **Check Counters:** The panel shows assigned vs. planned wells.  
- **Save Layout:** Click **Save Layout**.

---

### Step 4: Running the Experiment and Entering Data

#### Data Entry Interface

- **Day Tabs:** Switch between experiment days.  
- **Plate Tabs:** Switch between plates for the selected day.  
- **Plate Grid:** Click a well to select.  
- **Well Editor:** Enter data for the selected well.

#### Daily Workflow

1. Select the correct day and plate.  
2. Click the first well (e.g., A1).  
3. In the Well Editor, select embryo status:  
   - Live Embryo  
   - Dead Embryo  
   - Hatched Alive  
   - Dead Hatched  
   - Absent  

4. Mark **Sublethal Conditions** if observed (edema, spinal curvature, etc.).  
5. Add textual notes if needed.  
6. Navigate to the next well using keyboard arrow keys.  
7. Data is saved automatically.

#### Numeric Shortcuts

| Key | Status |
|-----|--------|
| 1   | Live Embryo |
| 2   | Dead Embryo |
| 3   | Live Hatched |
| 4   | Dead Hatched |
| 5   | Absent |

#### Finalizing the Day

- Click **Finalize Day** to lock the day and propagate irreversible states.  
- To make corrections, click **Reopen Day for Editing**.

---

### Step 5: Photographic Documentation

- The system automatically suggests wells to photograph based on entered data.  
- Navigate to the correct day and explore suggestions (Malformations, Representative Status, Significant Status).  
- **Attach Photo:** Select a well and click **Attach Photo…**.  
- Photos appear in the project gallery.

---

### Step 6: Analyzing Results

- Select the final day for analysis.  
- Click **Recalculate** to process data.  
- Analysis outputs:  
  - **Summary Table:** Total embryos, deaths, hatch rate, malformations.  
  - **Calculated Endpoints:** LC50, NOEC, LOEC.  
  - **Interactive Graphs:** Mortality Dose-Response, Hatching Rate, Malformation Profile.

---

### Step 7: Exporting Results

- **Export Raw Data to CSV:** Exports all raw data well by well.  
- **Generate Full Report (DOCX):** Creates a fully formatted Word document with methodology, results, tables, and graphs, suitable for publication.

---

## 4. Settings and Help

At the bottom navigation bar:

- **Save (Ctrl+S):** Manually save the project.  
- **Settings:** Edit project metadata.  
- **Theme Toggle:** Switch between light and dark mode.  
- **Help:** Access software guidance and support.
