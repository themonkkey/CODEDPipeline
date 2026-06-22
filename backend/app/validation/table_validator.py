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

    cells = df.astype(str).values.flatten()
    total = len(cells)

    empty = sum(
        1 for c in cells
        if c.strip() in ("", "nan", "None")
    )

    # front-matter pages (TOC, staff lists) are not statistical data
    if _is_frontmatter(df):
        return {
            "passed": False,
            "reason": "front_matter"
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
