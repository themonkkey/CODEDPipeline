"""Loop Spec 4: re-check quarantined tables after the extraction engine improves.

`backend.app.main.run_pipeline` writes failed_tables.csv once per job and
nothing ever reads it again — quarantine today is permanent. Loop Spec 1 taught
`extract_tables()` to retry a low-scoring page with other extraction
strategies before giving up, so some already-quarantined rows may now extract
cleanly without anyone re-uploading the source PDF. This tool re-runs
extraction — scoped to just the page(s) actually being rechecked this run
for a given PDF via extract_tables()'s `pages` parameter (Loop Spec 4), not
the whole document, so a single quarantined row on page 40 of a 200-page
report does not pay for camelot to re-scan the other 199 — plus the SAME
per-table cleaning chain CLAUDE.md pins (clean_dataframe
-> split_panels -> reassemble_wrapped_rows -> translate_dataframe ->
detect_header_rows -> extract_table_name -> apply_headers -> clean_headers ->
merge_continuation_values -> lift_section_rows -> normalize_numeric_columns ->
validate_table) on just the quarantined page, then:

  - promotes a now-passing row into promoted_tables.csv for a human to fold
    into a panel by hand (this tool never touches master.xlsx / table_catalog
    — that join stays a separate, human-gated step), or
  - re-quarantines a still-failing row in failed_tables.csv with a refreshed
    reason / best_score / strategies_tried.

No PDF path is stored in failed_tables.csv (see main.py's writer at the bottom
of run_pipeline) — see `_infer_pdf` for how the source PDF is located.

Avoiding redundant re-checks: since this repo has no engine-version stamp, a
`last_rechecked` column records the `--run-id` a row was last checked against.
A row already stamped with the CURRENT --run-id is skipped on the next run
(no --force needed) — bump --run-id (or pass --force) after an engine change
you actually want re-verified.

Usage:
    python backend/tools/recheck_quarantine.py [--dir backend/data/exports]
        [--pdf PATH] [--run-id ID] [--force]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# `table` is the id the row was quarantined under; it is NOT re-validated as a
# stable identifier on recheck (extract_tables reassigns ids sequentially on
# every run) — `page` is the only durable anchor, see `_run_pipeline_for_page`.
FAILED_COLUMNS = ["table", "page", "reason", "best_score", "strategies_tried", "last_rechecked"]
PROMOTED_COLUMNS = ["table", "page", "name", "reason", "score", "strategies_tried", "run_id"]


def _infer_pdf(results_dir):
    """Best-effort match of a failed_tables.csv's results dir to its source
    PDF. main.py's /api/process names uploads "{job_id}_{filename}" and drops
    results at backend/data/jobs/{job_id}/ — if `results_dir`'s basename is a
    live job_id, find its upload by that prefix. Otherwise fall back to
    Sample.pdf, the PDF test_pipeline.py runs to produce the one
    failed_tables.csv checked in today (backend/data/exports/)."""
    job_id = os.path.basename(os.path.normpath(results_dir))
    upload_dir = os.path.join(ROOT, "backend/data/uploads")
    if os.path.isdir(upload_dir):
        matches = sorted(f for f in os.listdir(upload_dir) if f.startswith(f"{job_id}_"))
        if matches:
            return os.path.join(upload_dir, matches[0])
    return os.path.join(ROOT, "Sample.pdf")


def _run_pipeline_for_page(tables, page):
    """Run the full per-table cleaning chain (main.py's order, unchanged) on
    every table `extract_tables()` currently finds on `page`, and return the
    best result: the first one that now passes `validate_table`, else the
    highest-extraction-scoring failure (so a still-quarantined row's reason
    reflects the best the improved engine could actually do).

    `tables` is an extract_tables() result already scoped (Loop Spec 4) to
    the union of pages being rechecked this run, passed in (and cached
    per-pdf) by the caller so N quarantined rows from the same PDF only pay
    for one extraction pass over just those pages, not N passes and not the
    whole document. Returns None if extract_tables no longer finds anything
    on this page at all."""
    from backend.app.cleaning.header_builder import apply_headers
    from backend.app.cleaning.header_detector import detect_header_rows
    from backend.app.cleaning.header_postprocessor import clean_headers
    from backend.app.cleaning.universal_cleaner import clean_dataframe
    from backend.app.cleaning.wrapped_row_reassembler import reassemble_wrapped_rows, merge_continuation_values
    from backend.app.cleaning.panel_splitter import split_panels
    from backend.app.cleaning.section_lifter import lift_section_rows
    from backend.app.cleaning.numeric_normalizer import normalize_numeric_columns
    from backend.app.standardization.table_name_extractor import extract_table_name
    from backend.app.translation.hindi_translator import translate_dataframe, translate_text
    from backend.app.validation.table_validator import validate_table

    best = None
    for t in tables:
        if t["page"] != page:
            continue

        strategies_tried = ",".join(a["strategy"] for a in t.get("attempts", []))
        try:
            df = clean_dataframe(t["dataframe"])
            df = split_panels(df)
            df = reassemble_wrapped_rows(df)
            df = translate_dataframe(df)
            h = detect_header_rows(df)
            cap = t.get("caption")
            name = extract_table_name(df, h, translate_text(cap) if cap else None)
            df = apply_headers(df, h)
            df = clean_headers(df)
            df = merge_continuation_values(df)
            df = lift_section_rows(df)
            df = normalize_numeric_columns(df)
            status = validate_table(df)
        except Exception as e:
            status, name = {"passed": False, "reason": f"recheck_error:{type(e).__name__}"}, None

        candidate = {
            "passed": status["passed"],
            "reason": status.get("reason"),
            "name": name,
            "best_score": t.get("best_score"),
            "strategies_tried": strategies_tried,
        }
        if candidate["passed"]:
            return candidate  # good enough — no need to check other tables on the page

        if best is None or (candidate["best_score"] or 0) > (best["best_score"] or 0):
            best = candidate

    return best


def recheck_one(pdf_path, page, tables_cache, page_scope=None):
    """`tables_cache` maps pdf_path -> extract_tables() result, shared across
    all rows in one run() call so a PDF is only ever re-extracted once no
    matter how many of its pages are quarantined.

    `page_scope` (Loop Spec 4): the set/list of pages actually being
    rechecked this run for `pdf_path` — normally prefetched and populated
    into `tables_cache` by run() BEFORE this is called, so the cache-miss
    path below is a fallback for direct/standalone calls only (e.g. a test
    calling recheck_one for a single row without going through run()). When
    it does fire, it now scopes extract_tables() to `page_scope` (or just
    `page` if no scope was given) instead of re-processing the whole PDF —
    the actual fix for the "only the failed page" claim this docstring makes
    everywhere else in this module."""
    if pdf_path not in tables_cache:
        from backend.app.extract.table_extractor import extract_tables
        tables_cache[pdf_path] = extract_tables(pdf_path, pages=page_scope or [page])
    return _run_pipeline_for_page(tables_cache[pdf_path], page)


def run(results_dir, pdf_path=None, run_id="default", force=False):
    """Returns (n_checked, n_promoted). Reads/writes failed_tables.csv and
    promoted_tables.csv in `results_dir`."""
    failed_path = os.path.join(results_dir, "failed_tables.csv")
    promoted_path = os.path.join(results_dir, "promoted_tables.csv")

    if not os.path.exists(failed_path):
        print(f"no failed_tables.csv at {failed_path} — nothing to recheck")
        return 0, 0

    pdf_path = pdf_path or _infer_pdf(results_dir)
    if not os.path.exists(pdf_path):
        print(f"source pdf not found ({pdf_path}) — leaving quarantine untouched; pass --pdf")
        return 0, 0

    failed_df = pd.read_csv(failed_path)
    if failed_df.empty:
        print("failed_tables.csv is empty — nothing to recheck")
        return 0, 0
    for col in FAILED_COLUMNS:
        if col not in failed_df.columns:
            failed_df[col] = pd.NA

    if force:
        mask = pd.Series(True, index=failed_df.index)
    else:
        mask = failed_df["last_rechecked"].fillna("") != run_id

    # Loop Spec 4 page-scoping: this run only ever touches ONE pdf_path (see
    # signature above), so prefetch a single extract_tables() call scoped to
    # the union of pages actually being rechecked this run — never the whole
    # document. recheck_one()'s own cache-miss fallback (unused in this path,
    # since we populate the cache here first) still exists for direct callers.
    tables_cache = {}
    pages_to_check = sorted({int(row["page"]) for idx, row in failed_df.iterrows() if mask[idx]})
    if pages_to_check:
        from backend.app.extract.table_extractor import extract_tables
        tables_cache[pdf_path] = extract_tables(pdf_path, pages=pages_to_check)

    promoted_records = []
    updated_rows = []

    for idx, row in failed_df.iterrows():
        if not mask[idx]:
            updated_rows.append(row.to_dict())
            continue

        result = recheck_one(pdf_path, int(row["page"]), tables_cache, page_scope=pages_to_check)

        if result is None:
            updated_rows.append({
                **row.to_dict(),
                "reason": "no_table_on_page",
                "last_rechecked": run_id,
            })
        elif result["passed"]:
            promoted_records.append({
                "table": row["table"],
                "page": row["page"],
                "name": result.get("name"),
                "reason": "passed_on_recheck",
                "score": result.get("best_score"),
                "strategies_tried": result.get("strategies_tried"),
                "run_id": run_id,
            })
            # dropped from failed_tables.csv — promoted rows are not carried over
        else:
            updated_rows.append({
                **row.to_dict(),
                "reason": result.get("reason"),
                "best_score": result.get("best_score"),
                "strategies_tried": result.get("strategies_tried"),
                "last_rechecked": run_id,
            })

    pd.DataFrame(updated_rows, columns=FAILED_COLUMNS).to_csv(failed_path, index=False)

    if promoted_records:
        new_promoted = pd.DataFrame(promoted_records, columns=PROMOTED_COLUMNS)
        if os.path.exists(promoted_path):
            existing = pd.read_csv(promoted_path)
            new_promoted = pd.concat([existing, new_promoted], ignore_index=True)
            new_promoted = new_promoted.drop_duplicates(subset=["table", "page"], keep="last")
        new_promoted.to_csv(promoted_path, index=False)

    n_checked = int(mask.sum())
    print(f"rechecked {n_checked} row(s): {len(promoted_records)} promoted, "
          f"{n_checked - len(promoted_records)} re-quarantined "
          f"({len(failed_df) - n_checked} skipped, already checked at run-id={run_id!r})")
    return n_checked, len(promoted_records)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="backend/data/exports",
                     help="results dir with failed_tables.csv (default: backend/data/exports)")
    ap.add_argument("--pdf", default=None, help="source PDF; inferred from --dir if omitted")
    ap.add_argument("--run-id", default="default",
                     help="marks rows as rechecked for this id so repeat runs skip them")
    ap.add_argument("--force", action="store_true", help="re-check every row regardless of last_rechecked")
    args = ap.parse_args()

    results_dir = args.dir if os.path.isabs(args.dir) else os.path.join(ROOT, args.dir)
    run(results_dir, pdf_path=args.pdf, run_id=args.run_id, force=args.force)


if __name__ == "__main__":
    main()
