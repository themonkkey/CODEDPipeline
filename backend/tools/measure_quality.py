"""Objective output-quality metrics for the CURRENT pipeline.

Runs the full production per-table pipeline (incl. the 5 new fixes) + stitch over
a PDF (page-capped for big files) and emits compact JSON: per-table structural /
heading / cell-content metrics plus a summary. Feeds the grading workflow.

Usage: python backend/tools/measure_quality.py <pdf> <out.json> [max_pages]
"""
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from pypdf import PdfReader, PdfWriter

from backend.app.cleaning.header_builder import apply_headers
from backend.app.cleaning.header_detector import detect_header_rows
from backend.app.cleaning.header_postprocessor import clean_headers
from backend.app.cleaning.universal_cleaner import clean_dataframe
from backend.app.cleaning.wrapped_row_reassembler import reassemble_wrapped_rows, merge_continuation_values
from backend.app.cleaning.panel_splitter import split_panels
from backend.app.cleaning.section_lifter import lift_section_rows
from backend.app.cleaning.numeric_normalizer import normalize_numeric_columns
from backend.app.extract.table_extractor import extract_tables
from backend.app.standardization.table_name_extractor import extract_table_name
from backend.app.standardization.table_stitcher import stitch_tables
from backend.app.translation.hindi_translator import translate_dataframe, translate_text
from backend.app.validation.table_validator import validate_table

COL_N = re.compile(r"^col(_\d+)?$")
NUM_STR = re.compile(r"^\(?-?[\d,]+(\.\d+)?%?\)?$")
YEAR = re.compile(r"20\d\d|19\d\d|\d{4}_\d{2}")
# subdivision / group tokens that mark a genuine multi-level (group+sub) name
_SUBLABEL = re.compile(
    r"(^|_)(rural|urban|male|female|males|females|persons?|total|combined|"
    r"value|volume|nfhs\d|cities|metropolitan|crude|imports?|exports?|"
    r"production|cases|rate|rural_urban|\d{4}_\d{2})($|_)")


def _is_composite(col):
    """A column name encoding >=2 header levels: either >=3 underscore parts,
    or >=2 parts with a recognised subdivision/group/year token. Excludes plain
    two-word concept names ('ministry_department', 'crime_head')."""
    parts = col.split("_")
    if len(parts) >= 3:
        return True
    return len(parts) >= 2 and (bool(_SUBLABEL.search(col)) or bool(YEAR.search(col)))
DEVA = re.compile(r"[ऀ-ॿ]")
FALLBACK_NAME = re.compile(r"^Table\s+\d+\s+\(p\.")


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v


def measure_table(df, name):
    cols = [str(c) for c in df.columns]
    nc = len(cols)
    nr = len(df)
    col_n = sum(1 for c in cols if COL_N.fullmatch(c))
    dup = nc - len(set(cols))
    has_cat = any(c == "category" for c in cols)
    # composite multi-level names: a merged group+sub header (year, rural/urban,
    # value/volume, >=3 parts …) — not just year-spans, which undercounted badly
    composite = sum(1 for c in cols if _is_composite(c))

    # cell content over value columns (skip the first label column)
    val = df.iloc[:, 1:] if nc > 1 else df.iloc[:, :0]
    populated = 0
    numeric = 0
    for row in val.values.tolist():
        for v in row:
            s = str(v).strip()
            if s in ("", "nan", "None"):
                continue
            populated += 1
            if _is_num(v):
                numeric += 1
    numeric_frac = numeric / populated if populated else 0.0

    # numeric-typed columns (>=80% of populated cells are real numbers)
    # AND honest readiness: among columns that are INTENDED numeric (majority of
    # cells are number-like), what fraction of cells actually got typed. This
    # excludes legitimate text dimension columns (state, indicator, period) from
    # the denominator, which the older numeric_value_frac wrongly penalised.
    num_cols = 0
    intended_numeric = 0
    purity_sum = 0.0
    for ci in range(1, nc):
        colvals = df.iloc[:, ci].tolist()
        pop = [v for v in colvals if str(v).strip() not in ("", "nan", "None")]
        if not pop:
            continue
        typed = sum(1 for v in pop if _is_num(v))
        if typed / len(pop) >= 0.8:
            num_cols += 1
        numberlike = sum(1 for v in pop if _is_num(v) or NUM_STR.match(str(v).strip()))
        if numberlike / len(pop) >= 0.5:
            intended_numeric += 1
            purity_sum += typed / len(pop)
    numeric_readiness = round(purity_sum / intended_numeric, 3) if intended_numeric else None

    # orphan rows: label cell empty but >=2 numeric values in the row
    label_idx = 1 if has_cat else 0
    orphan = 0
    deva_cells = 0
    for raw in df.astype(str).values.tolist():
        row = [str(v) for v in raw]
        if DEVA.search(" ".join(row)):
            deva_cells += 1
        lbl = str(row[label_idx]).strip() if label_idx < len(row) else ""
        rest = row[label_idx + 1:]
        if not lbl and sum(1 for v in rest if NUM_STR.match(str(v).strip())) >= 2:
            orphan += 1

    named = bool(name) and not FALLBACK_NAME.match(str(name))
    return {
        "rows": nr, "cols": nc,
        "col_n": col_n, "col_n_frac": round(col_n / nc, 3) if nc else 1.0,
        "dup_cols": dup,
        "has_category": has_cat,
        "composite_cols": composite,
        "numeric_value_frac": round(numeric_frac, 3),
        "numeric_readiness": numeric_readiness,
        "intended_numeric_cols": intended_numeric,
        "numeric_cols": num_cols,
        "value_cols": max(nc - 1, 0),
        "orphan_rows": orphan,
        "deva_rows": deva_cells,
        "named": named,
        "title": str(name)[:80] if name else None,
    }


def run(pdf, out_path, max_pages=40):
    reader = PdfReader(pdf)
    total_pages = len(reader.pages)
    use_pages = min(total_pages, max_pages)
    writer = PdfWriter()
    for p in range(use_pages):
        writer.add_page(reader.pages[p])
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    writer.write(tmp)
    tmp.close()

    failed = {}
    passed = []
    ocr_recovered = 0
    try:
        tables = extract_tables(tmp.name)
    except Exception as e:
        tables = []
        failed[f"extract:{type(e).__name__}"] = failed.get(f"extract:{type(e).__name__}", 0) + 1
    for t in tables:
        if t.get("flavor") == "ocr":
            ocr_recovered += 1
        try:
            df = clean_dataframe(t["dataframe"])
            df = split_panels(df)
            df = reassemble_wrapped_rows(df)
            df = translate_dataframe(df)
            from backend.app.profile.table_profiler import classify_table
            archetype = classify_table(df)["archetype"]
            h = detect_header_rows(df)
            cap = t.get("caption")
            nm = extract_table_name(df, h, translate_text(cap) if cap else None)
            df = apply_headers(df, h)
            df = clean_headers(df)
            df = merge_continuation_values(df)
            df = lift_section_rows(df)
            df = normalize_numeric_columns(df)
            s = validate_table(df)
            if s["passed"]:
                passed.append({"table_id": t["table_id"], "name": nm, "page": t["page"],
                               "df": df, "archetype": archetype})
            else:
                failed[s["reason"]] = failed.get(s["reason"], 0) + 1
        except Exception as e:
            failed[f"crash:{type(e).__name__}"] = failed.get(f"crash:{type(e).__name__}", 0) + 1
    os.unlink(tmp.name)

    stitched = stitch_tables([{"table_id": i["table_id"], "name": i["name"] or "",
                               "page": i["page"], "df": i["df"], "pages": [i["page"]],
                               "archetype": i.get("archetype")}
                              for i in passed])
    tabs = [measure_table(s["df"], s["name"]) for s in stitched]

    n = len(tabs) or 1
    summary = {
        "pdf": os.path.basename(pdf),
        "total_pages": total_pages, "pages_measured": use_pages,
        "tables_passed": len(tabs),
        "failed_reasons": failed,
        "ghosts_dropped": failed.get("index_legend_only", 0),
        "ocr_recovered": ocr_recovered,
        # structural quality
        "avg_col_n_frac": round(sum(t["col_n_frac"] for t in tabs) / n, 3),
        "tables_clean_cols": sum(1 for t in tabs if t["col_n"] == 0),
        "tables_with_dup_cols": sum(1 for t in tabs if t["dup_cols"] > 0),
        "tables_with_orphans": sum(1 for t in tabs if t["orphan_rows"] > 0),
        "total_orphan_rows": sum(t["orphan_rows"] for t in tabs),
        # heading quality
        "tables_named": sum(1 for t in tabs if t["named"]),
        "naming_frac": round(sum(1 for t in tabs if t["named"]) / n, 3),
        # sub-heading / multi-level
        "tables_with_category": sum(1 for t in tabs if t["has_category"]),
        "tables_with_composite": sum(1 for t in tabs if t["composite_cols"] > 0),
        # cell content / numeric readiness
        "avg_numeric_value_frac": round(sum(t["numeric_value_frac"] for t in tabs) / n, 3),
        "avg_numeric_readiness": (
            round(sum(t["numeric_readiness"] for t in tabs if t["numeric_readiness"] is not None)
                  / max(sum(1 for t in tabs if t["numeric_readiness"] is not None), 1), 3)),
        "tables_with_numeric_cols": sum(1 for t in tabs if t["numeric_cols"] > 0),
        "tables_with_deva": sum(1 for t in tabs if t["deva_rows"] > 0),
        "tables": tabs,
    }
    with open(out_path, "w") as f:
        json.dump(summary, f, default=str)
    brief = {k: summary[k] for k in (
        "pdf", "total_pages", "pages_measured", "tables_passed", "avg_col_n_frac",
        "tables_clean_cols", "avg_numeric_value_frac", "tables_with_category",
        "tables_with_composite", "naming_frac", "ghosts_dropped", "total_orphan_rows")}
    print(json.dumps(brief))


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 40)
