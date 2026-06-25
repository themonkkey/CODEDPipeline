# Data Rubiks (CODEDPipeline)

Extract data tables from any statistical PDF report and export them as clean,
named, analysis-ready Excel workbooks and CSVs, through a Streamlit web app.

Handles 600+ page government reports, bordered and borderless tables, bilingual
Hindi-English sources, multi-page tables that flow across pages, and reference
catalogues (code + text lookup tables) as well as statistical tables.

## Pipeline

```
PDF
 → extract_tables        Camelot (lattice + stream) + pdfplumber, optional OCR recovery
 → clean_dataframe       normalise whitespace / encoding (ftfy)
 → split_panels          split side-by-side panels into separate tables
 → reassemble_wrapped    rejoin wrapped rows
 → translate_dataframe   Kruti-Dev / Devanagari → English
 → classify_table        archetype: statistical | reference  (profiler)
 → detect_header_rows    numeric-anchored, or record-aware for reference tables
 → extract_table_name    title from caption / Table N / descriptive cell
 → apply_headers         build column names (spanning, multi-level, reference modes)
 → clean_headers         dedupe + content-based naming (state/year/code/percentage/…)
 → merge_continuation    merge orphaned continuation values
 → lift_section_rows     in-table section banners → a forward-filled `category` column
 → normalize_numeric     strings → typed int/float
 → validate_table        drop TOC / front-matter / prose / garbled / ghost fragments
 → stitch_tables         merge multi-page tables; semantic names for reference tables
 → excel_exporter        navigable workbook (TOC sheet) + per-table CSVs
```

## Key capabilities

- **Document understanding** — a profiler (`backend/app/profile/table_profiler.py`)
  classifies each table as `statistical` (numeric measures) or `reference`
  (code + text catalogue) and routes header detection accordingly. Reference
  tables (e.g. the NCO occupation concordance) keep their hierarchy rows as data
  instead of having them eaten as headers.
- **Cross-page merge** — tables that continue across pages are stitched into one
  (e.g. a 200-page concordance → a single table).
- **Content-based column naming** — when a header is lost, spanning, or blank,
  the column is named from its content: `state`, `year`, `percentage`, `date`,
  `code`, `level`, else `value` / `label`.
- **OCR recovery** — font-corrupt Kruti-Dev tables are re-read by rendering the
  region and OCR-ing the glyphs; unrecoverable ones are quarantined, not shipped
  as clean.
- **Quarantine** — TOC/index pages, prose paragraphs, staff lists, and garbled
  tables are detected and set aside rather than exported as data.

## Stack

| Layer | Tech |
|---|---|
| PDF extraction | Camelot (lattice + stream), pdfplumber |
| OCR (corrupt Hindi) | tesseract (hin+eng) via subprocess, pypdfium2 |
| ML extraction (opt-in) | Docling / TableFormer — `DOCLING_ENABLED=1` (needs ~1 GB RAM) |
| Cleaning / data | pandas, ftfy |
| App | Streamlit (`app.py`) |
| Export | openpyxl (Excel), CSV |

## Setup & run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt        # camelot-py, pdfplumber, pandas, ftfy, streamlit, openpyxl, pypdf, docling …

streamlit run app.py                    # open the local URL, drop a PDF
```

Optional high-accuracy table structure (heavier, local / paid tier):

```bash
DOCLING_ENABLED=1 streamlit run app.py
```

## Quality & regression guards

All behaviour is locked by regression guards. **Every guard must be GREEN before
any commit.**

```bash
.venv/bin/python3 backend/tools/regression_guards.py        # full guard suite
```

Corpus quality measurement:

```bash
.venv/bin/python3 backend/tools/measure_all.py /tmp/out 40 4 # measure corpus (outdir, max_pages, workers)
.venv/bin/python3 backend/tools/aggregate_quality.py /tmp/out
```

## Project structure

```
app.py                              # Streamlit UI + pipeline driver
backend/app/
  extract/        table_extractor, ocr_recovery, docling_extractor
  cleaning/       universal_cleaner, panel_splitter, wrapped_row_reassembler,
                  header_detector, header_builder, header_postprocessor,
                  section_lifter, numeric_normalizer
  profile/        table_profiler            # archetype classification
  standardization/ table_name_extractor, column_namer, table_stitcher,
                  metadata_builder, excel_exporter
  translation/    hindi_translator, kruti_dev, corruption
  validation/     table_validator
backend/tools/
  regression_guards.py   # all guards (run before every commit)
  measure_quality.py / measure_all.py / aggregate_quality.py   # corpus scoring
  diagnose_titles_cols.py # failure-case diagnostic
```

## Known limitations

- Scanned image-only PDFs need OCR; only font-corrupt (not image) tables are recovered.
- Very complex multi-level / borderless headers are best handled by the Docling path.
- Untranslated Devanagari row labels can remain on some bilingual tables (data underneath is clean).
