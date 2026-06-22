"""Headless stress-run: full pipeline over one PDF (chunked), CSVs + metadata JSON.

Usage: python backend/tools/stress_run.py <pdf> <outdir> [chunk_size]
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
from backend.app.cleaning.wrapped_row_reassembler import reassemble_wrapped_rows
from backend.app.extract.table_extractor import extract_tables
from backend.app.standardization.table_name_extractor import extract_table_name
from backend.app.standardization.table_stitcher import stitch_tables
from backend.app.translation.hindi_translator import translate_dataframe, translate_text
from backend.app.validation.table_validator import validate_table

DEVA = re.compile(r"[ऀ-ॿ]")
_ORPHAN_NUM = re.compile(r"^\(?-?[\d,]+(\.\d+)?%?\)?$")


def _orphan_rows(df):
    """Rows with an empty first cell where ≥80% of populated value cells are
    numeric — the label-less wrap artifacts reassembly is meant to eliminate.
    The 80% frac prevents counting legitimate indented sub-rows in wide tables
    where some cells carry text or blanks (Gap B fix)."""
    n = 0
    for row in df.astype(str).values.tolist():
        if not row[0].strip():
            populated = [v.strip() for v in row[1:] if v.strip() and v.strip() not in ("nan", "None")]
            num = sum(1 for v in populated if _ORPHAN_NUM.match(v))
            if populated and num >= 2 and num / len(populated) >= 0.8:
                n += 1
    return n


def run(pdf_path, outdir, chunk=50):
    os.makedirs(outdir, exist_ok=True)
    reader = PdfReader(pdf_path)
    n_pages = len(reader.pages)

    passed_items, failed, found = [], [], 0
    tid = 0

    for start in range(0, n_pages, chunk):
        end = min(start + chunk, n_pages)
        writer = PdfWriter()
        for p in range(start, end):
            writer.add_page(reader.pages[p])
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            writer.write(tmp)
            chunk_path = tmp.name
        try:
            tables = extract_tables(chunk_path)
        except Exception as e:
            failed.append({"page": f"{start+1}-{end}", "reason": f"extract:{type(e).__name__}:{e}"})
            os.unlink(chunk_path)
            continue
        os.unlink(chunk_path)

        for t in tables:
            found += 1
            tid += 1
            real_page = start + t["page"]
            try:
                df = clean_dataframe(t["dataframe"])
                df = reassemble_wrapped_rows(df)
                df = translate_dataframe(df)
                h = detect_header_rows(df)
                cap = t.get("caption")
                nm = extract_table_name(df, h, translate_text(cap) if cap else None)
                df = apply_headers(df, h)
                df = clean_headers(df)
                s = validate_table(df)
            except Exception as e:
                failed.append({"page": real_page, "reason": f"{type(e).__name__}: {e}"})
                continue
            if not s["passed"]:
                failed.append({"page": real_page, "reason": s["reason"], "flavor": t["flavor"]})
                continue
            passed_items.append({
                "table_id": tid,
                "name": nm,
                "titled": nm is not None,
                "page": real_page,
                "df": df,
                "flavor": t["flavor"],
            })
        del tables

    stitched = stitch_tables(passed_items)

    meta = []
    for it in stitched:
        # fallback names only AFTER stitching — a pre-assigned
        # "Table N (p.X)" reads as a strong title and blocks merges
        if not it["name"]:
            it["name"] = f"Table {it['table_id']} (p.{it['page']})"
        df = it["df"]
        cols = list(df.columns)
        coln = sum(1 for c in cols if re.fullmatch(r"col(_\d+)?", str(c)))
        deva_cells = int(df.astype(str).apply(lambda s: s.str.contains(DEVA)).to_numpy().sum())
        deva_cols = sum(1 for c in cols if DEVA.search(str(c)))
        csv_name = f"table_{it['table_id']}.csv"
        df.to_csv(os.path.join(outdir, csv_name), index=False)
        meta.append({
            "table_id": it["table_id"], "name": it["name"], "titled": it["titled"],
            "pages": it.get("pages", [it["page"]]), "rows": len(df), "cols": len(cols),
            "col_n_frac": round(coln / len(cols), 2) if cols else 1.0,
            "orphan_rows": _orphan_rows(df),
            "deva_cells": deva_cells, "deva_cols": deva_cols,
            "flavor": it["flavor"], "columns": cols, "csv": csv_name,
        })
        del df

    summary = {
        "pdf": os.path.basename(pdf_path), "pages": n_pages, "found": found,
        "passed_pre_stitch": len(passed_items), "after_stitch": len(stitched),
        "stitch_merges": len(passed_items) - len(stitched),
        "orphan_rows_total": sum(m["orphan_rows"] for m in meta),
        "failed": failed, "tables": meta,
    }
    with open(os.path.join(outdir, "metadata.json"), "w") as f:
        json.dump(summary, f, indent=1, default=str)
    print(json.dumps({k: summary[k] for k in
                      ("pdf", "pages", "found", "passed_pre_stitch", "after_stitch", "stitch_merges")}))
    print("failed:", len(failed))


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 50)
