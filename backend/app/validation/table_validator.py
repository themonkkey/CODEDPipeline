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
    if _is_frontmatter(sdf):
        return {
            "passed": False,
            "reason": "front_matter"
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
