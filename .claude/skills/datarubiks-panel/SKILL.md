---
name: datarubiks-panel
description: Compile a folder of same-source PDF reports (e.g. PLFS 2017-2024, successive Census or DARPG monthly reports) into a panel dataset, with a documented schema changelog of how variables changed across periods. Use when the user points at a folder of many PDFs from one source and wants them extracted, stacked into panel/long format, and analyzed.
---

# Data Rubiks — Batch Panel Builder

Turn many same-source PDF reports into one panel dataset plus a defensible
record of how the methodology changed across reporting periods.

## When to use

The user has a **folder of PDFs from a single source** across periods (years or
months) and wants them combined for analysis — not a single PDF. Signals: "I have
50 PLFS reports", "all the monthly DARPG reports", "stack these into panel data",
"build a master sheet across years".

For a single PDF, use the normal Streamlit app instead.

## How it works

The engine is in the folder where `install.sh` was run — referred to below as
`ENGINE_DIR`. The venv python is `ENGINE_DIR/.venv/bin/python3`.

Before doing anything, resolve ENGINE_DIR by finding the `datarubiks_mcp.py`
file that was registered as an MCP server — its parent directory is ENGINE_DIR.

### Step 1 — Extract + propose groups

Use the MCP tool `extract_folder`, or via CLI:
```bash
ENGINE_DIR/.venv/bin/python3 ENGINE_DIR/backend/tools/batch_panel.py \
  <pdf_folder> <workdir> --workers 4
```

Writes `workdir/groups.json`. Prints `[Np x Mt] label` summary per group.
Add `--max-pages 20` for a fast first pass on large corpora.

### Step 2 — Confirm groups with the user

Read `workdir/groups.json`. Every group carries a `confidence` field —
`"auto"` or `"review"` — set by the engine's own join history (a strong title
match or a >= 0.8 fuzzy column-overlap is confident; anything that needed the
0.40-0.59 grouping-agent tie-break, a 0.6-0.8 weaker overlap, or the singleton
rescue pass is not). Split your presentation on it:

- **`review` groups** — walk the user through each one in full, as before.
  Flag:
  - Groups that should be one panel but split (title embedded a date that drifted)
  - Singletons that are likely noise (cover sheets, summary boxes)
- **`auto` groups** — do not walk through these one by one. List them
  tersely, label + period count only, e.g. `[6p] 3.1 Ranking of Ministries`,
  and say they were auto-approved on a strong title/column match.

Let the user approve / drop / merge / rename anything (auto groups included —
the tag is a triage hint, not a lock). Save as `confirmed_groups.json`. If
happy as-is, reuse `groups.json` directly.

**Escape hatch:** `confidence` is derived fresh every time Step 1 runs — the
engine keeps no record of past approvals. If an "auto" call ever looks wrong,
just re-run Step 1 (or hand-edit `groups.json`); there is no stale
auto-approval state to clear first. This is a one-keystroke undo.

### Step 3 — Assemble panels

Use the MCP tool `assemble_panels`, or via CLI:
```bash
ENGINE_DIR/.venv/bin/python3 ENGINE_DIR/backend/tools/batch_panel.py \
  <pdf_folder> <workdir> --groups <workdir>/confirmed_groups.json
```

Produces:
- `workdir/master.xlsx` — one sheet per panel, Panels TOC, Schema Changes sheet
- `workdir/schema_changelog.md` — methodology appendix
- `workdir/quality_report.json` — batch quality gate verdict (see Step 3.5)

### Step 3.5 — Read the quality gate before offering Step 4

Read `workdir/quality_report.json` (the `assemble_panels` MCP tool also echoes
a one-line `quality_warning` field when it's not GREEN — check that first).

This is a **warning, never a block** — `master.xlsx` is already written
regardless of the verdict, and the engine will never refuse to produce it.

- `overall: "GREEN"` — say nothing extra, go straight to Step 4.
- `overall: "RED"` — tell the user explicitly which checks failed
  (`checks` dict) and name the worst tables (`worst_tables`, with their
  specific reasons — generic columns, no real title, low numeric readiness,
  duplicate columns). Then ask whether they want to proceed to Step 4 anyway,
  fix the source groups first (back to Step 2), or spot-check the named
  tables in `master.xlsx` before trusting any trend drawn from them. Proceed
  with whatever they choose — do not refuse or stall on their behalf.

### Step 4 — Analyze and tell the story

Read `schema_changelog.md` + panel sheets. Give the user:
- **Trends** across periods in key panels
- **Series breaks** — value jumps that coincide with a schema flag are
  methodology artifacts, not real-world changes — call these out explicitly
- **Caveats** — which variables are not comparable across the full span

## Sub-agents (automatic, no setup)

The engine fires Sonnet sub-agents for hard cases only:
- Score < 0.70 → Header Agent infers real column names from cell content
- Jaccard 0.40–0.59 → Grouping Agent decides same/different table
- combined/split flags → Rename Agent confirms the mapping

All cached to disk. Re-runs cost zero tokens. Uses the active Claude Code session.

## Rules

- Never silently merge mismatched variables — engine keeps them separate on purpose
- `combined`/`split` flags are heuristic (low confidence) — "verify this", not fact
- PDF-only; images/Word/non-OCR out of scope
- Spot-check panel values against one source PDF before drawing conclusions
