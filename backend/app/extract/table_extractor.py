import re

import camelot

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

from backend.app.extract.quality import (
    LOW_QUALITY_THRESHOLD,
    REVIEW_THRESHOLD,
    score_table,
)


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

# a chapter / part / annexure-style GOVERNING heading (spans multiple tables),
# as opposed to a per-table "Table X.Y" title
_SECTION_HEADING = re.compile(r"^(chapter|section|part|annex(ure)?)\b", re.IGNORECASE)


def _is_section_heading(cap):
    cap = str(cap).strip()
    return bool(_SECTION_HEADING.match(cap) or SECTION_LINE.match(cap))

# words that mark a line as a TABLE heading rather than body prose
_HEADING_KEYWORD = re.compile(
    r"\b(distribution|number|percentage|proportion|ratio|rate|trends?|"
    r"share|estimate[ds]?|coverage|prevalence|growth|index|average|"
    r"summary|profile|indicators?|statistics?|status|composition|"
    r"classification|state[\s\-]?wise|district[\s\-]?wise|category[\s\-]?wise|"
    r"by\s+(age|sex|state|district|sector|type|group|category|level))\b",
    re.IGNORECASE,
)


def _looks_like_heading(line):
    """A descriptive (non-'Table N') title line, distinguished from body prose.

    Headings are short, carry no sentence punctuation, start with a letter, are
    mostly alphabetic, and either read as Title Case or carry a statistical
    heading keyword ('Distribution of …', 'Number of …', '… state-wise')."""
    words = line.split()
    if not (2 <= len(words) <= 16):
        return False
    if line[:1].islower() or not line[:1].isalpha():
        return False
    if re.search(r"[.;]\s|[.;]$|:\s*$", line):     # sentence-like / dangling colon
        return False
    # unbalanced closing paren: a wrapped header-cell TAIL ("Integration)
    # Integration)"), never how a heading starts
    if line.count(")") > line.count("("):
        return False
    compact = line.replace(" ", "")
    if not compact or sum(c.isalpha() for c in compact) < 0.6 * len(compact):
        return False                                # mostly digits/symbols -> a data row
    titlecase = sum(1 for w in words if w[:1].isupper()) >= max(2, len(words) * 0.5)
    return titlecase or bool(_HEADING_KEYWORD.search(line))


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

    # descriptive heading (no "Table N" prefix): the closest title-like line
    # above the table — recovers the ~half of tables whose heading is purely
    # descriptive ("Distribution of households by source of lighting").
    for i in range(len(lines) - 1, -1, -1):
        if _looks_like_heading(lines[i]):
            return lines[i][:300]

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

        # full-page border frame: on pages with an outer decorative box,
        # lattice takes the FRAME as the table boundary, so the bbox spans
        # nearly the whole page and the title line sits INSIDE it (in the
        # band between the frame top and the real grid). Nothing is "above
        # the table" then — search the top band of the bbox itself.
        bbox_height = abs(bbox[3] - bbox[1])
        if bbox_height > 0.85 * page.height:
            band_bottom = table_top + 0.2 * bbox_height
            band = page.crop((0, max(0, table_top), page.width,
                              min(page.height, band_bottom)))
            band_lines = [l.strip() for l in (band.extract_text() or "").split("\n")
                          if l.strip()]
            title = _title_from_lines(band_lines)
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
    labelled = set()
    for j in range(data_i):
        cells = [str(v).strip() for v in df.iloc[j].tolist()]
        labels = [
            (k, c) for k, c in enumerate(cells)
            if c and c not in ("nan", "None") and not _INDEX_ONLY.match(c)
        ]
        if not labels:
            continue
        # a real captured header labels most columns with short tokens; a
        # title/section block leaves columns blank or carries long prose.
        # Multi-level headers spread the labels over SEVERAL rows (group row
        # + sub-label row), so judge the UNION of short-labelled columns
        # across the whole pre-data band, not each row alone.
        if all(len(c.split()) <= 3 for _, c in labels):
            labelled.update(k for k, _ in labels)
            if len(labelled) >= max(2, ncols * 0.5):
                return False

    return True


def _recover_stream_header(table, df, plumber_pdf, pad_below=16):
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

        # pad_below reaches slightly INTO the table for stream (its bbox top
        # can sit below the header line); lattice callers pass 0 — the grid
        # boundary is exact, and padding would leak the first data row's
        # words into the recovered header.
        band = page.crop((
            0, max(0, table_top - 58), page.width, min(height, table_top + pad_below)
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


def _ocr_extract_grid(table, plumber_pdf):
    """Low-level page-render recovery shared by the corruption-triggered path
    below and Loop Spec 1's quality-triggered retry (a scanned page where
    lattice AND stream both stayed low-quality). Returns a DataFrame, or None
    on any failure / when there is nothing to render."""
    if plumber_pdf is None:
        return None
    try:
        from backend.app.extract.ocr_recovery import recover_table
        import pandas as pd
        page = plumber_pdf.pages[int(table.page) - 1]
        grid = recover_table(page, table)
        if not grid:
            return None
        return pd.DataFrame(grid)
    except Exception:
        return None


def _ocr_recover_if_corrupt(table, df, plumber_pdf):
    """When a table's text layer is font-corrupted (Kruti soup / mangled
    Devanagari), rebuild it by OCR-ing the rendered glyphs. Returns
    (dataframe, recovered?). Best-effort: any failure keeps the original df."""
    try:
        from backend.app.translation.corruption import corruption_score
        before, kind = corruption_score(df)
        if before < 0.3:
            return df, False
        # Unicode Devanagari (kind="deva") is handled by translate_dataframe;
        # only Kruti-Dev ASCII soup (kind="kruti") cannot be translated and
        # genuinely needs OCR. Bilingual tables with Devanagari row labels
        # (e.g. PLFS) have kind="deva" and must NOT be OCR'd.
        if kind != "kruti":
            return df, False
        rdf = _ocr_extract_grid(table, plumber_pdf)
        if rdf is None:
            return df, False
        after, _ = corruption_score(rdf)
        if after < before:
            return rdf, True
    except Exception:
        pass
    return df, False


def _process_stream_table(table, plumber_pdf):
    """Validate + repair one camelot stream table. Shared by the blanket
    stream fallback (pages where lattice found nothing at all) and Loop
    Spec 1's per-table stream retry (a page where lattice DID find a table
    but it scored low). Returns (df, ocr_recovered?) or None when the table
    fails the density/confidence gates that keep stream from picking up
    pseudo-tables on prose pages."""
    try:
        df = table.df
        report = table.parsing_report
        accuracy = report.get("accuracy", 0)
        whitespace = report.get("whitespace", 100)
    except Exception:
        return None

    if accuracy < 80 or whitespace > 60:
        return None

    if len(df) < 4 or len(df.columns) < 3:
        return None

    # Recover headers camelot's stream flavor dropped: when the top rows are
    # empty (only a "1 2 3" index band survives), read the column labels
    # positionally from pdfplumber and prepend them.
    df = _recover_stream_header(table, df, plumber_pdf)
    df, ocr = _ocr_recover_if_corrupt(table, df, plumber_pdf)
    return df, ocr


def _stream_retry(pdf_path, page, plumber_pdf):
    """Loop Spec 1, strategy 2: re-read a single page with stream flavor when
    lattice succeeded but scored below LOW_QUALITY_THRESHOLD. A page can hold
    more than one stream table; keep the best-scoring one so the retry can
    never do worse than picking nothing. Returns (df, score, ocr?) or None."""
    best = None
    for table in _read(pdf_path, str(page), "stream"):
        result = _process_stream_table(table, plumber_pdf)
        if result is None:
            continue
        df, ocr = result
        score = score_table(df)["score"]
        if best is None or score > best[1]:
            best = (df, score, ocr)
    return best


def _consider(attempts, best, candidate_df, candidate_score, candidate_strategy):
    """Record one extraction attempt and apply Loop Spec 1's no-progress
    rule: a candidate only becomes the new best when it STRICTLY beats the
    best score seen so far, so a worse (or tied) retry can never replace a
    better earlier attempt. `best` is {"df", "score", "strategy"}; returns
    the (possibly unchanged) best. Pure / deterministic — no I/O — so the
    max-score guarantee is directly unit-testable."""
    attempts.append({"strategy": candidate_strategy, "score": candidate_score})
    if candidate_score > best["score"]:
        return {"df": candidate_df, "score": candidate_score, "strategy": candidate_strategy}
    return best


def _build_kept_item(page, df, bbox, attempts, best_score, best_strategy):
    """Loop Spec 1's kept-table record. A table is NEVER dropped for scoring
    low: one that stayed under LOW_QUALITY_THRESHOLD after every strategy was
    tried is still kept here, flagged low_quality (and review_needed if it
    never even reached REVIEW_THRESHOLD) so a downstream consumer can
    quarantine / escalate it instead of it silently vanishing."""
    return {
        "page": page,
        "dataframe": df,
        "bbox": bbox,
        "flavor": best_strategy,
        "attempts": attempts,
        "best_score": best_score,
        "low_quality": best_score < LOW_QUALITY_THRESHOLD,
        "review_needed": best_score < REVIEW_THRESHOLD,
    }


def _normalize_page_scope(pages):
    """Loop Spec 4 page-scoping: turn the `pages` argument of extract_tables()
    into (camelot_pages_str, allowed_page_set). `pages=None` means "no scope"
    (camelot_pages_str="all", allowed=None) — the default, so every existing
    caller's behavior is byte-identical. Accepts a single page number, an
    iterable of page numbers, or an already camelot-formatted string
    ("3", "3,7"). Range tokens ("3-9") are passed through to camelot as-is,
    but conservatively fall back to allowed=None (no post-filtering) since we
    cannot cheaply enumerate a range's members here — better to over-include
    than to silently drop a page, matching Loop Spec 1's never-drop ethos."""
    if pages is None:
        return "all", None
    if isinstance(pages, int):
        pages = [pages]
    if isinstance(pages, str):
        tokens = [t.strip() for t in pages.split(",") if t.strip()]
        allowed, all_simple = set(), True
        for t in tokens:
            if t.isdigit():
                allowed.add(int(t))
            else:
                all_simple = False
        return pages, (allowed if all_simple else None)
    ints = sorted({int(p) for p in pages})
    return ",".join(str(p) for p in ints), set(ints)


def extract_tables(pdf_path, pages=None):
    """
    pages: optional page scope (Loop Spec 4). None (default) processes the
    whole document — every pre-existing caller passes only pdf_path, so this
    is fully backward compatible. Pass a single page number, an iterable of
    page numbers, or a camelot-formatted page string to restrict BOTH the
    lattice pass and the stream fallback to just those pages. Used by
    recheck_quarantine.py so re-verifying a handful of quarantined pages does
    not re-run camelot over an entire multi-hundred-page document.
    """
    import os
    _check_pdf_magic(pdf_path)

    pages_spec, allowed_pages = _normalize_page_scope(pages)

    if os.getenv("DOCLING_ENABLED", "").lower() in ("1", "true", "yes"):
        from backend.app.extract.docling_extractor import extract_tables_docling
        results = extract_tables_docling(pdf_path)
        if allowed_pages is not None:
            results = [r for r in results if r.get("page") in allowed_pages]
        return results

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

    # Loop Spec 1, strategy 3 (OCR) is only worth trying on pages the scan
    # detector actually flags as image-only; computing that is a full-PDF
    # pass, so do it at most once and only when a table first needs it.
    _scan_cache = {}

    def _is_scanned_page(page):
        if "pages" not in _scan_cache:
            try:
                from backend.app.extract.scan_detector import analyze_pdf
                info = analyze_pdf(pdf_path)
                _scan_cache["pages"] = set(info.get("scanned_page_numbers") or [])
            except Exception:
                _scan_cache["pages"] = set()
        return page in _scan_cache["pages"]

    for table in _read(pdf_path, pages_spec, "lattice"):

        try:
            page = int(table.page)
            raw_df = table.df
        except Exception:
            continue

        if _is_crushed(raw_df):
            continue

        good_lattice_pages.add(page)
        # decide BEFORE the positional repair below — it mutates table.df
        # (raw_df aliases it), and a spread title row reads as headerless
        raw_header_missing = _header_is_missing(raw_df)
        df = _repair_header_positionally(table, plumber_pdf)
        df = _repair_crushed_header_rows(df)
        # lattice can also miss the header: when the ruled grid starts at the
        # first DATA row and the multi-line header band sits above it unruled
        # (DARPG annexures), the same positional recovery used for stream
        # applies — it is gated on _header_is_missing, so tables whose grid
        # captured the header are never touched.
        if raw_header_missing:
            df = _recover_stream_header(table, df, plumber_pdf, pad_below=0)
        df, ocr = _ocr_recover_if_corrupt(table, df, plumber_pdf)

        # extract-verify-re-extract: lattice "succeeding" only means it found
        # SOME grid, not a good one (garbage col/col_2 columns still score
        # low). Retry with other strategies, capped at 3 attempts total, and
        # only ever keep the max-scoring variant so a worse retry can never
        # replace a better earlier attempt.
        attempts = []
        lattice_strategy = "ocr" if ocr else "lattice"
        best = {"df": df, "score": score_table(df)["score"], "strategy": lattice_strategy}
        attempts.append({"strategy": lattice_strategy, "score": best["score"]})

        if best["score"] < LOW_QUALITY_THRESHOLD and len(attempts) < 3:
            retry = _stream_retry(pdf_path, page, plumber_pdf)
            if retry is not None:
                stream_df, stream_score, stream_ocr = retry
                stream_strategy = "ocr" if stream_ocr else "stream"
                best = _consider(attempts, best, stream_df, stream_score, stream_strategy)

        if best["score"] < LOW_QUALITY_THRESHOLD and len(attempts) < 3 and _is_scanned_page(page):
            ocr_df = _ocr_extract_grid(table, plumber_pdf)
            if ocr_df is not None:
                ocr_score = score_table(ocr_df)["score"]
                best = _consider(attempts, best, ocr_df, ocr_score, "ocr")

        kept.append(_build_kept_item(
            page, best["df"], getattr(table, "_bbox", None),
            attempts, best["score"], best["strategy"],
        ))

    # Stream fallback: pages where lattice found nothing usable
    # (borderless tables, or tables without row separator lines). This is a
    # single-strategy attempt (no lattice score to compare against), so it
    # is recorded the same way a lone attempt always is.
    #
    # Page scope (Loop Spec 4): when the caller restricted extract_tables()
    # to specific pages, the fallback must only consider THOSE pages, never
    # the whole document — otherwise a scoped call would still pay for a
    # full-document stream pass, defeating the point of scoping.
    if total_pages is not None:

        candidate_pages = (
            range(1, total_pages + 1) if allowed_pages is None else sorted(allowed_pages)
        )
        missing = [
            str(p)
            for p in candidate_pages
            if p not in good_lattice_pages
        ]

        for chunk_start in range(0, len(missing), 40):

            chunk_pages = missing[chunk_start:chunk_start + 40]

            for table in _read_resilient(pdf_path, chunk_pages, "stream"):

                try:
                    page = int(table.page)
                except Exception:
                    continue

                result = _process_stream_table(table, plumber_pdf)
                if result is None:
                    continue

                stream_df, ocr = result
                stream_strategy = "ocr" if ocr else "stream"
                attempts = []
                best = {"df": stream_df, "score": score_table(stream_df)["score"], "strategy": stream_strategy}
                attempts.append({"strategy": stream_strategy, "score": best["score"]})

                # lattice already ran (the "all pages, lattice" pass at the
                # top of this function) and found nothing keepable on this
                # page, so it counts as a spent attempt; stream is attempt 2.
                # A scanned page with a still-low stream score gets the 3rd.
                if (
                    best["score"] < LOW_QUALITY_THRESHOLD
                    and len(attempts) < 3
                    and _is_scanned_page(page)
                ):
                    ocr_df = _ocr_extract_grid(table, plumber_pdf)
                    if ocr_df is not None:
                        ocr_score = score_table(ocr_df)["score"]
                        best = _consider(attempts, best, ocr_df, ocr_score, "ocr")

                kept.append(_build_kept_item(
                    page, best["df"], getattr(table, "_bbox", None),
                    attempts, best["score"], best["strategy"],
                ))

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
            # Loop Spec 1 item flags (same convention as table_profiler's
            # "archetype": extra per-table metadata threaded alongside the
            # dataframe, not a parallel structure keyed by table_id).
            "attempts": t.get("attempts", [{"strategy": t["flavor"], "score": None}]),
            "best_score": t.get("best_score"),
            "low_quality": t.get("low_quality", False),
            "review_needed": t.get("review_needed", False),
        })

    # Chapter/section carry-forward: a governing heading ("Chapter-2C Kidnapping
    # & Abduction", "2.4 Ranking of …") titles the untitled tables printed below
    # it until the next heading. Only fills EMPTY captions and only carries a
    # section-level heading — never a specific "Table X.Y" title, which is
    # per-table — so a neighbour is never given the wrong table's name.
    last_section = None
    last_section_page = None
    for t in results:
        cap = t["caption"]
        if cap and _is_section_heading(cap):
            last_section, last_section_page = cap, t["page"]
        elif (
            not cap
            and last_section
            and t["page"] - last_section_page <= 5
        ):
            t["caption"] = last_section

    if plumber_pdf is not None:
        try:
            plumber_pdf.close()
        except Exception:
            pass

    return results
