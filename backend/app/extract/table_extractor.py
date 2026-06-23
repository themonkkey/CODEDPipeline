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


class InvalidPDFError(ValueError):
    """Raised when the input file is not a valid PDF."""


def _check_pdf_magic(pdf_path):
    """Raise InvalidPDFError if the file does not start with the PDF magic bytes."""
    try:
        with open(pdf_path, "rb") as f:
            header = f.read(1024)
    except OSError as e:
        raise InvalidPDFError(f"Cannot read file: {pdf_path} — {e}") from e

    if not header.startswith(b"%PDF-"):
        # Detect common mis-downloads
        if header[:15].lower().startswith(b"<!doctype html") or b"<html" in header[:200].lower():
            raise InvalidPDFError(
                f"invalid_source:bot_redirect — file is an HTML page, not a PDF: {pdf_path}"
            )
        raise InvalidPDFError(
            f"invalid_source — file does not begin with %PDF-: {pdf_path}"
        )


_INDEX_ONLY = re.compile(r"^\(?\d{1,2}\)?$")
_HEADER_TITLE_LINE = re.compile(
    r"\b(table|tabel|statement|annexure|appendix)\b\s*\d", re.IGNORECASE
)
_HEADER_UNIT_LINE = re.compile(
    r"₹|crore|per cent|\blakh\b|million|billion|base\s*[:y]|end-march|end-",
    re.IGNORECASE,
)


def _header_is_missing(df):
    """
    True when camelot's stream flavor failed to capture the column header.

    Two cases qualify:
      (1) the rows above the first data row are empty / index-only ("1 2 3"), or
      (2) they are sparse title/section lines — long prose in one cell with the
          other columns blank (e.g. NCRB "Chapter-2D | Kidnapping & Abduction
          (Metropolitan…)") — rather than a well-formed column-header row.

    A *well-formed* header row labels at least half the columns with SHORT
    (<=3 word) cells; when one exists camelot DID capture the header and we must
    NOT prepend a second one. Recovery itself is conservative (it only prepends
    when the band above buckets confidently into the columns), so a relaxed gate
    that occasionally opens on a table with no recoverable band is a no-op, not
    a corruption.
    """

    # locate the first data row: column 0 starts with a digit / year
    data_i = None
    for i in range(min(8, len(df))):
        col0 = str(df.iloc[i, 0]).strip()
        if re.match(r"^\(?\d", col0):
            data_i = i
            break

    if data_i is None:
        return False

    ncols = df.shape[1]
    for j in range(data_i):
        cells = [str(v).strip() for v in df.iloc[j].tolist()]
        labels = [
            c for c in cells
            if c and c not in ("nan", "None") and not _INDEX_ONLY.match(c)
        ]
        if not labels:
            continue
        # a real captured header labels most columns with short tokens; a
        # title/section block leaves columns blank or carries long prose.
        all_short = all(len(c.split()) <= 3 for c in labels)
        if all_short and len(labels) >= max(2, ncols * 0.5):
            return False

    return True


def _recover_stream_header(table, df, plumber_pdf):
    """
    Read the column labels that camelot's stream flavor dropped, by extracting
    words from the band just above the table and bucketing them into camelot's
    column x-ranges. Returns df with a recovered header row prepended, or the
    original df when recovery is not applicable / not confident.

    Gated on _header_is_missing so tables whose headers camelot DID capture are
    never touched.
    """

    if plumber_pdf is None:
        return df

    try:
        if not _header_is_missing(df):
            return df

        cols = getattr(table, "cols", None)
        bbox = getattr(table, "_bbox", None)
        if not cols or bbox is None:
            return df

        page = plumber_pdf.pages[int(table.page) - 1]
        height = page.height
        table_top = height - bbox[3]

        band = page.crop((
            0, max(0, table_top - 58), page.width, min(height, table_top + 16)
        ))
        words = band.extract_words()
        if not words:
            return df

        # cluster words into text lines, drop the title line and lone unit lines
        lines = {}
        for w in words:
            lines.setdefault(round(w["top"] / 3), []).append(w)

        keep = []
        for ws in lines.values():
            text = " ".join(x["text"] for x in sorted(ws, key=lambda z: z["x0"]))
            if _HEADER_TITLE_LINE.search(text):
                continue
            if _HEADER_UNIT_LINE.search(text) and len(ws) <= 3:
                continue
            keep.extend(ws)

        buckets = [[] for _ in cols]
        for w in keep:
            xm = (w["x0"] + w["x1"]) / 2
            for ci, (cx1, cx2) in enumerate(cols):
                if cx1 <= xm <= cx2:
                    buckets[ci].append((round(w["top"]), w["x0"], w["text"]))
                    break

        recovered = [
            " ".join(t for _, _, t in sorted(b)).strip() for b in buckets
        ]

        filled = sum(1 for v in recovered if v)
        if filled < max(3, len(recovered) * 0.5):
            return df

        if len(recovered) != df.shape[1]:
            return df

        import pandas as pd

        head = pd.DataFrame([recovered], columns=df.columns)
        return pd.concat([head, df], ignore_index=True)

    except Exception:
        return df


def extract_tables(pdf_path):
    import os
    _check_pdf_magic(pdf_path)

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

                # Recover headers camelot's stream flavor dropped: when the top
                # rows are empty (only a "1 2 3" index band survives), read the
                # column labels positionally from pdfplumber and prepend them.
                df = _recover_stream_header(table, df, plumber_pdf)

                kept.append({
                    "page": page,
                    "dataframe": df,
                    "bbox": getattr(table, "_bbox", None),
                    "flavor": "stream",
                })

    # Expand side-by-side independent tables (two narrow tables printed next to
    # each other, extracted as one wide frame) into separate tables. Done here,
    # before table_ids are assigned, so every sub-table gets a unique sequential
    # id and nothing downstream needs to change.
    from backend.app.cleaning.panel_splitter import split_side_by_side

    expanded = []
    for t in kept:
        panels = split_side_by_side(t["dataframe"])
        if len(panels) == 1:
            expanded.append(t)
        else:
            for panel in panels:
                expanded.append({**t, "dataframe": panel})
    kept = expanded

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
