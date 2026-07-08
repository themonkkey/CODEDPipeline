"""Engine scorecard: grade every pipeline element from a finished batch
workdir so improvements are measurable run-over-run.

Each element gets a 0-100 score computed from artifacts the pipeline already
writes (manifests, groups.json, quality_report.json) — nothing is re-extracted.
Scores are comparable across runs of the SAME corpus: run, improve the engine,
re-run, diff the scorecards. A `baseline` block records the raw inputs behind
every score so a future change in the formula itself is visible, not silent.

Elements scored:
  extraction        — how well tables come out of the PDF (mean quality score)
  retry_effectiveness — Loop 1: how often a retry strategy beat the first try
  header_naming     — fraction of columns with real (non-fallback) names
  table_titling     — fraction of tables with a real extracted title
  grouping          — consolidation: how much of the corpus lives in
                      multi-period panels instead of singletons
  panel_depth       — period coverage of the panels that did form
  auto_confidence   — Loop 5: how much of the corpus needs no human review
  numeric_readiness — are numeric columns analysis-ready (from quality gate)

Usage:
    python backend/tools/scorecard.py <workdir> [--out scorecard.json]
"""
import argparse
import glob
import json
import os
import re


_FALLBACK_COL = re.compile(r"^(col|value|code|label)(_\d+)?$")
_FALLBACK_TITLE = re.compile(r"^\(\d+-col\s")


def _load(workdir):
    manifests = [json.load(open(p))
                 for p in sorted(glob.glob(os.path.join(workdir, "tables", "*", "manifest.json")))]
    groups_path = os.path.join(workdir, "groups_v2.json")
    if not os.path.exists(groups_path):
        groups_path = os.path.join(workdir, "groups.json")
    groups = json.load(open(groups_path)) if os.path.exists(groups_path) else []
    qr_path = os.path.join(workdir, "quality_report.json")
    quality = json.load(open(qr_path)) if os.path.exists(qr_path) else None
    return manifests, groups, quality


def compute(workdir):
    manifests, groups, quality = _load(workdir)
    tables = [t for mf in manifests for t in mf["tables"]]
    n = max(len(tables), 1)

    # extraction: mean per-table quality score (the engine's own 0-1 scorer)
    scores = [t["extraction_quality"]["score"] for t in tables if t.get("extraction_quality")]
    extraction = 100 * (sum(scores) / max(len(scores), 1))

    # retry effectiveness: of tables where a retry ran, how many did the
    # retry improve (kept strategy != first attempted strategy)
    retried = [t for t in tables if len(t.get("extract_attempts") or []) > 1]
    improved = [t for t in retried
                if t["extract_attempts"][0]["score"] < (t.get("extract_best_score") or 0)]
    retry_effectiveness = 100 * (len(improved) / max(len(retried), 1)) if retried else None

    # header naming: fraction of all columns carrying a real name
    all_cols = [c for t in tables for c in t.get("columns", [])]
    named_cols = [c for c in all_cols if not _FALLBACK_COL.fullmatch(str(c))]
    header_naming = 100 * (len(named_cols) / max(len(all_cols), 1))

    # table titling: real title extracted (manifest name present + not a
    # generated "(N-col ...)" placeholder)
    titled = [t for t in tables if t.get("name") and not _FALLBACK_TITLE.match(str(t["name"]))]
    table_titling = 100 * (len(titled) / n)

    # grouping consolidation: fraction of member tables that live in a
    # multi-period panel (the whole point of the batch pipeline)
    members_total = sum(g["n_members"] for g in groups) or 1
    members_paneled = sum(g["n_members"] for g in groups if g["n_periods"] >= 2)
    grouping = 100 * (members_paneled / members_total)

    # panel depth: mean period-coverage of multi-period panels, relative to
    # the corpus's total period span
    n_periods_corpus = len({mf["period"] for mf in manifests}) or 1
    multi = [g for g in groups if g["n_periods"] >= 2]
    # per-panel coverage clamped to the corpus span — a group can't cover
    # more periods than the corpus has, but a stale/hand-edited groups file
    # (or a synthetic fixture) may claim otherwise; never let that push the
    # score past 100.
    panel_depth = 100 * (sum(min(g["n_periods"], n_periods_corpus) for g in multi)
                          / (max(len(multi), 1) * n_periods_corpus)) if multi else 0.0

    # auto confidence (Loop 5): groups the analyst can skim vs must review
    auto = sum(1 for g in groups if g.get("confidence") == "auto")
    auto_confidence = 100 * (auto / max(len(groups), 1)) if groups else None

    # numeric readiness from the quality gate (already 0-1)
    numeric_readiness = None
    if quality:
        v = quality["checks"].get("mean_numeric_readiness", {}).get("value")
        numeric_readiness = 100 * v if v is not None else None

    def r(x):
        return None if x is None else round(x, 1)

    return {
        "workdir": os.path.abspath(workdir),
        "corpus": {
            "pdfs": len(manifests),
            "periods": n_periods_corpus,
            "tables": len(tables),
            "groups": len(groups),
            "singleton_groups": sum(1 for g in groups if g["n_periods"] == 1),
        },
        "scores": {
            "extraction": r(extraction),
            "retry_effectiveness": r(retry_effectiveness),
            "header_naming": r(header_naming),
            "table_titling": r(table_titling),
            "grouping": r(grouping),
            "panel_depth": r(panel_depth),
            "auto_confidence": r(auto_confidence),
            "numeric_readiness": r(numeric_readiness),
        },
        "baseline": {
            "tables_scored": len(scores),
            "tables_retried": len(retried),
            "retries_improved": len(improved),
            "columns_total": len(all_cols),
            "columns_named": len(named_cols),
            "tables_titled": len(titled),
            "members_total": members_total,
            "members_in_multi_period_panels": members_paneled,
            "multi_period_panels": len(multi),
        },
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("workdir")
    ap.add_argument("--out", default=None,
                    help="output path (default: <workdir>/engine_scorecard.json)")
    args = ap.parse_args()

    card = compute(args.workdir)
    out = args.out or os.path.join(args.workdir, "engine_scorecard.json")
    with open(out, "w") as f:
        json.dump(card, f, indent=1)

    print(f"scorecard -> {out}")
    for k, v in card["scores"].items():
        bar = "#" * int((v or 0) / 5)
        print(f"  {k:20s} {v if v is not None else '—':>6}  {bar}")


if __name__ == "__main__":
    main()
