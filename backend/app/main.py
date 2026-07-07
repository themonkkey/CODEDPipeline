import io
import os
import threading
import uuid
import warnings
import zipfile
from pathlib import Path

import pandas as pd
from fastapi import BackgroundTasks, FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

warnings.filterwarnings("ignore")

app = FastAPI(title="CODEDPipeline API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

JOBS: dict = {}

UPLOAD_DIR = Path("backend/data/uploads")
JOBS_DIR = Path("backend/data/jobs")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DIR.mkdir(parents=True, exist_ok=True)


def run_pipeline(job_id: str, pdf_path: str):
    from backend.app.cleaning.header_builder import apply_headers
    from backend.app.cleaning.header_detector import detect_header_rows
    from backend.app.cleaning.header_postprocessor import clean_headers
    from backend.app.cleaning.universal_cleaner import clean_dataframe
    from backend.app.cleaning.wrapped_row_reassembler import reassemble_wrapped_rows, merge_continuation_values
    from backend.app.cleaning.panel_splitter import split_panels
    from backend.app.cleaning.section_lifter import lift_section_rows
    from backend.app.cleaning.numeric_normalizer import normalize_numeric_columns
    from backend.app.extract.table_extractor import extract_tables, InvalidPDFError
    from backend.app.standardization.metadata_builder import build_metadata
    from backend.app.translation.hindi_translator import translate_dataframe, translate_text
    from backend.app.standardization.table_name_extractor import extract_table_name
    from backend.app.standardization.table_stitcher import stitch_tables
    from backend.app.validation.table_validator import validate_table

    results_dir = JOBS_DIR / job_id
    csv_dir = results_dir / "csv"
    csv_dir.mkdir(parents=True, exist_ok=True)

    JOBS[job_id]["status"] = "processing"

    try:
        tables = extract_tables(pdf_path)
    except InvalidPDFError as e:
        JOBS[job_id]["status"] = "invalid_source"
        JOBS[job_id]["error"] = str(e)
        return
    except Exception as e:
        JOBS[job_id]["status"] = "failed"
        JOBS[job_id]["error"] = str(e)
        return

    JOBS[job_id]["total"] = len(tables)
    passed, failed = [], []

    for table in tables:
        try:
            df = clean_dataframe(table["dataframe"])
            df = split_panels(df)
            df = reassemble_wrapped_rows(df)
            df = translate_dataframe(df)
            h = detect_header_rows(df)
            table_name = extract_table_name(
                df, h, translate_text(table.get("caption") or "") or None
            )
            df = apply_headers(df, h)
            df = clean_headers(df)
            df = merge_continuation_values(df)
            df = lift_section_rows(df)
            df = normalize_numeric_columns(df)
            status = validate_table(df)

            if status["passed"]:
                passed.append({
                    "table_id": table["table_id"],
                    "name": table_name,
                    "page": table["page"],
                    "df": df,
                })
            else:
                failed.append(
                    {
                        "table": table["table_id"],
                        "page": table["page"],
                        "reason": status["reason"],
                        # Loop Spec 1: extract_tables()'s own retry record —
                        # a table dropped here (validate_table failure) was
                        # never silently given up on at the extraction layer;
                        # this shows what was tried and the best it scored.
                        "best_score": table.get("best_score"),
                        "strategies_tried": ",".join(
                            a["strategy"] for a in table.get("attempts", [])
                        ),
                    }
                )
        except Exception as e:
            failed.append(
                {
                    "table": table["table_id"],
                    "page": table["page"],
                    "reason": str(e),
                    "best_score": table.get("best_score"),
                    "strategies_tried": ",".join(
                        a["strategy"] for a in table.get("attempts", [])
                    ),
                }
            )

        JOBS[job_id]["progress"] = JOBS[job_id].get("progress", 0) + 1

    # merge multi-page continuation fragments, then name the rest
    passed = stitch_tables(passed)

    catalog = []
    table_dfs = {}
    unnamed_seq = 0

    for it in passed:
        name = it["name"]
        if not name:
            unnamed_seq += 1
            name = f"Table {unnamed_seq} (p.{it['page']})"
        if len(it["pages"]) > 1:
            name += f" (pp. {it['pages'][0]}–{it['pages'][-1]})"
        catalog.append(
            build_metadata(it["table_id"], name, it["page"], it["df"])
        )
        table_dfs[it["table_id"]] = it["df"]
        it["df"].to_csv(csv_dir / f"table_{it['table_id']}.csv", index=False)

    pd.DataFrame(catalog).to_csv(results_dir / "table_catalog.csv", index=False)
    pd.DataFrame(failed).to_csv(results_dir / "failed_tables.csv", index=False)

    # single navigable workbook: Contents (TOC) tab + one sheet per table
    try:
        from backend.app.export.excel_exporter import build_workbook
        with open(results_dir / "workbook.xlsx", "wb") as f:
            f.write(build_workbook(table_dfs, catalog).getbuffer())
    except Exception as e:
        JOBS[job_id]["workbook_error"] = str(e)

    JOBS[job_id]["status"] = "done"
    JOBS[job_id]["catalog"] = catalog
    JOBS[job_id]["failed"] = failed


@app.post("/api/process")
async def process_pdf(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    job_id = str(uuid.uuid4())
    upload_path = str(UPLOAD_DIR / f"{job_id}_{file.filename}")

    content = await file.read()
    with open(upload_path, "wb") as f:
        f.write(content)

    JOBS[job_id] = {"status": "queued", "progress": 0, "total": 0}
    background_tasks.add_task(run_pipeline, job_id, upload_path)

    return {"job_id": job_id}


@app.get("/api/status/{job_id}")
def get_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return {"status": "not_found"}
    return {
        "status": job["status"],
        "progress": job.get("progress", 0),
        "total": job.get("total", 0),
    }


@app.get("/api/results/{job_id}")
def get_results(job_id: str):
    job = JOBS.get(job_id)
    if not job or job["status"] != "done":
        return {"error": "not ready"}
    return {"catalog": job.get("catalog", []), "failed": job.get("failed", [])}


@app.get("/api/download/{job_id}/all")
def download_all(job_id: str):
    results_dir = JOBS_DIR / job_id
    if not results_dir.exists():
        return {"error": "not found"}

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for csv in (results_dir / "csv").glob("*.csv"):
            zf.write(csv, csv.name)
        catalog = results_dir / "table_catalog.csv"
        if catalog.exists():
            zf.write(catalog, "table_catalog.csv")
        workbook = results_dir / "workbook.xlsx"
        if workbook.exists():
            zf.write(workbook, "workbook.xlsx")
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=results_{job_id[:8]}.zip"
        },
    )


@app.get("/api/download/{job_id}/workbook.xlsx")
def download_workbook(job_id: str):
    path = JOBS_DIR / job_id / "workbook.xlsx"
    if not path.exists():
        return {"error": "not found"}
    return FileResponse(
        str(path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"tables_{job_id[:8]}.xlsx",
    )


@app.get("/api/download/{job_id}/{table_id}")
def download_table(job_id: str, table_id: int):
    path = JOBS_DIR / job_id / "csv" / f"table_{table_id}.csv"
    if not path.exists():
        return {"error": "not found"}
    return FileResponse(str(path), filename=f"table_{table_id}.csv")
