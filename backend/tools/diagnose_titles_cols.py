"""Diagnostic: enumerate the tables that ship UNTITLED or with phantom col_N
columns, with enough raw context to root-cause WHY.

For every table that PASSES validation we record, when it is a failure case:
 - untitled: caption seen by extract_table_name + raw first rows + detected header_rows
 - phantom : which final columns are col_N + the raw header region they came from

Usage: python backend/tools/diagnose_titles_cols.py <pdf> <out.json> [max_pages]
       python backend/tools/diagnose_titles_cols.py --corpus <outdir> [max_pages] [workers]
"""
import glob
import json
import os
import re
import sys
import tempfile
import warnings

warnings.filterwarnings("ignore")
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
FALLBACK_NAME = re.compile(r"^Table\s+\d+\s+\(p\.")
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _rows(df, n):
    return [[str(v)[:40] for v in r] for r in df.head(n).values.tolist()]


def run(pdf, out_path, max_pages=40):
    reader = PdfReader(pdf)
    total = len(reader.pages)
    use = min(total, max_pages)
    writer = PdfWriter()
    for p in range(use):
        writer.add_page(reader.pages[p])
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    writer.write(tmp); tmp.close()

    untitled, phantom = [], []
    passed = []
    try:
        tables = extract_tables(tmp.name)
    except Exception:
        tables = []
    for t in tables:
        try:
            raw = t["dataframe"]
            raw_head = _rows(raw, 4)
            df = clean_dataframe(raw)
            df = split_panels(df)
            df = reassemble_wrapped_rows(df)
            df = translate_dataframe(df)
            h = detect_header_rows(df)
            header_region = _rows(df, h) if h else []
            cap = t.get("caption")
            cap_tr = translate_text(cap) if cap else None
            nm = extract_table_name(df, h, cap_tr)
            df = apply_headers(df, h)
            df = clean_headers(df)
            df = merge_continuation_values(df)
            df = lift_section_rows(df)
            df = normalize_numeric_columns(df)
            s = validate_table(df)
            if s["passed"]:
                passed.append({"table_id": t["table_id"], "name": nm, "page": t["page"],
                               "df": df, "caption": cap, "caption_tr": cap_tr,
                               "raw_head": raw_head, "header_rows": h,
                               "header_region": header_region, "flavor": t.get("flavor")})
        except Exception:
            pass
    os.unlink(tmp.name)

    stitched = stitch_tables([{"table_id": i["table_id"], "name": i["name"] or "",
                               "page": i["page"], "df": i["df"], "pages": [i["page"]],
                               "_diag": i} for i in passed])

    for s in stitched:
        df = s["df"]
        name = s["name"]
        diag = s.get("_diag", {})
        cols = [str(c) for c in df.columns]
        coln = [c for c in cols if COL_N.fullmatch(c)]
        named = bool(name) and not FALLBACK_NAME.match(str(name))
        base = {"pdf": os.path.basename(pdf), "page": s.get("page"),
                "cols": cols, "ncols": len(cols),
                "flavor": diag.get("flavor")}
        if not named:
            untitled.append({**base, "name": name,
                             "caption": (diag.get("caption") or "")[:300],
                             "caption_tr": (diag.get("caption_tr") or "")[:300],
                             "header_rows": diag.get("header_rows"),
                             "raw_head": diag.get("raw_head"),
                             "header_region": diag.get("header_region")})
        if coln:
            phantom.append({**base, "name": name,
                            "coln_cols": coln,
                            "coln_frac": round(len(coln) / max(len(cols), 1), 3),
                            "header_rows": diag.get("header_rows"),
                            "raw_head": diag.get("raw_head"),
                            "header_region": diag.get("header_region")})

    result = {"pdf": os.path.basename(pdf), "tables_passed": len(stitched),
              "untitled_count": len(untitled), "phantom_count": len(phantom),
              "untitled": untitled, "phantom": phantom}
    with open(out_path, "w") as f:
        json.dump(result, f, default=str)
    print(json.dumps({"pdf": os.path.basename(pdf), "passed": len(stitched),
                      "untitled": len(untitled), "phantom": len(phantom)}))


def _slug(p):
    return os.path.splitext(os.path.basename(p))[0][:50].replace(" ", "_")


def _one(a):
    pdf, outdir, cap = a
    try:
        run(pdf, os.path.join(outdir, _slug(pdf) + ".json"), cap)
        return (os.path.basename(pdf), "ok")
    except Exception as e:
        return (os.path.basename(pdf), f"ERR {type(e).__name__}: {e}")


def corpus(outdir, cap=40, workers=4):
    from multiprocessing import Pool
    os.makedirs(outdir, exist_ok=True)
    pdfs = sorted(glob.glob(os.path.join(ROOT, "Testpdfs/**/*.pdf"), recursive=True)
                  + glob.glob(os.path.join(ROOT, "backend/data/uploads/*.pdf")))
    tasks = [(p, outdir, cap) for p in pdfs]
    print(f"diagnosing {len(tasks)} pdfs", flush=True)
    with Pool(workers, maxtasksperchild=1) as pool:
        for name, status in pool.imap_unordered(_one, tasks):
            print(f"  {status:10} {name}", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    if sys.argv[1] == "--corpus":
        corpus(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 40,
               int(sys.argv[4]) if len(sys.argv) > 4 else 4)
    else:
        run(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 40)
