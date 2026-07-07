"""Batch panel builder CLI — compile many same-source PDFs into a panel dataset
with a documented schema changelog.

Two stages, so the analyst can confirm the proposed table groups between them:

  # stage 1: extract every PDF + propose panel groups
  python backend/tools/batch_panel.py <folder> <workdir> [--workers N] [--max-pages M]
    -> writes <workdir>/groups.json   (review / edit this, then run stage 2)

  # stage 2: assemble panels from confirmed groups
  python backend/tools/batch_panel.py <folder> <workdir> --groups <workdir>/groups.json
    -> writes <workdir>/master.xlsx and <workdir>/schema_changelog.md

groups.json is a plain list of group dicts; to drop a panel, delete its entry;
to split/merge, edit the `members` lists and rerun stage 2.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.app.batch.batch_export import build_panel_workbook, write_changelog_md
from backend.app.batch.batch_extract import extract_folder
from backend.app.batch.panel_builder import build_panel
from backend.app.batch.table_signature import group_tables
from backend.tools.batch_quality_gate import summary_line, write_report


def stage_extract(folder, workdir, workers, max_pages):
    manifests = extract_folder(folder, workdir, workers=workers, max_pages=max_pages)
    with open(os.path.join(workdir, "manifests.json"), "w") as f:
        json.dump(manifests, f, indent=1)
    groups = group_tables(manifests)
    groups_path = os.path.join(workdir, "groups.json")
    with open(groups_path, "w") as f:
        json.dump(groups, f, indent=1)
    n_auto = sum(1 for g in groups if g["confidence"] == "auto")
    print(f"\n{len(groups)} proposed panel groups ({n_auto} auto, "
          f"{len(groups) - n_auto} need review) -> {groups_path}")
    for g in groups[:25]:
        tag = "auto" if g["confidence"] == "auto" else "REVIEW"
        print(f"  [{g['n_periods']}p x {g['n_members']}t] ({tag}) {g['label'][:70]}")
    if len(groups) > 25:
        print(f"  ... and {len(groups) - 25} more")
    print("\nReview groups.json, then rerun with --groups to assemble panels.")


def stage_assemble(workdir, groups_path):
    with open(groups_path) as f:
        groups = json.load(f)
    panels = []
    for g in groups:
        panel, d = build_panel(g, workdir)
        d["signature"] = g["signature"]
        d["label"] = g["label"]
        panels.append({"signature": g["signature"], "label": g["label"],
                       "panel": panel, "diff": d})
    xlsx = build_panel_workbook(panels)
    xlsx_path = os.path.join(workdir, "master.xlsx")
    with open(xlsx_path, "wb") as f:
        f.write(xlsx.getvalue())
    md_path = write_changelog_md(panels, os.path.join(workdir, "schema_changelog.md"))
    nonempty = [p for p in panels if p["panel"] is not None and p["panel"].shape[0]]
    n_changes = sum(len(p["diff"]["changes"]) for p in panels)
    print(f"assembled {len(nonempty)} panels, {n_changes} schema changes")
    print(f"  workbook  -> {xlsx_path}")
    print(f"  changelog -> {md_path}")

    # Loop Spec 3: quality gate — warn, never block, on the way to Step 4.
    report, report_path = write_report(panels, workdir)
    print(f"  {summary_line(report)}")
    print(f"  quality report -> {report_path}")


def main():
    ap = argparse.ArgumentParser(description="Data Rubiks batch panel builder")
    ap.add_argument("folder", help="folder of same-source PDFs")
    ap.add_argument("workdir", help="output / scratch directory")
    ap.add_argument("--groups", help="confirmed groups.json -> stage 2 (assemble)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-pages", type=int, default=None)
    args = ap.parse_args()

    os.makedirs(args.workdir, exist_ok=True)
    if args.groups:
        stage_assemble(args.workdir, args.groups)
    else:
        stage_extract(args.folder, args.workdir, args.workers, args.max_pages)


if __name__ == "__main__":
    main()
