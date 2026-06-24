import re


MERGED_RUN = re.compile(
    r"^(-?[\d,]+(\.\d+)?%?\s+){3,}-?[\d,]+(\.\d+)?%?$"
)

# small integer: page number range 1-999 (no decimals, no commas, no %)
_PAGE_NUM = re.compile(r"^\d{1,3}$")

# honorifics found in staff/acknowledgement lists
_HONORIFIC = re.compile(
    r"\b(Shri|Smt|Dr|Prof|Sh\.|Km\.|Shrimati|Ku\.)\b",
    re.IGNORECASE,
)

# job title words from acknowledgement / supervision sections
_JOB_TITLE = re.compile(
    r"\b(Director|Officer|Assistant|Deputy|Joint|Additional|"
    r"Analyst|Statistician|Superintendent|Inspector|Registrar|"
    r"Commissioner|Secretary|Manager|Advisor|Consultant|Head)\b",
    re.IGNORECASE,
)


def _is_frontmatter(df):
    """
    Detect TOC/index pages and staff-list/acknowledgement tables.

    TOC signal: last column is mostly small integers (page numbers 1-999)
    AND at least one other column has long text (avg len > 15 chars).

    Staff list signal: a column has honorific-prefixed names AND another
    column has job-title keywords.
    """
    if len(df) < 3:
        return False

    # --- TOC detection ---
    last_col_vals = df.iloc[:, -1].astype(str).str.strip()
    page_frac = last_col_vals.map(lambda v: bool(_PAGE_NUM.match(v))).mean()

    # Last column name must be generic (col_N / col) or contain "page" —
    # a named field like "grai_rank" or "score" is real data, not a page ref.
    _NAMED_FIELD = re.compile(
        r"(rank|score|rate|percent|ratio|count|amount|value|"
        r"total|number|year|period|status|level|index)",
        re.IGNORECASE,
    )
    last_col_name = str(df.columns[-1]).lower()
    is_page_col_name = (
        re.match(r"^col_?\d*$", last_col_name)
        or "page" in last_col_name
        or not _NAMED_FIELD.search(last_col_name)
    )

    if page_frac >= 0.55 and is_page_col_name:
        # Confirm: at least one other column has long text (titles/descriptions)
        for c in range(df.shape[1] - 1):
            avg_len = (
                df.iloc[:, c]
                .astype(str)
                .str.strip()
                .str.len()
                .mean()
            )
            if avg_len > 15:
                return True

    # --- Staff list / acknowledgement detection ---
    all_text = df.astype(str)
    has_honorific = False
    has_job_title = False
    for c in range(df.shape[1]):
        col_text = " ".join(all_text.iloc[:, c].tolist())
        if _HONORIFIC.search(col_text):
            has_honorific = True
        if _JOB_TITLE.search(col_text):
            has_job_title = True

    if has_honorific and has_job_title:
        return True

    return False


# --- table-of-contents / index page detection ---------------------------------
# Dotted leaders ("Basic Characteristics ............ 47") are a contents-list
# fingerprint no statistical data row ever carries.
_DOT_LEADER = re.compile(r"\.{4,}")
# "INDEX" / "CONTENTS" / Hindi विषय सूची
_TOC_INDEX = re.compile(r"\b(index|contents)\b|विषय\s*सूची")
# a column that REFERENCES other tables/chapters (TOC left column)
_TOC_TABLE_REF = re.compile(
    r"\b(table\s*no|chapters?|sl\.?\s*no|s\.?\s*no|annexure\s*no)\b", re.IGNORECASE)
# a page-number column header
_TOC_PAGE_REF = re.compile(r"\bpage\s*no\b|\bpage\b", re.IGNORECASE)
# a page number OR a page range ("47", "1-48", "109–110")
_PAGE_OR_RANGE = re.compile(r"^\d{1,4}\s*[-–]\s*\d{1,4}$|^\d{1,3}$")
# a hierarchical section / table number ("1", "1.1", "2.3", "1A.10")
_SECTION_NUM = re.compile(r"^\d{1,3}[A-Za-z]?(\.\d{1,3})*$")


def _is_toc(sdf, columns=None):
    """A table-of-contents / index / list-of-tables page masquerading as data.

    Independent fingerprints, any one is sufficient:
      1. dotted leaders in >=25% of populated cells (a contents list), OR
      2. a header (row OR column name) that names a table/chapter reference AND
         a page column, with the last column actually holding page numbers, OR
      3. structural: first column is section/table numbers, last column is page
         numbers OR page ranges, and a middle column is long descriptive text —
         the 'list of tables' layout, even after its header degraded to col_N.
    A genuine statistical table carries none of these, so real data is kept."""
    rows = sdf.values.tolist()
    if len(rows) < 2:
        return False
    cells = [c.strip() for r in rows for c in r if c.strip() not in ("", "nan", "None")]
    if not cells:
        return False

    # 1) dotted-leader contents list
    if sum(1 for c in cells if _DOT_LEADER.search(c)) / len(cells) >= 0.25:
        return True

    # 2) explicit TOC vocabulary — scan the first rows AND the column names
    # (after apply_headers the TOC header becomes the column names).
    head_text = " ".join(c for r in rows[:2] for c in r)
    if columns is not None:
        head_text += " " + " ".join(str(c) for c in columns)
    has_index = bool(_TOC_INDEX.search(head_text))
    has_tableref = bool(_TOC_TABLE_REF.search(head_text))
    has_pageref = bool(_TOC_PAGE_REF.search(head_text))
    last_col = [r[-1].strip() for r in rows if r and r[-1].strip() not in ("", "nan", "None")]
    page_frac = (sum(1 for v in last_col if _PAGE_NUM.match(v)) / len(last_col)
                 ) if last_col else 0.0

    if has_index and (has_pageref or has_tableref):
        return True
    if has_tableref and has_pageref and page_frac >= 0.5:
        return True

    # 3) structural list-of-tables: section-numbers | descriptive title | pages
    if len(rows[0]) >= 3 and last_col:
        first_col = [r[0].strip() for r in rows if r and r[0].strip() not in ("", "nan", "None")]
        sec_frac = (sum(1 for v in first_col if _SECTION_NUM.match(v)) / len(first_col)
                    ) if first_col else 0.0
        pagey_frac = sum(1 for v in last_col if _PAGE_OR_RANGE.match(v.replace(" ", ""))) / len(last_col)
        # interior columns (between section# and page#): a TOC has only
        # descriptive titles there, NEVER numeric value columns. A ranking /
        # data table (serial | name | receipts | … | rank) has numeric interior
        # columns and must be kept — this is the decisive discriminator.
        descriptive = False
        numeric_interior = False
        for c in range(1, len(rows[0]) - 1):
            vals = [r[c].strip() for r in rows if c < len(r) and r[c].strip() not in ("", "nan", "None")]
            if not vals:
                continue
            if sum(len(v) for v in vals) / len(vals) > 20:
                descriptive = True
            if sum(1 for v in vals if _CLEAN_NUM.match(v)) / len(vals) >= 0.5:
                numeric_interior = True
        if sec_frac >= 0.5 and pagey_frac >= 0.5 and descriptive and not numeric_interior:
            return True
    return False


# --- prose-paragraph detection -------------------------------------------------
# A run of >=6 word-like tokens — a sentence, not a label.
_SENTENCE_CELL = re.compile(r"[A-Za-z]{2,}(?:[\s,]+[A-Za-z]{2,}){5,}")
# a clean, whole numeric value (not a number embedded in a sentence)
_CLEAN_NUM = re.compile(r"^\(?-?[\d,]+(\.\d+)?%?\)?$")


def _is_prose(sdf):
    """A prose paragraph (a bullet list, a 'Limitations' note, a narrative
    block) that camelot mis-parsed into cells. Signature: most populated cells
    are full sentences and almost none is a clean numeric value. A real
    statistical table — even a text-heavy one (state listings, indicator
    names) — carries clean numbers in its value columns, so it never trips."""
    rows = sdf.values.tolist()
    cells = [c.strip() for r in rows for c in r if c.strip() not in ("", "nan", "None")]
    if len(cells) < 3:
        return False
    sentence = sum(1 for c in cells if len(c.split()) >= 6 and _SENTENCE_CELL.search(c))
    numeric = sum(1 for c in cells if _CLEAN_NUM.match(c))
    return sentence / len(cells) >= 0.4 and numeric / len(cells) < 0.1


# a cell holding only small column-index tokens: "1", "(2)", "1 2 3"
_INDEX_TOKEN = re.compile(r"^\(?\d{1,2}\)?$")

from backend.app.translation.corruption import corruption_score


def _is_index_legend_row(cells):
    """True when a row is nothing but the column-number legend a PDF prints
    under the header ("1 | 2 | 3 | …" or "1 2 3 | 4 | 5").

    Garhwal-census fragments leave this band as the ONLY surviving data row, so
    a table whose every row is such a legend carries no real data and must be
    dropped.  The tokens must form a short, strictly-ascending small-int run so
    a genuine one-row KPI strip (real values, not 1..N) is never mistaken."""
    ints = []
    for c in cells:
        c = str(c).strip()
        if c in ("", "nan", "None"):
            continue
        toks = c.split()
        if not all(_INDEX_TOKEN.match(t) for t in toks):
            return False
        ints.extend(int(re.sub(r"\D", "", t)) for t in toks)
    if len(ints) < 2:
        return False
    ascending = all(b > a for a, b in zip(ints, ints[1:]))
    return ascending and ints[0] <= 3 and ints[-1] <= 40


def _to_text_frame(df):
    """A blank-for-missing string view of df.

    After numeric_normalizer casts a column, its missing cells are None and the
    column may be a StringDtype whose na-value is float nan — both of which leak
    a bare float through df.astype(str).values. Map every cell to "" / str(v)
    so the string-based validation checks never meet a non-string."""
    def s(v):
        if v is None:
            return ""
        if isinstance(v, float) and v != v:  # nan
            return ""
        return str(v)
    mapper = df.map if hasattr(df, "map") else df.applymap
    return mapper(s)


def validate_table(df):

    rows = len(df)
    cols = len(df.columns)

    #
    # Keep every real table, even tiny or headingless ones.
    # Reject only degenerate shapes that cannot be a table.
    #

    #
    # A single data row is still a table (e.g. a KPI strip:
    # header row of years + one row of totals).
    #

    if rows < 1:

        return {
            "passed": False,
            "reason": "too_few_rows"
        }

    if cols < 2:

        return {
            "passed": False,
            "reason": "too_few_columns"
        }

    sdf = _to_text_frame(df)
    cells = sdf.values.flatten()
    total = len(cells)

    empty = sum(
        1 for c in cells
        if c.strip() in ("", "nan", "None")
    )

    # front-matter pages (TOC, staff lists) are not statistical data
    if _is_frontmatter(sdf) or _is_toc(sdf, df.columns):
        return {
            "passed": False,
            "reason": "front_matter"
        }

    # prose paragraphs (bullet lists, narrative notes) mis-parsed as a table —
    # full sentences, no clean numeric values
    if _is_prose(sdf):
        return {
            "passed": False,
            "reason": "prose_text"
        }

    # ghost table: every row is just the column-number legend ("1 | 2 | 3 …")
    # — a header-only fragment with no real data (census village-directory
    # spillover). One real data row (KPI strip) is kept; a legend-only one is not.
    row_lists = sdf.values.tolist()
    if row_lists and all(_is_index_legend_row(r) for r in row_lists):
        return {
            "passed": False,
            "reason": "index_legend_only"
        }

    # garbled-source table: a large share of cells are still font-corrupt
    # (Devanagari OR Kruti-Dev soup) — un-translatable Hindi that OCR recovery
    # could not rescue. Quarantine it instead of shipping corrupt data as clean;
    # a stray untranslated name (a few cells) does NOT trip this.
    if corruption_score(df)[0] > 0.4:
        return {
            "passed": False,
            "reason": "garbled_source"
        }

    # phantom tables (charts parsed as tables) are almost entirely blank
    if total and empty / total > 0.85:

        return {
            "passed": False,
            "reason": "mostly_empty"
        }

    # crushed extraction: cells holding runs of values from many rows
    merged = sum(
        1 for c in cells
        if MERGED_RUN.match(c.strip())
    )

    if total and merged / total > 0.15:

        return {
            "passed": False,
            "reason": "merged_rows"
        }

    #
    # Headingless tables are kept (named "Table N" downstream);
    # weak headers alone are not grounds for rejection.
    #

    return {
        "passed": True,
        "reason": "ok"
    }
