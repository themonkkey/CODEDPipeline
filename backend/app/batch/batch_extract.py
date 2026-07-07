"""Folder of same-source PDFs -> per-PDF kept tables, extracted in parallel.

Runs the SAME canonical pipeline as the single-PDF app (mirrors
`regression_guards._pipeline` + `stitch_tables`), so each PDF yields exactly the
kept, stitched tables the interactive app would produce. Results are written to
disk (one CSV per kept table + a per-PDF manifest) so a 50-100 PDF batch never
has to hold every dataframe in memory at once.

Parallelism reuses the proven `measure_all` pattern: a process Pool with
`maxtasksperchild=1` (each worker handles one PDF then exits, bounding peak RAM)
and heaviest-first ordering so the long pole starts early.
"""
import glob
import json
import os
import re
import sys
import tempfile
import warnings
from multiprocessing import Pool

warnings.filterwarnings("ignore")

from pypdf import PdfReader, PdfWriter

from backend.app.cleaning.header_builder import apply_headers
from backend.app.cleaning.header_detector import detect_header_rows
from backend.app.cleaning.header_postprocessor import clean_headers
from backend.app.cleaning.numeric_normalizer import normalize_numeric_columns
from backend.app.cleaning.panel_splitter import split_panels
from backend.app.cleaning.section_lifter import lift_section_rows
from backend.app.cleaning.universal_cleaner import clean_dataframe
from backend.app.cleaning.wrapped_row_reassembler import (
    merge_continuation_values,
    reassemble_wrapped_rows,
)
from backend.app.extract.table_extractor import extract_tables
from backend.app.profile.table_profiler import classify_table
from backend.app.standardization.table_name_extractor import extract_table_name
from backend.app.standardization.table_stitcher import stitch_tables
from backend.app.translation.hindi_translator import translate_dataframe, translate_text
from backend.app.validation.table_validator import validate_table
from backend.app.agents import header_agent

_BATCH = 25  # page window for the OOM-bounded slice loop (mirrors app.py)

# Fix 8: score 0.0-1.0 for how cleanly a table was extracted. Loop Spec 1
# moved the formula to backend/app/extract/quality.py so table_extractor's
# retry loop and this batch path share ONE scorer; keep the local alias so
# every existing call site here (_extraction_quality(df)) is untouched.
from backend.app.extract.quality import score_table as _extraction_quality

# Loop Spec 2: the header agent's own recomputed-quality signal used to be
# thrown away (rename applied unconditionally, new score just stored). A
# rename only "counts" if it clears the old score by this margin — small
# reshuffles of column order/whitespace can nudge header_coherence a hair
# without the agent having actually fixed anything.
_HEADER_RENAME_MIN_GAIN = 0.05
_HEADER_RETRY_SAMPLE_ROWS = 5  # richer than the first attempt's df.head(3)

_MONTHS = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
    "jan": "01", "feb": "02", "mar": "03", "apr": "04", "jun": "06",
    "jul": "07", "aug": "08", "sep": "09", "sept": "09", "oct": "10",
    "nov": "11", "dec": "12",
}
_YEAR_RANGE = re.compile(r"(19|20)\d{2}([-–/]\d{2,4})?")


def parse_period(pdf_path, table_names=None):
    """A sortable period key for a report, parsed from the filename first
    (most reliable for a same-source series), then any table title, else the
    filename stem. Returns (period_key, basis): e.g. ("2026-01", "filename"),
    ("2017-18", "filename"), ("2011", "title")."""
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    low = stem.lower()

    # year (+optional range) anywhere in the filename
    ym = _YEAR_RANGE.search(stem)
    # month name in the filename -> pin to YYYY-MM when a 4-digit year is
    # present. Use letter-boundary lookarounds, not \b: filenames join tokens
    # with underscores ("Central_April_2026") and "_" is a \w char, so \b would
    # not fire between "_" and "april".
    for name, mm in sorted(_MONTHS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"(?<![a-z]){name}(?![a-z])", low):
            yr = re.search(r"(19|20)\d{2}", stem)
            if yr:
                return f"{yr.group(0)}-{mm}", "filename"
    if ym:
        return ym.group(0).replace("–", "-").replace("/", "-"), "filename"

    # fall back to a year found in a table title
    for nm in (table_names or []):
        m = _YEAR_RANGE.search(str(nm or ""))
        if m:
            return m.group(0).replace("–", "-").replace("/", "-"), "title"

    return stem, "stem"


def _pipeline_tables(pdf_path):
    """Every raw table through the canonical per-table pipeline (no stitch yet).
    Returns kept (validated) items: {table_id, name, page, df, archetype}.

    extract_attempts / extract_low_quality are Loop Spec 1 item flags —
    table_extractor's per-strategy retry record, threaded through the SAME
    way "archetype" already is, so a table that stayed low-quality across
    every strategy is still traceable at manifest time (see process_pdf)."""
    items = []
    for t in extract_tables(pdf_path):
        df = clean_dataframe(t["dataframe"])
        df = split_panels(df)
        df = reassemble_wrapped_rows(df)
        df = translate_dataframe(df)
        archetype = classify_table(df)["archetype"]
        h = detect_header_rows(df)
        cap = t.get("caption")
        nm = extract_table_name(df, h, translate_text(cap) if cap else None)
        df = apply_headers(df, h)
        df = clean_headers(df)
        df = merge_continuation_values(df)
        df = lift_section_rows(df)
        df = normalize_numeric_columns(df)
        if not validate_table(df)["passed"]:
            continue
        items.append({
            "table_id": t["table_id"], "name": nm, "page": t["page"],
            "df": df, "archetype": archetype,
            "extract_attempts": t.get("attempts", []),
            "extract_low_quality": t.get("low_quality", False),
            "extract_review_needed": t.get("review_needed", False),
        })
    return items


def _slice_pdf(reader, lo, hi):
    writer = PdfWriter()
    for p in range(lo, hi):
        writer.add_page(reader.pages[p])
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    writer.write(tmp)
    tmp.close()
    return tmp.name


def extract_pdf(pdf_path, max_pages=None):
    """Kept, stitched tables for one PDF, page-batched to bound memory on big
    reports (same strategy as the single-PDF app driver)."""
    reader = PdfReader(pdf_path)
    n_pages = len(reader.pages)
    if max_pages:
        n_pages = min(n_pages, max_pages)

    if n_pages <= 2 * _BATCH:
        path = pdf_path if not max_pages else _slice_pdf(reader, 0, n_pages)
        try:
            items = _pipeline_tables(path)
        finally:
            if path != pdf_path:
                os.unlink(path)
    else:
        import gc
        items = []
        next_id = 1
        for lo in range(0, n_pages, _BATCH):
            hi = min(lo + _BATCH, n_pages)
            btmp = _slice_pdf(reader, lo, hi)
            try:
                batch = _pipeline_tables(btmp)
            finally:
                os.unlink(btmp)
            for it in batch:
                it["page"] += lo
                it["table_id"] = next_id
                next_id += 1
            items.extend(batch)
            del batch
            gc.collect()

    items.sort(key=lambda it: (it["page"], it["table_id"]))
    return stitch_tables(items)


def _attempt_header_rename(df, table_meta, sample_rows):
    """One header-agent call-and-apply cycle: ask for a rename, apply it if
    given one, and rescore. Returns (renamed_df_or_None, quality_or_None,
    rename_map) — the Nones signal "the agent didn't fire or returned
    nothing", which the caller treats the same as a failed attempt.

    Pulled out of `_verify_and_apply_header_agent` so the first (3-row) and
    retry (5-row) attempts share one apply+rescore path.
    """
    rename_map = header_agent.fix_headers(table_meta, sample_rows)
    if not rename_map:
        return None, None, {}
    renamed_df = df.rename(columns=rename_map)
    return renamed_df, _extraction_quality(renamed_df), rename_map


def _verify_and_apply_header_agent(df, table_meta):
    """Loop Spec 2: verify the header agent's rename actually helped before
    keeping it, instead of applying it unconditionally and discarding the
    recomputed score.

    - recomputed_score >= old_score + 0.05 -> keep the rename.
    - otherwise -> revert to the original columns and retry the agent ONCE
      more with a richer prompt (5 sample rows instead of 3).
    - if the retry also fails to clear the bar -> revert to the original
      columns and flag the table as header_agent_failed.

    Never loops more than twice total (original attempt + 1 retry), and
    never keeps a rename that leaves the table worse off than before the
    agent touched it.

    Returns (final_df, final_quality, rename_map_applied_or_None,
    header_agent_record, header_agent_failed) where header_agent_record is
    the per-table manifest entry {applied, old_score, new_score, retried}.
    `new_score` is the score of the last attempt made (the retry's score if
    a retry happened, else the first attempt's), even when that attempt was
    ultimately rejected — so the manifest shows how close it got.
    """
    old_quality = table_meta["extraction_quality"]
    old_score = old_quality["score"]

    first_df, first_quality, first_map = _attempt_header_rename(
        df, table_meta, df.head(3).to_dict(orient="records"),
    )
    if first_map:
        if first_quality["score"] >= old_score + _HEADER_RENAME_MIN_GAIN:
            record = {"applied": True, "old_score": old_score,
                      "new_score": first_quality["score"], "retried": False}
            return first_df, first_quality, first_map, record, False

        # Verification failed: revert, retry once with a richer prompt.
        retry_df, retry_quality, retry_map = _attempt_header_rename(
            df, table_meta, df.head(_HEADER_RETRY_SAMPLE_ROWS).to_dict(orient="records"),
        )
        if retry_map and retry_quality["score"] >= old_score + _HEADER_RENAME_MIN_GAIN:
            record = {"applied": True, "old_score": old_score,
                      "new_score": retry_quality["score"], "retried": True}
            return retry_df, retry_quality, retry_map, record, False

        last_score = retry_quality["score"] if retry_map else first_quality["score"]
        record = {"applied": False, "old_score": old_score,
                  "new_score": last_score, "retried": True}
        return df, old_quality, None, record, True

    # Agent never fired / returned nothing on the first attempt -> nothing
    # to verify, nothing to retry, no failure (there was no rename to fail).
    record = {"applied": False, "old_score": old_score, "new_score": None, "retried": False}
    return df, old_quality, None, record, False


def process_pdf(pdf_path, workdir, max_pages=None):
    """Extract one PDF and write its kept tables + manifest under workdir.
    Returns the manifest dict (the unit a worker hands back)."""
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    out_dir = os.path.join(workdir, "tables", stem)
    os.makedirs(out_dir, exist_ok=True)

    items = extract_pdf(pdf_path, max_pages)
    # Fix 3: batch context only — drop tables with < 3 data rows. These are
    # one-off KPI strips, case-study snippets, or cover-page fragments that
    # will never form a meaningful panel and inflate the group count.
    items = [it for it in items if it["df"].shape[0] >= 3]
    period, basis = parse_period(pdf_path, [it["name"] for it in items])

    tables = []
    for it in items:
        df = it["df"]
        quality = _extraction_quality(df)
        table_meta = {
            "columns": [str(c) for c in df.columns],
            "extraction_quality": quality,
        }
        # Header agent: fires only on low-quality tables (score < 0.70), cached.
        # Loop Spec 2: verify the rename actually helped (>= +0.05) before
        # keeping it; revert + retry once with a richer prompt otherwise.
        df, quality, rename_map, header_agent_record, header_agent_failed = (
            _verify_and_apply_header_agent(df, table_meta)
        )

        # Loop Spec 1: extract_tables()'s own per-strategy retry record for
        # this table (lattice / stream / ocr attempts + scores), independent
        # of the header-agent's post-naming `quality` above. Surfaced as
        # plain columns (not just the raw attempts list) so a manifest
        # consumer can filter on them without reparsing extract_attempts.
        attempts = it.get("extract_attempts", [])
        extract_best_score = max((a["score"] for a in attempts), default=None)
        strategies_tried = ",".join(a["strategy"] for a in attempts)

        csv_path = os.path.join(out_dir, f"table_{it['table_id']}.csv")
        df.to_csv(csv_path, index=False)
        tables.append({
            "table_id": it["table_id"],
            "name": it.get("name"),
            "page": it["page"],
            "pages": it.get("pages", [it["page"]]),
            "rows": int(df.shape[0]),
            "cols": int(df.shape[1]),
            "columns": [str(c) for c in df.columns],
            "archetype": it.get("archetype", "statistical"),
            "extraction_quality": quality,
            "agent_renames": rename_map or None,
            "header_agent": header_agent_record,
            "header_agent_failed": header_agent_failed,
            "csv": os.path.relpath(csv_path, workdir),
            "extract_attempts": attempts,
            "extract_best_score": extract_best_score,
            "extract_strategies_tried": strategies_tried,
            "extract_low_quality": it.get("extract_low_quality", False),
            "extract_review_needed": it.get("extract_review_needed", False),
        })

    # Best-effort keeps that never cleared the review bar (best score < 0.50
    # across every extraction strategy tried) — flagged distinctly so a human
    # or Loop 3's quality gate can find them without scanning every table.
    review_needed = [
        {"table_id": t["table_id"], "name": t["name"], "page": t["page"],
         "extract_best_score": t["extract_best_score"]}
        for t in tables if t["extract_review_needed"]
    ]

    # Loop Spec 2: tables where the header agent fired but neither the
    # original nor the retried rename cleared the +0.05 verification bar —
    # kept on original columns, surfaced here the same way review_needed
    # surfaces low-score extractions, so a human/Loop 3 gate can spot-check
    # the ones the agent gave up on.
    header_agent_failures = [
        {"table_id": t["table_id"], "name": t["name"], "page": t["page"],
         "header_agent": t["header_agent"]}
        for t in tables if t["header_agent_failed"]
    ]

    manifest = {
        "pdf": os.path.basename(pdf_path),
        "stem": stem,
        "period": period,
        "period_basis": basis,
        "n_tables": len(tables),
        "tables": tables,
        "review_needed": review_needed,
        "header_agent_failures": header_agent_failures,
    }
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    return manifest


def _worker(args):
    pdf, workdir, max_pages = args
    try:
        m = process_pdf(pdf, workdir, max_pages)
        return (os.path.basename(pdf), "ok", m)
    except Exception as e:
        return (os.path.basename(pdf), f"ERR {type(e).__name__}: {e}", None)


def extract_folder(folder, workdir, workers=4, max_pages=None):
    """Extract every PDF in a folder in parallel. Returns the list of per-PDF
    manifests (skipping any that errored, which are reported to stdout)."""
    pdfs = sorted(glob.glob(os.path.join(folder, "*.pdf"))
                  + glob.glob(os.path.join(folder, "*.PDF")))
    if not pdfs:
        raise SystemExit(f"no PDFs found in {folder}")

    def _pages(p):
        try:
            return len(PdfReader(p).pages)
        except Exception:
            return 0
    pdfs.sort(key=_pages, reverse=True)  # heaviest first

    os.makedirs(workdir, exist_ok=True)
    tasks = [(p, workdir, max_pages) for p in pdfs]
    print(f"extracting {len(tasks)} pdfs, workers={workers}"
          + (f", cap={max_pages}p" if max_pages else ""), flush=True)

    manifests = []
    with Pool(workers, maxtasksperchild=1) as pool:
        for name, status, m in pool.imap_unordered(_worker, tasks):
            print(f"  {status:14} {name}"
                  + (f" -> {m['n_tables']} tables, period {m['period']}" if m else ""),
                  flush=True)
            if m:
                manifests.append(m)

    manifests.sort(key=lambda m: m["period"])
    return manifests
