"""Roll per-PDF measure_quality JSONs into one corpus scorecard.

Usage: python backend/tools/aggregate_quality.py <indir> [out.json]
Prints a compact corpus summary and (optionally) writes the full structure.
"""
import glob
import json
import os
import sys


def main(indir, out=None):
    files = [f for f in glob.glob(os.path.join(indir, "*.json"))
             if not os.path.basename(f).startswith("_")]
    per_pdf = []
    all_tables = []
    failed_reasons = {}
    for f in sorted(files):
        with open(f) as fh:
            d = json.load(fh)
        if d.get("error"):
            per_pdf.append({"pdf": d["pdf"], "error": d["error"]})
            continue
        for k, v in (d.get("failed_reasons") or {}).items():
            failed_reasons[k] = failed_reasons.get(k, 0) + v
        all_tables.extend(d.get("tables", []))
        per_pdf.append({k: d.get(k) for k in (
            "pdf", "total_pages", "pages_measured", "tables_passed",
            "avg_col_n_frac", "tables_clean_cols", "avg_numeric_value_frac",
            "tables_with_category", "tables_with_composite", "naming_frac",
            "ghosts_dropped", "total_orphan_rows", "tables_with_dup_cols",
            "tables_with_numeric_cols", "tables_with_deva")})

    T = len(all_tables) or 1
    def frac(pred):
        return round(sum(1 for t in all_tables if pred(t)) / T, 3)
    corpus = {
        "pdfs_measured": len(files),
        "pdfs_errored": sum(1 for p in per_pdf if p.get("error")),
        "total_tables": len(all_tables),
        "failed_reasons": failed_reasons,
        "ghosts_dropped_total": sum(v for k, v in failed_reasons.items() if k == "index_legend_only"),
        # ---- structural / columns
        "tables_zero_coln_frac": frac(lambda t: t["col_n"] == 0),
        "mean_col_n_frac": round(sum(t["col_n_frac"] for t in all_tables) / T, 3),
        "tables_with_dup_cols_frac": frac(lambda t: t["dup_cols"] > 0),
        "tables_with_orphans_frac": frac(lambda t: t["orphan_rows"] > 0),
        # ---- headings / titles
        "named_frac": frac(lambda t: t["named"]),
        # ---- sub-headings / multi-level + sections
        "category_frac": frac(lambda t: t["has_category"]),
        "composite_frac": frac(lambda t: t["composite_cols"] > 0),
        # ---- cell content / numeric readiness
        "mean_numeric_value_frac": round(sum(t["numeric_value_frac"] for t in all_tables) / T, 3),
        "mean_numeric_readiness": (lambda r: round(sum(r) / len(r), 3) if r else None)(
            [t["numeric_readiness"] for t in all_tables if t.get("numeric_readiness") is not None]),
        "tables_with_numeric_cols_frac": frac(lambda t: t["numeric_cols"] > 0),
        # ---- hindi leakage
        "tables_with_deva_frac": frac(lambda t: t["deva_rows"] > 0),
        "per_pdf": per_pdf,
    }
    brief = {k: v for k, v in corpus.items() if k != "per_pdf"}
    print(json.dumps(brief, indent=1))
    if out:
        with open(out, "w") as f:
            json.dump(corpus, f)
        print(f"\nwrote {out}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
