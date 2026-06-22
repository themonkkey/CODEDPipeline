# Stress Test Report — New PDF Batch
**CODEDPipeline PDF Table Extraction — 15-PDF Stress Test**
Date: 2026-06-22

---

## 1. Executive Summary

This batch tests the CODEDPipeline against 15 government statistical PDFs spanning RBI handbooks, economic surveys, census documents, NCRB crime reports, and PLFS employment data. The pipeline demonstrates strong numeric fidelity — when it extracts data rows, the values are almost always correct — but structural defects in column naming, wide-table handling, and TOC/front-matter filtering consistently degrade usability. Of 15 PDFs, one (RBI Annual Report) is a hard failure because the input file is an HTML redirect page, not a PDF. Of the remaining 14, only HCES 2023-24 and Table 1.1 (Economic Survey) approach acceptable output, and even those suffer from garbled column headers. The pipeline's core extraction and stitching engine is fundamentally sound; the defects are concentrated in pre/post-processing layers.

The two biggest wins are numeric accuracy and multi-page stitching. Row values across the entire corpus — NSDP figures, GNI time series, LFPR rates, crime incidence counts — match source PDFs exactly when sampled. Stitching correctly spans hundreds of rows across page boundaries (table_19 in the RBI Handbook runs 121 rows over multiple pages; table_13 in NCRB Part 1 is complete). The regression suite confirms these capabilities are stable: all six guards (DES, DARPG, PLFS p11, NFHS-6, NFHS-5, FR375) pass green.

The three biggest problems are: (1) wide-table column header collapse — state names, year labels, and semantic headers are systematically mangled into snake_case garbage strings or dropped to `col` placeholders, affecting the majority of government statistical tables in this corpus; (2) TOC and front-matter pages extracted as data tables, inflating table counts and burying real data; (3) Kruti Dev / Hindi font encoding producing garbled Unicode throughout bilingual documents, corrupting column names, table names, and cell values. These three defects are independent, fixable, and should be prioritized in that order.

---

## 2. Results Table

| PDF | Type | Pages Tested | Found | Passed | Stitched | col_N | Orphans | Grade |
|---|---|---|---|---|---|---|---|---|
| RBI Handbook 2024-25 | Time-series, state-wise | 30 / 446 | 22 | 22 | 17 | 0 | 3 | C |
| RBI Annual Report 2024-25 | Annual report | 0 / 0 | 0 | 0 | 0 | 0 | 0 | F |
| Agriculture Output 2025 | State-level, wide | 385 / 385 | 364 | 363 | 354 | 5 | 231 | C |
| Census 2011 Garhwal | District handbook | 25 / 1274 | 2 | 2 | 2 | 0 | 0 | D |
| Econ Survey Table 1.1 | GNI time-series | 3 / 3 | 4 | 4 | 2 | 0 | 0 | B |
| Econ Survey Table 1.7 | GDP components | 3 / 3 | 4 | 4 | 3 | 0 | 0 | C |
| Econ Survey Table 1.18 | Crop production | 2 / 2 | 0 | 0 | 0 | 0 | 0 | F |
| Economic Survey Hindi | Hindi statistical appendix | 30 / 233 | 33 | 33 | 19 | 0 | 3 | D |
| HCES 2023-24 | Household survey | 11 / 11 | 7 | 7 | 7 | 1 | 0 | B |
| NAS 2025 | Statement list | 3 / 3 | 2 | 2 | 2 | 0 | 0 | C |
| NCRB ADSI 2023 | Accidental deaths | 20 / 298 | 6 | 6 | 6 | 0 | 0 | C |
| NCRB Crime 2023 Part 1 | Crime statistics | 20 / 546 | 12 | 12 | 12 | 0 | 0 | D |
| NCRB Crime 2023 Part 2 | Crime statistics | 20 / 542 | 13 | 13 | 13 | 0 | 0 | C |
| PLFS 2023-24 | Employment survey | 10 / 10 | 6 | 6 | 6 | 0 | 0 | B |
| PLFS Calendar 2024 | Employment indicators | 6 / 6 | 4 | 4 | 4 | 0 | 0 | C |

---

## 3. Per-PDF Deep Dive

### RBI Handbook of Statistics 2024-25 — Grade: C

The pipeline extracted all 22 tables and stitched 17. Numeric row data is the clear win: the NSDP time series (table_19) correctly spans 1994-95 through 2024-25 at 121 rows, with values like Andhra Pradesh 1994-95 NSDP = 61789 (current prices) confirmed against source. Missing/suppressed values are faithfully preserved as `-` rather than zero or null, and year labels including qualified labels like `2023-24 (1st RE)` are intact.

Column headers are the critical failure. State-wise NSDP tables (tables 5, 6, 9, 10) have 34+ columns where PDF headers read `Andhra Pradesh | Arunachal Pradesh | Assam...` — the CSVs show `pradesh | pradesh | col | col`. State names are truncated to the last word or dropped entirely. Multi-row headers in non-state tables produce snake_case column names embedding data values: `the_database_indian_economy_for_version` (table_1, should be "Subscription Price"), `from_national_income_saving_and_employment_2018_19` (table_2, should be "From"). Table names are either empty strings (table_1, table_3, table_4) or raw concatenated PDF text (`CONTENTS Table Title Data Period Page` for table_2).

Three further defects compound the damage. Hindi/Kruti Dev corruption appears at table_10 line 148: `मदक-डंतबी` in what should be English text (the External Debt table title). Contents and index pages (tables 2–5) are extracted as data tables, inflating the count from ~18 actual data tables to 22. Duplicate column names occur: `s_no` appears as both first and last column in table_2.

### RBI Annual Report 2024-25 — Grade: F

Hard failure, but a clean one. The input file at the tested path is not a PDF — it is an HTML bot-challenge redirect page served by the RBI web server. The file begins with `<!DOCTYPE html>` and `pdftopm` emits `Couldn't find trailer dictionary / Couldn't read xref table`. The pipeline correctly produced no output rather than hallucinating tables from invalid input, which is the correct failure behavior.

Action required before any quality assessment is possible: re-download using a browser session or `curl` with appropriate `User-Agent` and cookie headers to bypass the RBI anti-bot gate. A pre-flight magic-bytes check (`%PDF-`) should be added to the ingestion step to catch this class of failure at intake.

### Agriculture Output 2025 — Grade: C

The highest-volume test: 385 pages, 364 tables found, 354 stitched. Numeric yield is high (92%) and spot-checked values are correct — table_4 GVA shares (18.5, 18.2, 18.6, 18.2, 17.7, 18.0, 18.3, 17.6, 18.3, 20.4, 18.9, 18.1, 17.8) match PDF page 14 Table 1 exactly. All 36 States/UTs rows appear in each state-level table, all India totals present, all 13 year periods (2011-12 through 2023-24) captured.

Two structural failures dominate. First, wide-table header collapse: the 13-year column span is concatenated into a single column named `2011_12_2012_13_2013_14_2014_15_2015_16_2016_17_2017_18_2018_19_2019_20_2020_21_2021_22_2022_23_2023_24` with 12 phantom `col` siblings. Data lands in one row correctly but the column structure is broken. Second, Hindi Kruti Dev state name corruption is pervasive: `icfe िंगाल` (West Bengal), `क े jy` (Kerala), `ेघालय` (Meghalaya), `ग ुजरात` (Gujarat) — affecting all ~300 state-level tables.

231 orphan rows indicate sub-table stitching failures. table_111.csv confirms this: rows 1–38 are one crop, rows 39–75 are a second crop with a duplicate header structure merged into one file. The `mostly_empty` flag is the only pass/fail failure mode triggered, but the structural defects make these CSVs extremely difficult to use programmatically.

### Census 2011 Garhwal — Grade: D

Only 25 of 1274 pages were tested, and only 2 tables were extracted — both from front matter (Table of Contents and Acknowledgements), not from the Village Directory Data that constitutes the actual substantive content of this handbook. The real data starts around page 100 and was never reached in this sample.

Of the 2 extracted tables, both have first-row data values promoted to column headers: table_2 shows `shri_ram_jafri` and `joint_director` as column names instead of `serial_number`, `name`, `designation`. Table_1 uses `village_directory_data` as a column name instead of a section descriptor. Row completeness is also poor: table_1 captures only 9 rows (CD Blocks Khirsu, Kot, Pauri) while the second TOC page covering blocks Kaljikhal through Yamkeshwar is not stitched. Table_2 captures only the Supervision subsection (5 rows) from Acknowledgements, missing 5 of 6 sections (Guidance, DCHB Section, Map Unit, Data Centre, ORGI-Data Processing Division). The 25-page sample is simply too small for a 1274-page document.

### Economic Survey Table 1.1 — Grade: B

Best-performing document in the batch for data completeness. All 75 data rows are present (1950-51 through 2025-26 1st AE), numeric values match the PDF exactly (spot-checked: 1950-51 GNI Current=10181, Per Capita Constant=12493; 2025-26 AE GNI Current=35158997, Per Capita Constant=121968), and the table name is correctly identified on both CSVs. Stitching works correctly across 3 pages.

Column naming is the single structural failure: all 10 columns use generic names (`col,col,gross,col,net,col,per,col,col,col`) instead of the 7 semantic headers (Year, GNI Current prices crore, GNI Constant prices crore, NNI Current prices crore, NNI Constant prices crore, Per Capita NNI Current, Per Capita NNI Constant). Three blank separator columns (positions 3, 6, 9) are retained as empty columns, inflating width from 7 to 10. table_4.csv is a redundant duplicate of the final 18 rows already in table_1.csv. Page-boundary header rows appear as data rows at positions 37-42 and 73-78 in table_1.csv.

### Economic Survey Table 1.7 — Grade: C

The 76-row GDP components table (PFCE, GFCE, GFCF, CIS, Valuables, Exports, Imports, Discrepancies, GDP) is numerically correct: 1950-51 PFCE=412309, GDP=496848 confirmed; 2025-26 PFCE=11367565, GDP=20189919 confirmed. Column naming fails completely — every column is `col`. The more serious defect is a missing column: table_3.csv has only 9 columns (GDP is absent), so the 2025-26 row reads `11367565,1796419,6828576,334875,241985,4327569,4840195,133126` with GDP value 20189919 missing. table_4.csv has GDP but drops the Year column and misidentifies the first column as `s_no`, with PFCE values appearing in the `s_no` position. No single CSV contains all 76 rows with all 10 correct columns.

### Economic Survey Table 1.18 — Grade: F

Complete extraction failure on a clean, well-structured English table. The PDF contains a ~50-row, 2-page table with 5 columns (Crops/Groups of Crops, States, Production, Per cent Share, Cumulative Per cent Share). The pipeline found 0 tables, passed 0, produced 0 CSVs. The output path `/tmp/undefined/econ_survey_tab1.18/` suggests an undefined variable in the output path configuration. The colored header row (dark red) and mid-table section-header rows for crop groups (Foodgrains, Oilseeds) are suspected detection failures. Representative rows like `Rice | Telangana | 16.87 | 12.24 | 12.24` appear nowhere in any output.

### Economic Survey Hindi — Grade: D

The most seriously defective document. The pipeline extracted 33 tables and passed all 33, but the majority are TOC index pages misidentified as data tables. Tables 1–4 (14–55 rows each) contain rows like `1.1, ldy राष्ट्रीय Income..., 1` — these are table-of-contents entries (table number, title, page number), not statistical data. The actual Table 1.1 data (7 columns, ~75 years of national income) is entirely absent from the output.

Kruti Dev corruption is severe and pervasive throughout. Hindi text is decoded as garbled ASCII: `ldy` (निवल), `fuoy`, `ewy`, `cqfu;knh`, `o`f¼`, `Hkqxrku`, `fofue;`, `lwpdkad`. Table names show this clearly: table_3 name is `1.9 ldy घरेलू cpr and ldy iwath निर्माण ......`. Column headers default to `col` placeholders across all tables. Two tables with actual numeric data — table_13 (GVA by industry) and table_32/33 (gross capital formation) — do have correct values (1950-51 GVA row: 309778, 71025, 35646, 60308, 36061, 479210 confirmed), but these represent roughly 2 of ~19 stitched tables. Repeating header rows appear mid-table at rows 41-47 and 80-87 of table_13.

### HCES 2023-24 — Grade: B

Second-best document in the batch. All 7 tables extracted with zero failures. Numeric data is perfect across all spot-checked tables: Table 1 row 2023-24 (4,122 / 6,996 / 2,079 / 3,632) matches PDF exactly; Table 2 Andhra Pradesh row (4,870 / 6,782 / 39 / 5,327 / 7,182 / 35) matches exactly; All-India Gini (0.266 / 0.314 / 0.237 / 0.284) matches exactly. All 18 states + All-India present in Table 2 and 5, all social groups and household types correctly captured. No orphan rows, no phantom columns (one col_N flagged in table_2 but confirmed as col_5 in the header, not a data column).

Column naming is the persistent structural problem. Table 2 headers read `average_states_major_state, mpce_2022_23, and, col, mpce_2023_24, col_5, and` where the PDF has `Major State | Average MPCE Rural 2022-23 | Average MPCE Urban 2022-23 | Urban-Rural diff% 2022-23 | Average MPCE Rural 2023-24 | Average MPCE Urban 2023-24 | Urban-Rural diff% 2023-24`. The multi-level header merge (2022-23 / 2023-24 spanning Rural/Urban/diff sub-columns) was not resolved. Table names are prose fragments from surrounding text (`Table 1 below:`, `Table 2 shows the average MPCE along with urban-rural gap for the`) rather than actual PDF table titles.

### NAS 2025 — Grade: C

A narrow 2-column table (Ser. No. + Name of Statement) tested across 3 pages. 59 of 60 rows are present and accurate — row 1 (`Key aggregates of national accounts at current and constant prices`) was consumed as an inferred column header and is absent from the data. The auto-generated second column name (`name_the_key_current_and`) is clearly wrong and should simply be `name_of_statement`. One logical 60-row table was split into table_1 (rows 1-37) and table_2 (rows 38-60) across a page boundary without merging. Table title (`National Accounts Statistics - 2025: List of Statements`) is not captured in either CSV.

### NCRB ADSI 2023 — Grade: C

Six tables extracted from a 298-page document, tested across 20 pages. The standout is table_6: a 57-year time series (1967–2023) with 10 well-named descriptive columns (`accidental_deaths_male`, `suicides_transgender`, etc.), all values correct, transgender column correctly showing `-` for pre-2014 years. Snapshot numeric values are exact (4,44,104 accidental deaths 2023; 1,71,418 suicides 2023). Zero phantom columns, zero orphan rows.

Defects: Hindi Kruti Dev corruption in table_2 cell values — `ैजंजमध्न्ज्` where `State/UT` should appear in at least 9 rows. Snapshot tables (table_4, table_5) have `col,col,col` column headers with year labels 2022 and 2023 not extracted. table_1.csv column header is `contents_disclaimer_limitation_number_accidental_deaths` — a nonsense compound. The 20-page sample misses the bulk of the statistical tables starting around page 14.

### NCRB Crime in India 2023 Part 1 — Grade: D

All 12 extracted tables are from the first 20 pages of a 546-page document — exclusively front matter. Zero actual crime statistics tables (state/UT-wise crime counts, murder, kidnapping, IPC head-wise data) were extracted because the pipeline never reached page 22+ where data tables begin. The 20-page sample is fundamentally insufficient for a 546-page document.

The one wide data table reached — table_10 (Population of 19 Metropolitan Cities) — completely failed: a proper 6-column, 19-row table was collapsed into 2 data rows with all cities concatenated into single cells (`1 Ahmedabad (Gujarat) 63.52 2 Bengaluru (Karnataka) 84.99 ... 9 Jaipur (Rajasthan) 30.73` as one cell value). Tables 1 and 2 are near-duplicate extractions of the same Table of Contents. Table_7 extracted a methodology disclaimer paragraph as a 2-column table.

### NCRB Crime in India 2023 Part 2 — Grade: C

Same front-matter limitation as Part 1: 20 of 542 pages tested, all 13 passing tables are index/contents pages or narrow summaries. The exception is table_13 (Disposal of IPC Cases), which is perfectly extracted — 7 correctly named columns (`s_no`, `crime_head_ipc`, `total_cases_for_investigation`, `cases_charge_sheeted`, `charge_rate`, `total_cases_for_trial`, `total_cases_convicted`, `conviction_rate`), Murder row (45,544 / 24,575 / 85.7 / 272,198 / 7,181 / 37.7) confirmed exact.

Wide-table failure appears in table_9 (projected state population) and table_10 (metropolitan city population), same collapse pattern as Part 1: 36 states concatenated into `532.17 Andhra Pradesh 1 15.65 Arunachal Pradesh 2...` in a single cell. Column names default to content-derived slugs (`abduction_metropolitan_cities`, `feedback_contents_volume`) rather than PDF header text. No Kruti Dev or encoding garbage detected in any CSV.

### PLFS 2023-24 — Grade: B

100% recall: all 6 employment tables extracted from a 10-page press note. All 7 data rows per table (2017-18 through 2023-24) are present and numerically exact — 2023-24 LFPR row (80.2, 47.6, 63.7, 75.6, 28.0, 52.0, 78.8, 41.7, 60.1) matches PDF Table 1 exactly. Zero orphan rows, zero phantom columns, zero stitching failures.

Column naming is structurally broken despite column count being correct. The pipeline split words from multi-row headers without hierarchical reconstruction: `labour_survey`, `force_rural_male`, `participation_rural_female`, `rate_rural_person`, `usual_urban_male`, `status_urban_female`, `for_urban_person`, `persons_rural_urban_male`, `age_years_rural_urban_female`, `and_rural_urban_person` — these are word fragments from the 3-row PDF header (Survey period | Rural/Urban/Rural+Urban | male/female/person), not semantic column names. Kruti Dev corruption appears only in note rows: `(ps+ss)` decoded as `(चे+ेे)`, affecting tables 1, 2, and 3 note text. Note rows are appended as data row 9 in each CSV rather than separated as metadata.

### PLFS Calendar 2024 — Grade: C

Tables 3 and 4 (the actual employment indicator annexures) extract all numeric values correctly — LFPR, WPR, UR for Jan2023-Dec2024 periods confirmed exact (rural male Jan2023=79.8, Jan2024=80.6; urban female UR Jan2023=7.5, Jan2024=6.7). Column count is correct at 10 with no phantom columns.

Column names contain date-period text leaked from multi-row headers: `rural_january` (should be `rural_person`) and `urban_december` (should be `urban_female`). Tables 1 and 2 are narrative press-note paragraphs extracted as structured data tables with columns `col`, `col`, and garbage third-column names (`government_india_ministry_statistics_and_programme`). Table names are empty or truncated fragments: `years and above` and `for persons aged years and above` instead of the full annexure title. Hindi Unicode appears in table_1 row 2 (`च ैत्र 27, शक laor 1947` — Saka calendar date) and row 25 (`ैजंजने(च्ै+ैै)` where PDF shows `Principal and Subsidiary Status (PS+SS)`).

---

## 4. Failure Mode Analysis

### FM-1: Wide Table Column Header Collapse
**Count: 9 of 15 PDFs affected**
The most pervasive and damaging failure mode. Wide tables (5+ columns with state names, year ranges, or demographic breakdowns) consistently produce mangled column headers:
- State-name columns truncated to `pradesh` or dropped to `col` (RBI Handbook tables 5, 6, 9, 10)
- Year-range columns concatenated into one string: `2011_12_2012_13_..._2023_24` (Agriculture Output table_4)
- Date-period text leaked into column names: `rural_january`, `urban_december` (PLFS Calendar)
- Word-fragment concatenation from multi-row headers: `force_rural_male`, `for_urban_person` (PLFS 2023-24)
- Generic `col` placeholders: every column in Econ Survey 1.7, all data tables in Economic Survey Hindi
- Two-panel wide tables collapsed to 2 cells: all city/state population data in NCRB Parts 1 and 2

### FM-2: TOC and Front-Matter False Positives
**Count: 7 of 15 PDFs affected**
Contents pages, acknowledgements, and supervision lists are extracted as data tables:
- RBI Handbook: tables 2–5 are TOC pages
- Agriculture Output: table_1 is an acknowledgements staff list
- Census Garhwal: both extracted tables are front matter (TOC, Acknowledgements)
- Economic Survey Hindi: tables 1–4 (the 4 largest CSVs) are TOC index pages
- NCRB ADSI: tables 1–3 are contents/metadata
- NCRB Crime Parts 1 and 2: all extracted tables are from pages 1-20, exclusively front matter
- NAS 2025: table covers the full document, but lacks a document-level filter

### FM-3: Hindi / Kruti Dev Encoding Corruption
**Count: 6 of 15 PDFs affected (marked hindi_soup=true)**
Kruti Dev font encoding is decoded as garbled Unicode throughout:
- Agriculture Output: state names corrupted (`icfe िंगाल`, `क े jy`, `ेघालय`, `ग ुजरात`)
- Economic Survey Hindi: column headers and table names corrupted (`ldy`, `fuoy`, `ewy`, `cqfu;knh`, `o`f¼`); table_3 name is `1.9 ldy घरेलू cpr and ldy iwath निर्माण ......`
- RBI Handbook: `मदक-डंतबी` in table_10 line 148 (External Debt section)
- NCRB ADSI: `ैजंजमध्न्ज्` in table_2 where `State/UT` should appear
- PLFS 2023-24: `(चे+ेे)` where `(ps+ss)` should appear in note rows
- PLFS Calendar 2024: `ैजंजने(च्ै+ैै)` where `Principal and Subsidiary Status (PS+SS)` should appear

### FM-4: First Data Row Promoted to Column Header
**Count: 5 of 15 PDFs affected**
When the pipeline cannot identify a proper header row, it promotes the first data row as column names:
- Census Garhwal table_2: `shri_ram_jafri`, `joint_director` as headers
- Census Garhwal table_1: `village_directory_data` as header
- NAS 2025 table_1: `Key aggregates of national accounts at current and constant prices` → `name_the_key_current_and`
- NAS 2025 table_2: `Net Capital Stock by industry of use - Households at current prices` → `name_the_net_capital_industry_households`
- Economic Survey Hindi: data value `2018_19` embedded in column name `from_national_income_saving_and_employment_2018_19`

### FM-5: No Tables Found / Complete Extraction Failure
**Count: 2 of 15 PDFs (RBI Annual Report = invalid file; Econ Survey 1.18 = missed detection)**
- RBI Annual Report: file is HTML, not PDF — hard failure, clean (no garbage output)
- Econ Survey Table 1.18: 0 tables found from a clear, well-structured 2-page English table. Colored header rows and mid-table section-header rows (Foodgrains, Oilseeds) likely caused detection failure. Output path `undefined` variable suggests configuration error.

### FM-6: Orphan Rows
**Count: 3 PDFs affected — 231 total orphan rows**
- Agriculture Output 2025: 231 orphan rows — the dominant case. table_111.csv confirmed as two distinct sub-tables stitched into one CSV without separator (rows 1-38 = one crop, rows 39-75 = second crop with duplicate header structure)
- RBI Handbook 2024-25: 3 orphan rows
- Economic Survey Hindi: 3 orphan rows (trailing `जारीण्ण्ण्ण्` continuation marker in table_33)

### FM-7: Missing or Truncated Column (Stitching Artifact)
**Count: 1 PDF (Econ Survey Table 1.7)**
table_3.csv is missing the GDP column (9 columns vs 10 in PDF). The 2025-26 row reads `11367565,1796419,6828576,334875,241985,4327569,4840195,133126` with GDP=20189919 absent. table_4.csv has GDP but drops Year column and misapplies `s_no` header to PFCE data. No single file contains all 76 rows with all 10 columns.

### FM-8: Phantom col_N Columns
**Count: 2 PDFs — 6 total phantom columns**
- Agriculture Output: 5 phantom `col` columns in wide tables alongside the year-concatenation header
- HCES 2023-24: 1 phantom `col_5` column in table_2

### FM-9: Duplicate Table Output
**Count: 2 PDFs**
- Econ Survey Table 1.1: table_4.csv duplicates last 18 rows already in table_1.csv
- Econ Survey Table 1.7: table_4.csv duplicates last 16 rows of table_3.csv range with conflicting structure
- NCRB Crime Part 1: tables 1 and 2 are near-duplicate extractions of the same Table of Contents

### FM-10: Repeated Header Rows as Data Rows
**Count: 4 PDFs**
Page-boundary header rows injected into data at stitch points:
- Econ Survey Table 1.1: rows 37-42 and 73-78 repeat column header text
- Econ Survey Table 1.7: similar pattern
- Economic Survey Hindi: table_13 rows 41-47 and 80-87 are header repeats
- PLFS 2023-24: note rows appended as data row 9 in each CSV

---

## 5. Hindi & Bilingual PDFs

### Economic Survey 2024-25 Hindi Statistical Appendix

This document is the hardest-hit by Kruti Dev encoding. The PDF uses Kruti Dev font for all Hindi text, including section headings, table titles, and some column headers. When the font encoding table is not applied during PDF text extraction, Devanagari characters are decoded as arbitrary ASCII sequences. The result is systematic: `ldy` (निवल), `fuoy` (निवल), `ewy` (मूल), `cqfu;knh` (बुनियादी), `o`f¼` (वृद्धि), `Hkqxrku` (भुगतान), `fofue;` (विनिमय), `lwpdkad` (सूचकांक).

This corruption propagates into: table names (table_3 name is `1.9 ldy घरेलू cpr and ldy iwath निर्माण ......`), column headers (all default to `col` because header text is unreadable), and the most damaging defect — TOC misidentification. The Hindi table of contents uses a 3-column pattern (table number | Kruti Dev title | page number) that the pipeline's data-table detector cannot distinguish from actual data tables because the title text is garbled and the column count matches typical data tables. This causes tables 1-4 (the four largest CSVs, 14–55 rows each) to capture TOC entries instead of actual GNI, savings, and employment data.

The two tables with recoverable numeric content — table_13 (GVA by industry, 1950-51 to 2025-26) and table_32/33 (gross capital formation) — have correct values because they are purely numeric. The 1950-51 GVA row (309778, 71025, 35646, 60308, 36061, 479210) was confirmed correct. But the column headers are all `col` and mid-table header repetitions appear at rows 41-47 and 80-87. Roughly 2 of 19 stitched tables are usable; the remainder are misidentified TOC content.

**What survives:** Purely numeric rows in actual data tables, stitching across pages, zero phantom columns.
**What doesn't survive:** Any text that was typeset in Kruti Dev — table names, column headers for Hindi-language tables, state names in bilingual tables, unit annotations. The `(ps+ss)` → `(चे+ेे)` corruption in PLFS 2023-24 indicates even the parenthetical Latin text within Kruti Dev-encoded strings can be affected.

### PLFS Calendar 2024

A shorter, partially bilingual document (6 pages). The core annexure data (tables 3 and 4) extracts correctly for all numeric values. The Kruti Dev issue surfaces in two specific places: row 2 of table_1 contains `च ैत्र 27, शक laor 1947` (a Saka calendar date in a bilingual header) and row 25 contains `ैजंजने(च्ै+ैै)` for what should be `Principal and Subsidiary Status (PS+SS)`. The corruption pattern is similar to PLFS 2023-24's `(ps+ss)` → `(चे+ेे)` but appears in different positions, suggesting the Kruti Dev glyph mapping is inconsistently applied — some runs are decoded correctly, others not.

The more significant structural defect in this document is date-period text leaking into column names (`rural_january`, `urban_december`) from multi-row headers. This is a header parsing issue independent of Kruti Dev: the pipeline is picking up sub-header cell text from a calendar period row and appending it to the parent column name, producing semantically incorrect but predictably wrong names.

**Fix for Hindi/Bilingual PDFs:** A Kruti Dev-to-Unicode transliteration table should be applied as a post-processing step after PDF text extraction. Libraries exist for this conversion (`kruti-dev-to-unicode` npm package; the mapping is a known fixed table). The pipeline should detect Kruti Dev encoding by checking font names in the PDF XObject/Resources dictionary and apply the transliteration before any table detection or column naming logic runs. Without this, any document using Kruti Dev will produce corrupted column names and unreliable table detection.

---

## 6. Regression Guards — Status

```
Guard A — DES p145-155
  [PASS] 11/11 passed
  [PASS] 11/11 named Tabel X.Y
  [PASS] no Kruti soup in names
  [PASS] p148 cols start s_no|district|telephone_number_center_2020_21
  [PASS] district column English

Guard B — DARPG Jan pp8-9 table 3.1
  [PASS] found 3.1 Ranking of Ministries/Departments – Group A
  [PASS] cols exact
  [PASS] 40 rows (serials 1-40 on pages)
  [PASS] serial sequence unbroken

Guard C — PLFS p11
  [PASS] table on p11
  [PASS] 72 numeric cells
  [PASS] cols incl rural_males
  [PASS] rows incl 'Persons aged 15 years'

Guard D — NFHS-6 India pp26-28
  [PASS] 3 India tables pass
  [PASS] named 'India Key Indicators'
  [PASS] NFHS group columns on all 3
  [PASS] wrapped #41 reassembled (label+88.6)
  [PASS] <=2 orphan number-rows in slice

Guard E — NFHS-5 India national (Gap A)
  [PASS] >=5 tables pass
  [PASS] >=4 tables with nfhs_* column schema
  [PASS] 0 NFHS tables with >1 col_N value column

Guard F — FR375 pp118-123 KPI strip (Gap C)
  [PASS] 0 too_few_rows in FR375 pp118-123

STATUS: GREEN — all 6 guards pass, 0 regressions
```

No regressions from prior runs. All previously fixed behaviors (NFHS schema, DARPG serial continuity, PLFS p11 column naming, FR375 row completeness, wrapped label reassembly) remain stable. The new batch failures are all in previously untested document categories and do not overlap with guarded behaviors.

---

## 7. Top Recommendations (Priority Order)

### R1 — Fix Multi-Row Header Parsing (Highest Impact)
**Affects: 9/15 PDFs. Fixes FM-1, FM-4, FM-10 partially.**

The pipeline must detect when a PDF table has 2-3 header rows and merge them hierarchically before generating column names. Current behavior: takes one row (often the wrong one) or concatenates all words into a flat snake_case string without hierarchy.

**What to change:** In the table header extraction stage, detect the repeating column-index row (the `(1) (2) (3)...` numbering rows present in all government statistical tables) as an anchor. Everything above the index row is a header zone. Merge tier-1 and tier-2 cells by column position: `rural` + `male` → `rural_male`, not the word-by-word left-to-right concatenation that produces `force_rural_male`. For state-name columns in wide tables, extract headers from the first page occurrence only (before truncation artifacts appear from PDF rendering of long column headers).

**Validation:** After fix, `table_2.csv` in Econ Survey 1.1 should produce `year, gni_current_crore, gni_constant_crore, nni_current_crore, nni_constant_crore, per_capita_nni_current_rs, per_capita_nni_constant_rs`; PLFS 2023-24 table_1 should produce `survey_period, rural_male, rural_female, rural_person, urban_male, urban_female, urban_person, rural_urban_male, rural_urban_female, rural_urban_person`.

### R2 — Add Kruti Dev Detection and Transliteration
**Affects: 6/15 PDFs. Fixes FM-3 entirely.**

Apply a Kruti Dev-to-Unicode mapping pass on all extracted text before any table detection or column naming runs. The mapping is a fixed 256-entry table (each Kruti Dev codepoint maps to a Devanagari Unicode codepoint).

**What to change:** In the PDF text extraction layer (before the table detection step), check the font Resources dictionary for font names containing `Kruti`, `KrutiDev`, `DevLys`, or `DV-TTSurekh`. When detected, run the transliteration pass on all text blocks rendered in that font. The `kruti-dev-to-unicode` npm package or the Python equivalent (`indic_transliteration` with Kruti Dev support) implements this. Without this fix, the pipeline cannot correctly process any document from MOSPI, MoSPI state offices, or state statistical bureaus that use Kruti Dev, which is the majority of Hindi-medium statistical publications.

**Validation:** After fix, `table_3` in Economic Survey Hindi should be named something like `1.9 शुद्ध घरेलू उत्पाद और निवल राष्ट्रीय आय` (readable Hindi); state names in Agriculture Output should show `पश्चिम बंगाल`, `केरल`, `मेघालय`, `गुजरात` instead of the garbled sequences.

### R3 — Add TOC/Front-Matter Page Classifier
**Affects: 7/15 PDFs. Fixes FM-2 entirely.**

Contents pages, acknowledgements, supervision lists, and methodology notes are being extracted as data tables. A classifier that identifies these page types should flag them as non-data before extraction.

**What to change:** Add a page-type pre-classifier that runs before the table detector. Signals for TOC pages: 3-column pattern where column 3 is all small integers (page numbers 1-999), column 1 is short alphanumeric (table numbers like `1.1`, `2A.3`), and column 2 is long prose text with dots/ellipsis. Signals for acknowledgement/supervision pages: column 1 is sequential integers, column 2 is a proper name pattern (`Shri/Dr/Ms + name`), column 3 is a job title (`Director/Officer/Analyst`). Pages matching these patterns should be skipped or stored in a `metadata/` subdirectory separate from data tables.

**Validation:** After fix, RBI Handbook should output ~18 tables instead of 22 (tables 2–5 excluded); Economic Survey Hindi should extract actual GNI/savings data instead of TOC entries; NCRB Crime Parts 1 and 2 should show 0 TOC tables and start counting actual data tables from page 22+.

### R4 — Fix Wide Two-Panel Table Stitching
**Affects: NCRB Parts 1 and 2. Fixes FM-1 for side-by-side panel layout.**

Tables formatted as two or more side-by-side panels (e.g., Population of 19 Metropolitan Cities: left panel SL/City/Population, right panel SL/City/Population) are being collapsed into 2 rows with all panel content concatenated as strings. The pipeline must detect column-boundary gaps (whitespace or ruling lines) separating panels and treat each panel as a set of columns in a single table.

**What to change:** In the bounding-box extraction stage, detect vertical whitespace gaps of >2x the average column gap as panel separators. Reconstruct multi-panel tables by interleaving columns: `[sl_1, city_1, pop_1, sl_2, city_2, pop_2]`. For the specific 19-city population table format, the output should be 19 rows × 3 columns (sl, city, population_lakhs), not 2 rows × 2 columns with concatenated strings.

**Validation:** NCRB Part 1 table_10 should produce 19 rows with `sl, city, population_lakhs` columns and values like `1, Ahmedabad (Gujarat), 63.52` as separate columns.

### R5 — Add PDF Validity Pre-Flight Check
**Affects: RBI Annual Report failure. Prevents silent invalid-input failures.**

**What to change:** Add a pre-flight check at the start of the pipeline ingestion step: read the first 5 bytes and verify they are `%PDF-`. If not, classify the job as `invalid_source` and emit a structured error (e.g., `{"status": "invalid_source", "detected_type": "HTML", "path": "..."}`) rather than proceeding to produce an empty output directory. The `file` command output and `pdfinfo` xref error are already sufficient signals — the pipeline should surface them as first-class error states, not silent no-ops.

**Additional:** Check for anti-bot HTML redirect patterns specifically (DOCTYPE + meta Pragma no-cache is a fingerprint of RBI and other government server bot gates). Flag these with `invalid_source: bot_redirect` to guide the retry strategy.

### R6 — Strip Empty Separator Columns Post-Extraction
**Affects: Econ Survey 1.1, Agriculture Output. Fixes FM-8 partially.**

Three blank separator columns in Econ Survey Table 1.1 (positions 3, 6, 9) inflate column count from 7 to 10 and create empty `col` columns. Five phantom `col` columns in Agriculture Output table_4 are similar artifacts.

**What to change:** In the post-extraction cleanup step, drop any column that: (a) has a `col_N` generated name or a blank name, and (b) contains only empty strings or NaN values across all data rows. This is a safe filter — a column with a generic name and no data provides zero value and should be removed. Apply after stitching, before output.

### R7 — Fix Missing GDP Column in Table 1.7 (Stitching Column Count Validation)
**Affects: Econ Survey Table 1.7. Fixes FM-7.**

**What to change:** Add a column-count consistency check after each stitch operation: if the number of columns in the stitched continuation differs from the base table, log a warning and attempt to realign by matching the last N-1 columns rather than assuming positional alignment. For table_3.csv specifically, the GDP column is missing because the last column on the page boundary was lost during stitching. The fix is to validate `len(stitch_cols) == len(base_cols)` and reject or flag non-matching stitches for manual review.

### R8 — Deduplicate Redundant Table Output
**Affects: Econ Survey Tables 1.1 and 1.7, NCRB Parts 1/2.**

table_4.csv in Econ Survey 1.1 is a full duplicate of the last 18 rows in table_1.csv. In Econ Survey 1.7, table_4.csv duplicates table_3.csv's page range with conflicting structure.

**What to change:** After all CSVs are written, run a pairwise deduplication pass: if CSV_A's rows are a strict subset of CSV_B's rows (same column count, same row order), delete CSV_A. If rows overlap but structures conflict (e.g., table_4 in 1.7 has a different column count than table_3), flag the conflict for inspection rather than silently writing both. This reduces output clutter and prevents downstream joins from double-counting rows.

---

## 8. What's Working Well

**Numeric accuracy is the pipeline's strongest property.** Across every document where rows were extracted, spot-checked values matched the source PDF exactly. This holds for: GNI time series (1950-51 through 2025-26, 75 rows), NSDP state values (61789 for Andhra Pradesh 1994-95 at current prices), HCES MPCE figures (4,122 / 6,996 / 2,079 / 3,632 for 2023-24), Gini coefficients (0.266 / 0.314 / 0.237 / 0.284), NCRB crime incidence (4,44,104 accidental deaths; 1,71,418 suicides), GVA shares (18.5, 18.2, 18.6... 17.8 matching Agriculture Output Table 1), and PLFS employment rates (80.2, 47.6, 63.7 for 2023-24 LFPR). The extraction engine is trustworthy for numeric data.

**Multi-page table stitching works.** table_19 in the RBI Handbook correctly spans 121 rows across multiple PDF pages. All 6 PLFS employment tables stitch cleanly with zero failures. NCRB ADSI table_6 spans 57 years (1967-2023) without gaps. Agriculture Output stitched 354 of 363 tables. The NFHS regression guards (Guard D, wrapped #41 reassembled with label+88.6) confirm the stitching handles complex wrapped-row cases too.

**Missing/suppressed value handling is correct.** RBI Handbook's `-` notation for missing NSDP data is preserved as `-` rather than converted to zero or null. PLFS 2023-24 `(ps+ss)` notation is preserved (though the parenthetical may be corrupted in Kruti Dev documents). NAS 2025 multi-line cell values (rows 4, 12, 19, 49) are correctly merged into single cells.

**The NFHS column schema (nfhs_* naming) and regression guard coverage demonstrate that the pipeline can produce high-quality, semantically named column headers when the header detection works.** NCRB ADSI table_6 also demonstrates this — `accidental_deaths_male`, `suicides_transgender` are correctly named. This confirms the naming capability exists; the problem is that it fires inconsistently across document types. The guards (all GREEN) confirm these capabilities are stable across the version tested.

**False-positive avoidance for structural failures is clean.** When inputs are invalid (RBI Annual Report as HTML), the pipeline outputs nothing rather than hallucinating tables. When tables are too sparse, the `mostly_empty` filter correctly identifies them. No catastrophic parse failures were observed in the 15-document corpus — every failure mode produces either empty output or structurally consistent (if semantically wrong) CSV output.

**High extraction yield on the Agriculture Output corpus (354/363 = 97.5% tables stitched from 385 pages) demonstrates the pipeline's throughput capacity.** Processing a 385-page, 364-table document is non-trivial, and the per-row numeric accuracy holds even at that scale.
