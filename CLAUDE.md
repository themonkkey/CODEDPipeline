# CLAUDE.md

Guidance for working in this repo (Data Rubiks / CODEDPipeline).

Note: cross-period panel building (grouping, schema diff, panel assembly,
quality gate, scorecard, agents) now lives in the separate RubikX repo
(`/Users/thesinghaa/RubikX`), which uses this repo as its extraction engine.

## What this is

A deterministic pipeline that extracts data tables from statistical PDFs and
exports clean Excel/CSV, with a Streamlit front end (`app.py`). It is tuned for
Indian government reports (NCRB, NFHS, PLFS, RBI, DARPG, census, economic
survey, occupation classifications).

## The one hard rule: guards stay green

All behaviour is pinned by regression guards in
`backend/tools/regression_guards.py` (currently 50 guard groups, labelled A–BL).

**Before any commit, run the full suite and confirm GREEN:**

```bash
.venv/bin/python3 backend/tools/regression_guards.py
```

When a change intentionally improves behaviour and a guard's old expectation is
now outdated, update that guard's assertion (don't weaken it) and say so. Add a
new guard for every new behaviour, following the existing pattern (synthetic
cases + a real-PDF slice via `_slice` / `_pipeline`).

Use the system Python venv: `.venv/bin/python3` (plain `python`/`python3` won't
have the deps).

## Pipeline order (per table)

`extract_tables` → `clean_dataframe` → `split_panels` →
`reassemble_wrapped_rows` → `translate_dataframe` → **classify_table** →
`detect_header_rows` → `extract_table_name` → `apply_headers` → `clean_headers`
→ `merge_continuation_values` → `lift_section_rows` → `normalize_numeric_columns`
→ `validate_table` → `stitch_tables` → `excel_exporter`.

This order is mirrored in `app.py`, `backend/tools/measure_quality.py`, and the
guards' `_pipeline` helper. If you change the order or signatures, change all
three.

## Two table archetypes

`backend/app/profile/table_profiler.py` classifies each table:

- **statistical** — rows = entities, columns = numeric measures. The default;
  uses the numeric-density header detector. Most of the corpus.
- **reference** — code + text lookup catalogue (e.g. NCO concordance), no
  numeric measures. Uses a record-aware header detector and post-merge,
  content-based semantic column names.

The archetype is computed pre-normalization (codes are still strings then) and
threaded through as an item flag, because after numeric casting the codes read
as measures. The profiler is deliberately conservative — verified that no
statistical corpus table flips to reference. Keep it that way: if you touch the
thresholds, re-run the corpus classification check.

## Column naming

`backend/app/standardization/column_namer.py` infers a column's role from its
content (`code`, `level`, `state`, `year`, `percentage`, `date`, `name`). Used
to name headerless / generic columns on any table. `clean_headers` applies
high-confidence roles before the generic `value` / `label` fallback.

## Measuring quality

```bash
.venv/bin/python3 backend/tools/measure_all.py /tmp/out 40 4   # outdir, max_pages, workers
.venv/bin/python3 backend/tools/aggregate_quality.py /tmp/out
```

Watch: `named_frac` (titles), `mean_col_n_frac` / `tables_zero_coln_frac`
(column integrity), `mean_numeric_readiness`, `tables_with_dup_cols_frac`,
`failed_reasons` (quarantine buckets). A change that regresses these on the
statistical corpus is a regression even if guards pass.

`backend/tools/diagnose_titles_cols.py` dumps the actual untitled / phantom-col
cases with context — use it to root-cause naming problems before editing.

## Conventions

- Deterministic and dependency-light by default. Docling/TableFormer is the
  opt-in heavy path (`DOCLING_ENABLED=1`), not the default (Streamlit free tier
  has ~1 GB RAM).
- Match the surrounding code's comment density and naming. Files carry detailed
  docstrings explaining *why* a heuristic exists — keep that.
- Frontend (`app.py`) has a deliberate custom theme. Do not restyle without
  being asked; never touch the CSS cube animation.
- Commit and push only when asked. Keep guards green; keep the corpus from
  regressing.
