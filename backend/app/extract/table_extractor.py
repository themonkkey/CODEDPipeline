import re

import camelot

try:
    import pdfplumber
except ImportError:
    pdfplumber = None


def _is_crushed(df):
    """
    Lattice on tables without horizontal row separators merges many
    rows into one cell. Detect: a meaningful share of cells holding
    multi-line content.
    """

    if df.empty:
        return True

    cells = df.astype(str).values.flatten()

    if len(cells) == 0:
        return True

    multiline = sum(1 for c in cells if c.count("\n") >= 2)

    if multiline / len(cells) > 0.25:
        return True

    # a single mega-row can hide a whole table: one cell holding many
    # newline-separated numbers IS the column, crushed (Annual Report
    # GVA statements: 4 camelot rows, data row = '26,97,294\n23,67,287\n...')
    for c in cells:
        frags = [f.strip() for f in c.split("\n") if f.strip()]
        if len(frags) >= 4:
            numeric = sum(bool(NUMERIC_FRAGMENT.match(f)) for f in frags)
            if numeric / len(frags) >= 0.7:
                return True

    return False


NUMERIC_FRAGMENT = re.compile(r"^-?[\d,]+(\.\d+)?%?$")


def _repair_crushed_header_rows(df):
    """
    Camelot sometimes crams an entire header row into one cell
    ("S. No.\nName of Ministry\nReceipts\n...") leaving the other
    cells empty — the row had no vertical separators. Split the
    fragments and distribute them across the columns so headers
    survive cleaning.
    """

    ncols = df.shape[1]

    for i in range(min(8, len(df))):

        row = [str(v).strip() for v in df.iloc[i].tolist()]
        non_empty = [(j, v) for j, v in enumerate(row) if v]

        if len(non_empty) != 1:
            continue

        col, value = non_empty[0]
        fragments = [f.strip() for f in value.split("\n") if f.strip()]

        if len(fragments) < 3:
            continue

        # headers are text; a crushed DATA column is mostly numbers
        numeric = sum(1 for f in fragments if NUMERIC_FRAGMENT.match(f))

        if numeric / len(fragments) > 0.3:
            continue

        slots = ncols - col

        if slots < 2:
            continue

        placed = fragments[:slots - 1]
        placed.append(" ".join(fragments[slots - 1:]))

        for k, frag in enumerate(placed):
            df.iat[i, col + k] = frag

    return df


TITLE_LINE = re.compile(
    r"^(table|tabel|statement|annexure|appendix)\s*[\(\-:.]?\s*\d",
    re.IGNORECASE,
)


def _repair_header_positionally(table, plumber_pdf):
    """
    When a header row collapses into ONE cell (no vertical separators in
    the PDF), the fragments arrive in scrambled visual order, so blind
    splitting misplaces them. Rebuild such rows by reading the words'
    x-positions with pdfplumber and bucketing them into camelot's
    column boundaries.
    """

    df = table.df

    if plumber_pdf is None:
        return df

    try:
        page = plumber_pdf.pages[int(table.page) - 1]
        height = page.height

        for i in range(min(4, len(df))):

            row = [str(v).strip() for v in df.iloc[i].tolist()]
            non_empty = [v for v in row if v]

            if len(non_empty) != 1 or len(non_empty[0].split()) < 3:
                continue

            cells = table.cells[i]

            top = height - max(c.y2 for c in cells)
            bottom = height - min(c.y1 for c in cells)

            region = page.crop((
                0, max(0, top - 1), page.width, min(height, bottom + 1)
            ))
            words = region.extract_words()

            bounds = [(c.x1, c.x2) for c in cells]
            buckets = [[] for _ in bounds]

            for w in words:
                xm = (w["x0"] + w["x1"]) / 2
                for j, (x1, x2) in enumerate(bounds):
                    if x1 <= xm <= x2:
                        buckets[j].append((round(w["top"]), w["x0"], w["text"]))
                        break

            rebuilt = [
                " ".join(t for _, _, t in sorted(b)) for b in buckets
            ]

            if sum(1 for v in rebuilt if v) >= 3:
                for j, v in enumerate(rebuilt):
                    if j < df.shape[1]:
                        df.iat[i, j] = v

    except Exception:
        pass

    return df


# numbered section heading: "2.4 Ranking of Ministries/Departments – Group A"
SECTION_LINE = re.compile(r"^\d{1,2}(\.\d{1,2})+[\s\-–:]+[A-Z]")


def _title_from_lines(lines):
    """Best title candidate from text lines above a table, or None."""

    # explicit title line, closest one to the table wins;
    # absorb a wrapped continuation line (starts lowercase)
    for i in range(len(lines) - 1, -1, -1):

        if TITLE_LINE.match(lines[i]):

            title = lines[i]

            if (
                i + 1 < len(lines)
                and lines[i + 1][:1].islower()
                and not TITLE_LINE.match(lines[i + 1])
            ):
                title += " " + lines[i + 1]

            return title[:300]

    # numbered section heading ("2.4 Ranking of ..."): headings are
    # short; prose paragraphs are not. Absorb a following parenthetical
    # qualifier line ("(Ministries/Departments with ...)").
    for i in range(len(lines) - 1, -1, -1):

        line = lines[i]

        if SECTION_LINE.match(line) and len(line) < 90:

            if i + 1 < len(lines) and lines[i + 1].startswith("("):
                line += " " + lines[i + 1]

            return line[:300]

    return None


def _extract_caption(plumber_pdf, page_num, bbox):
    """
    Find the table's title. Preference order: an explicit
    "Table/Statement/Annexure N ..." line above the table, then a
    numbered section heading ("2.4 Ranking of ..."), then — when the
    table starts at the very top of its page — the same search over
    the bottom of the PREVIOUS page. Fallback: the lines printed just
    above the table.
    """

    if plumber_pdf is None or bbox is None:
        return None

    try:
        page = plumber_pdf.pages[page_num - 1]

        # camelot bbox is (x1, y1, x2, y2) in PDF coords (y from bottom);
        # pdfplumber uses top-down coords.
        table_top = page.height - max(bbox[1], bbox[3])

        lines = []

        if table_top > 0:
            region = page.crop((0, 0, page.width, table_top))
            text = region.extract_text() or ""
            lines = [l.strip() for l in text.split("\n") if l.strip()]

        title = _title_from_lines(lines)

        if title:
            return title

        # table starts at the very top of its page: the heading may sit
        # at the bottom of the previous page
        if table_top < 0.15 * page.height and page_num >= 2:

            prev = plumber_pdf.pages[page_num - 2]
            band = prev.crop((
                0, prev.height * 0.7, prev.width, prev.height
            ))
            prev_lines = [
                l.strip()
                for l in (band.extract_text() or "").split("\n")
                if l.strip()
            ]

            title = _title_from_lines(prev_lines)

            if title:
                return title

        if not lines:
            return None

        # fallback: the line(s) printed right above the table
        return " ".join(lines[-2:])[:300]

    except Exception:
        return None


def _read(pdf_path, pages, flavor):

    try:
        return camelot.read_pdf(pdf_path, pages=pages, flavor=flavor)
    except Exception:
        return []


def _read_resilient(pdf_path, page_list, flavor):
    """
    camelot raises (e.g. "max() arg is an empty sequence") on blank or
    vector-only pages, aborting the whole multi-page call. Try the chunk
    first; on failure fall back to page-by-page so one bad page cannot
    sink its 39 neighbours.
    """

    try:
        return list(
            camelot.read_pdf(
                pdf_path, pages=",".join(page_list), flavor=flavor
            )
        )
    except Exception:
        pass

    tables = []

    for p in page_list:
        try:
            tables.extend(
                camelot.read_pdf(pdf_path, pages=p, flavor=flavor)
            )
        except Exception:
            continue

    return tables


def extract_tables(pdf_path):
    import os
    if os.getenv("DOCLING_ENABLED", "").lower() in ("1", "true", "yes"):
        from backend.app.extract.docling_extractor import extract_tables_docling
        return extract_tables_docling(pdf_path)

    plumber_pdf = None

    if pdfplumber is not None:
        try:
            plumber_pdf = pdfplumber.open(pdf_path)
        except Exception:
            plumber_pdf = None

    total_pages = None

    if plumber_pdf is not None:
        total_pages = len(plumber_pdf.pages)

    kept = []
    good_lattice_pages = set()

    for table in _read(pdf_path, "all", "lattice"):

        try:
            page = int(table.page)
            df = table.df
        except Exception:
            continue

        if _is_crushed(df):
            continue

        good_lattice_pages.add(page)
        df = _repair_header_positionally(table, plumber_pdf)
        kept.append({
            "page": page,
            "dataframe": _repair_crushed_header_rows(df),
            "bbox": getattr(table, "_bbox", None),
            "flavor": "lattice",
        })

    # Stream fallback: pages where lattice found nothing usable
    # (borderless tables, or tables without row separator lines).
    if total_pages is not None:

        missing = [
            str(p)
            for p in range(1, total_pages + 1)
            if p not in good_lattice_pages
        ]

        for chunk_start in range(0, len(missing), 40):

            chunk_pages = missing[chunk_start:chunk_start + 40]

            for table in _read_resilient(pdf_path, chunk_pages, "stream"):

                try:
                    page = int(table.page)
                    df = table.df

                    report = table.parsing_report
                    accuracy = report.get("accuracy", 0)
                    whitespace = report.get("whitespace", 100)
                except Exception:
                    continue

                # stream "finds" pseudo-tables on prose pages;
                # keep only confident, dense ones
                if accuracy < 80 or whitespace > 60:
                    continue

                if len(df) < 4 or len(df.columns) < 3:
                    continue

                kept.append({
                    "page": page,
                    "dataframe": df,
                    "bbox": getattr(table, "_bbox", None),
                    "flavor": "stream",
                })

    kept.sort(key=lambda t: t["page"])

    results = []

    for i, t in enumerate(kept):

        results.append({
            "table_id": i + 1,
            "page": t["page"],
            "dataframe": t["dataframe"],
            "caption": _extract_caption(plumber_pdf, t["page"], t["bbox"]),
            "flavor": t["flavor"],
        })

    if plumber_pdf is not None:
        try:
            plumber_pdf.close()
        except Exception:
            pass

    return results
