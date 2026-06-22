"""ML-based table extractor using Docling (IBM).

Enabled via: DOCLING_ENABLED=1 (env var). Falls back to Camelot when unset.
Too heavy for Streamlit Cloud free tier (~1 GB RAM limit); intended for local use.

Output shape is identical to table_extractor.extract_tables():
    {"table_id": int, "page": int, "dataframe": DataFrame, "caption": str|None, "flavor": str}
"""
import re

import pandas as pd
import pdfplumber

from backend.app.extract.table_extractor import _extract_caption

_NFHS_GROUP = re.compile(r"nfhs[_\-\s]*(\d+)", re.IGNORECASE)
_NFHS_SUB = re.compile(
    r"\.(urban|rural|total)\b"   # dot-prefixed: "NFHS-6 (2023-24).Urban"
    r"|^(urban|rural|total)$",   # bare label: "Total" with no NFHS prefix
    re.IGNORECASE,
)
_INDICATOR = re.compile(r"^indicator", re.IGNORECASE)

_converter = None  # module-level singleton — avoid reloading models per call


def _get_converter():
    global _converter
    if _converter is None:
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.pipeline_options import PdfPipelineOptions

        opts = PdfPipelineOptions()
        opts.do_table_structure = True
        opts.table_structure_options.do_cell_matching = True
        _converter = DocumentConverter(
            format_options={"pdf": PdfFormatOption(pipeline_options=opts)}
        )
    return _converter


def _rename_nfhs_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Convert Docling-flattened NFHS headers to our canonical schema.

    E.g. "NFHS-6 (2023-24).Urban" → "nfhs6_urban", "Total" (bare) → "nfhs6_total".
    """
    new = []
    last_g = None
    for c in df.columns:
        s = str(c).strip()
        if _INDICATOR.match(s):
            new.append("indicator")
            continue
        gm = _NFHS_GROUP.search(s)
        sm = _NFHS_SUB.search(s)
        if gm:
            last_g = gm.group(1)
        g = last_g or "6"
        if sm:
            sub = (sm.group(1) or sm.group(2)).lower()
            new.append(f"nfhs{g}_{sub}")
        else:
            new.append("indicator" if not s or s.lower() in ("", "nan") else s)
    df = df.copy()
    df.columns = new
    return df


def _is_nfhs_table(df: pd.DataFrame) -> bool:
    return any(_NFHS_GROUP.search(str(c)) for c in df.columns)


def _docling_bbox(table, page_height: float):
    """Convert Docling BoundingBox → camelot-style (x1, y1, x2, y2) from page bottom."""
    try:
        from docling_core.types.doc import CoordOrigin
        bb = table.prov[0].bbox
        if bb.coord_origin == CoordOrigin.BOTTOMLEFT:
            # bb.t = top y from bottom (larger), bb.b = bottom y from bottom (smaller)
            return (bb.l, bb.b, bb.r, bb.t)
        else:
            # TOPLEFT: bb.t = top y from top (smaller), bb.b = bottom y from top (larger)
            return (bb.l, page_height - bb.b, bb.r, page_height - bb.t)
    except Exception:
        return None


def extract_tables_docling(pdf_path) -> list:
    """Extract tables using Docling ML pipeline.

    Returns same dict shape as extract_tables() in table_extractor.py.
    """
    converter = _get_converter()
    result = converter.convert(str(pdf_path))
    doc = result.document

    plumber_pdf = None
    try:
        plumber_pdf = pdfplumber.open(pdf_path)
    except Exception:
        pass

    out = []
    for i, table in enumerate(doc.tables):
        try:
            df = table.export_to_dataframe(doc=doc)
        except Exception:
            continue
        if df is None or df.empty:
            continue

        page_no = table.prov[0].page_no if table.prov else 1
        page_height = (
            plumber_pdf.pages[page_no - 1].height
            if plumber_pdf and page_no <= len(plumber_pdf.pages)
            else 841.89  # A4 fallback
        )

        if _is_nfhs_table(df):
            df = _rename_nfhs_cols(df)

        bbox = _docling_bbox(table, page_height)
        caption = _extract_caption(plumber_pdf, page_no, bbox) if plumber_pdf else None

        out.append({
            "table_id": i + 1,
            "page": page_no,
            "dataframe": df,
            "caption": caption,
            "flavor": "docling",
        })

    if plumber_pdf is not None:
        try:
            plumber_pdf.close()
        except Exception:
            pass

    return out
