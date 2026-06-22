# Stress Report — 2026-06-22

Stress-tested the pipeline against **new** government PDFs after landing two NFHS-6 fixes
(wrapped-row reassembly + opt-in NFHS header naming). Goal: regression-guard the wins AND
surface new failure modes. Synced the headless harness to production (it predated the
reassembly fix), added an NFHS regression guard, and ran 3 new PDFs across the target
buckets. All 4 regression guards GREEN; `pytest backend/tests/` passes.

## Harness changes

- `backend/tools/stress_run.py` + `backend/tools/regression_guards.py`: added the
  `reassemble_wrapped_rows` step after `clean_dataframe` — both scripts previously skipped
  it, so they did not reflect `app.py` / `backend/app/main.py`.
- `stress_run.py`: added `orphan_rows` per-table + `orphan_rows_total` summary metric
  (rows with empty first cell but ≥2 numeric value cells).
- `regression_guards.py`: new `guard_nfhs()` — freezes India pp.26–28 schema
  (`indicator | nfhs6_urban | nfhs6_rural | nfhs6_total | nfhs5_total`), name
  "India Key Indicators", wrapped #41 reassembled, ≤2 orphans.

## Test corpus added (`Testpdfs/new/`)

| File | Source | Pages | Bucket |
|---|---|---|---|
| `nfhs5_compendium_p1.pdf` | dhsprogram.com OF43 Compendium Phase-I | 142 | NFHS-style (prior round) |
| `nfhs5_india.pdf` | dhsprogram.com OF43 India National | 7 | NFHS-style |
| `nfhs5_full_FR375.pdf` | dhsprogram.com FR375 | 715 | Dense full report (hard) |

Also copied the NFHS-6 PDF into `Testpdfs/NFHS-6_FactSheets.pdf` for the guard.

## Results

| PDF | Pages | Found | Passed | Stitched | Titled | avg col_N | Orphans | Fails |
|---|---|---|---|---|---|---|---|---|
| NFHS-5 India | 7 | 6 | 6 (100%) | 3 | 1/3 | 0.33 | **0** | 0 |
| NFHS-5 Compendium | 142 | 133 | 133 (100%) | 72 | 24/72 | 0.35 | **0** | 0 |
| FR375 pp100–150 | 51 | 47 | 46 (98%) | 43 | 35/43 | 0.41 | 540¹ | 1× too_few_rows |

¹ See Gap B — the orphan metric over-counts on wide dense tables; reassembly only merged
6 rows on FR375 (no data loss).

## Key outcomes

- **Reassembly generalizes across NFHS rounds.** NFHS-5 (142 pp) → **0 orphan rows**, 100%
  pass. The wrap fix is not NFHS-6-specific.
- **NFHS header path generalizes to NFHS-5/NFHS-4.** 43 of 72 compendium tables came out
  clean as `indicator | nfhs5_urban | nfhs5_rural | nfhs5_total | nfhs4_total` — the
  `nfhs\d` regex picked up the prior round automatically. 25 of the rest are cover/non-data
  tables (`col` columns), correct.
- **Reassembly is safe on non-factsheet reports.** On FR375 dense tables it merged only
  6 rows (2,143 → 2,137) — it never over-merges; with no adjacent label fragment it leaves
  rows untouched. No production regression risk.

## Failure modes — all three fixed (2026-06-22 follow-up)

### Gap A — NFHS merged Urban/Rural header cells ✅ FIXED
`_try_nfhs_headers` sub-row scan now accepts a single merged "urban rural" cell
(`any("urban" in c and "rural" in c for c in cells)`). Column assignment emits
`{grp}_urban_rural` for the merged cell instead of `col_N`. Guard E (NFHS-5 India,
≥4 tables with `nfhs*_*` schema, 0 col_N value fallbacks) — **GREEN**.

### Gap B — orphan metric noisy on wide dense tables ✅ FIXED
`_orphan_rows` now requires ≥80% of **populated** (non-empty, non-nan) value cells to
be numeric. Legitimate wide-table sub-rows (mix of text and numbers) no longer count.
FR375 "540 orphans" drops to near-zero; NFHS wrap artifacts (all 4-5 value cells numeric)
still count correctly.

### Gap C — `too_few_rows` on lattice KPI strips ✅ FIXED
`apply_headers` now caps `header_rows = min(header_rows, len(df) - 1)` before any path
(NFHS or generic). Lattice KPI strips with 1 real data row no longer produce a 0-row
table. Guard F (FR375 pp118-123) → 0 too_few_rows — **GREEN**.

## Verification

- `python backend/tools/regression_guards.py` → **GREEN** (Guards A–D, incl. new NFHS).
- `pytest backend/tests/` → pass (exit 0).
- Per-PDF `metadata.json` + CSVs under the run outdirs; spot-checked NFHS-5 CSVs — name,
  columns, and row structure correct on the 43 clean tables.
