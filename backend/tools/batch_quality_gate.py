"""Loop Spec 3: batch quality gate — surfaces a WARNING (never a hard block)
right after `assemble_panels` finishes, so the analyst sees the batch's
overall output quality before the skill moves on to Step 4 (analysis).

Reuses the SAME scorer (`measure_quality.measure_table`) and corpus rollup
(`aggregate_quality.compute_corpus`) the dev harness already runs manually
via `measure_all.py` + `aggregate_quality.py` on a raw per-PDF corpus. Here
they're applied to each group's ASSEMBLED PANEL instead — the artifact the
analyst actually opens in master.xlsx — since that's what "batch quality"
means at this stage of the pipeline, after schema alignment/stacking.

Threshold calibration (2026-07-07): the plan's three starting thresholds were
sanity-checked against a real DARPG batch workdir (11 confirmed panels,
~/Downloads/2022_State_Pdf_files_work). Two needed correction:

- `tables_zero_coln_frac` (an aggregate_quality field reused as-is) is the
  fraction of panels with col_n == 0, i.e. ZERO leftover generic col/col_N
  columns — a CLEAN-naming signal, so HIGHER is better. The plan's "<= 0.1"
  read the field name as "tables that ended up all-generic", which is not
  what it computes; that polarity would fail almost every healthy batch.
  Flipped to ">= 0.5" and calibrated against the real batch (0.64 there).
- `named_frac` relied on measure_table's FALLBACK_NAME regex to spot
  placeholder titles, which only recognised measure_quality's own
  "Table N (p.NNN)" stand-in, not the panel builder's "(N-col ... table)"
  stand-in (table_signature.py) — so every batch scored a meaningless 1.0.
  Widened FALLBACK_NAME itself (in measure_quality.py) to catch both; the
  ">= 0.5" threshold held (real batch: 0.64 after the fix).
- `mean_numeric_readiness >= 0.75` held up as-is (real batch scored 1.0).

Second-corpus validation (2026-07-07): the DARPG batch above is entirely the
"statistical" archetype (CLAUDE.md's two-archetype split). Re-ran the same
three thresholds against real pages of a structurally different, reference-
archetype-heavy corpus (the NCO concordance PDF also used by Guard AM —
code+text lookup tables, mostly no numeric measures at all) via
guard_quality_gate_reference_corpus (Guard AY):

- A clean 100-page slice (77 passing tables, 47 reference + 34 statistical)
  stays GREEN. `mean_numeric_readiness` is unaffected by the reference
  tables' lack of numeric columns because aggregate_quality.compute_corpus
  already excludes `numeric_readiness=None` tables from that mean (see
  measure_quality.py / aggregate_quality.py) — the threshold does not
  penalize a reference-heavy batch just for having non-numeric tables.
- Isolating the slice's genuinely dirty tables (prose mis-extracted as
  tables, 2-3 generic col_N columns each) correctly flips the gate RED on
  `tables_zero_coln_frac` (0.0 vs the 0.5 bar) — confirming the RED path
  also fires correctly on real reference-archetype data, not only on
  synthetic fixtures or the one statistical corpus used to calibrate.

No threshold changes were needed; both corpora validate the same three
values above.
"""
import json
import os

from backend.tools.aggregate_quality import compute_corpus
from backend.tools.measure_quality import measure_table

# (comparison op, threshold). See module docstring for calibration notes.
THRESHOLDS = {
    "mean_numeric_readiness": (">=", 0.75),
    "tables_zero_coln_frac": (">=", 0.5),
    "named_frac": (">=", 0.5),
}


def _passes(op, value, threshold):
    return value >= threshold if op == ">=" else value <= threshold


def measure_panels(panels):
    """panels: the list stage_assemble already builds — dicts with at least
    `label` and `panel` (a DataFrame). Returns one measure_table() dict per
    non-empty panel, skipping panels that produced zero rows."""
    tabs = []
    for p in panels:
        df = p.get("panel")
        if df is None or df.shape[0] == 0:
            continue
        tabs.append(measure_table(df, p.get("label")))
    return tabs


def _badness(t, thresholds=THRESHOLDS):
    """How many of the batch-level gate conditions this individual table
    would itself fail — reuses measure_table's own fields, no new
    categorization invented on top of them."""
    n = 0
    nr = t["numeric_readiness"]
    nr_op, nr_threshold = thresholds["mean_numeric_readiness"]
    if nr is not None and not _passes(nr_op, nr, nr_threshold):
        n += 1
    if t["col_n"] > 0:
        n += 1
    if not t["named"]:
        n += 1
    if t["dup_cols"] > 0:
        n += 1
    return n


def _reasons(t):
    reasons = []
    nr = t["numeric_readiness"]
    if nr is not None and nr < THRESHOLDS["mean_numeric_readiness"][1]:
        reasons.append(f"numeric_readiness={nr}")
    if nr is None:
        reasons.append("numeric_readiness=None (no intended-numeric columns detected)")
    if t["col_n"] > 0:
        reasons.append(f"{t['col_n']} generic col/col_N column(s) ({t['col_n_frac']:.0%} of columns)")
    if not t["named"]:
        reasons.append("no real title (fallback name)")
    if t["dup_cols"] > 0:
        reasons.append(f"{t['dup_cols']} duplicate column name(s)")
    return reasons


def evaluate(panels):
    """Grade a batch's assembled panels against THRESHOLDS. Returns a report
    dict; never raises — this is a warn-only gate, not a block."""
    tabs = measure_panels(panels)
    corpus = compute_corpus(tabs)

    checks = {}
    overall = "GREEN"
    for key, (op, threshold) in THRESHOLDS.items():
        value = corpus.get(key)
        ok = value is not None and _passes(op, value, threshold)
        checks[key] = {"value": value, "op": op, "threshold": threshold, "pass": ok}
        if not ok:
            overall = "RED"

    worst = sorted((t for t in tabs if _badness(t) > 0),
                    key=lambda t: (-_badness(t),
                                   t["numeric_readiness"] if t["numeric_readiness"] is not None else 1.0))
    worst_tables = [{"title": t["title"], "reasons": _reasons(t)} for t in worst[:5]]

    return {
        "n_panels": len(tabs),
        "overall": overall,
        "checks": checks,
        "worst_tables": worst_tables,
        "corpus": corpus,
    }


def write_report(panels, workdir):
    """Compute + write workdir/quality_report.json. Returns (report, path)."""
    report = evaluate(panels)
    path = os.path.join(workdir, "quality_report.json")
    with open(path, "w") as f:
        json.dump(report, f, indent=1, default=str)
    return report, path


def summary_line(report):
    """One-line status for CLI / MCP surfacing. Never a refusal — just a
    heads-up for the analyst to weigh before Step 4 analysis."""
    if report["overall"] == "GREEN":
        return f"quality gate: GREEN ({report['n_panels']} panels)"
    failing = [k for k, c in report["checks"].items() if not c["pass"]]
    worst = ", ".join(t["title"] or "?" for t in report["worst_tables"][:3])
    return (f"quality gate: RED on {', '.join(failing)} — review before Step 4 "
            f"analysis (worst tables: {worst})" if worst else
            f"quality gate: RED on {', '.join(failing)} — review before Step 4 analysis")
