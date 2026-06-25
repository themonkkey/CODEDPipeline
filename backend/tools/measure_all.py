"""Run measure_quality over the whole test corpus with bounded concurrency.

Usage: python backend/tools/measure_all.py <outdir> [max_pages] [workers]
"""
import glob
import json
import os
import sys
import warnings
from multiprocessing import Pool

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.tools.measure_quality import run

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _slug(p):
    return os.path.splitext(os.path.basename(p))[0][:50].replace(" ", "_")


def _one(args):
    pdf, outdir, cap = args
    out = os.path.join(outdir, _slug(pdf) + ".json")
    try:
        run(pdf, out, cap)
        return (os.path.basename(pdf), "ok")
    except Exception as e:
        with open(out, "w") as f:
            json.dump({"pdf": os.path.basename(pdf), "error": f"{type(e).__name__}: {e}",
                       "tables_passed": 0, "tables": []}, f)
        return (os.path.basename(pdf), f"ERR {type(e).__name__}")


def main(outdir, cap=40, workers=5):
    os.makedirs(outdir, exist_ok=True)
    pdfs = sorted(glob.glob(os.path.join(ROOT, "Testpdfs/**/*.pdf"), recursive=True)
                  + glob.glob(os.path.join(ROOT, "backend/data/uploads/*.pdf")))
    # heaviest first so the long pole starts early
    from pypdf import PdfReader
    def pages(p):
        try:
            return len(PdfReader(p).pages)
        except Exception:
            return 0
    pdfs.sort(key=pages, reverse=True)
    tasks = [(p, outdir, cap) for p in pdfs]
    print(f"measuring {len(tasks)} pdfs, cap={cap}p, workers={workers}", flush=True)
    with Pool(workers, maxtasksperchild=1) as pool:
        for name, status in pool.imap_unordered(_one, tasks):
            print(f"  done {status:12} {name}", flush=True)
    print("ALL DONE", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 40,
         int(sys.argv[3]) if len(sys.argv) > 3 else 5)
