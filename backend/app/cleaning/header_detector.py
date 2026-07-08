import re


# any year or fiscal-year span, e.g. 2011, 2020-21, 2024-25, 2022/23
YEAR_PATTERN = re.compile(
    r"\b(19|20)\d{2}\s*([-–/]\s*\d{2,4})?\b"
)

NUMERIC_CELL = re.compile(
    r"^-?[\d,]+(\.\d+)?%?$"
)

# calendar month label used as a column header ("Jan", "February", "Sept")
MONTH_PATTERN = re.compile(
    r"^(jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|jun(e)?|jul(y)?|"
    r"aug(ust)?|sep(t(ember)?)?|oct(ober)?|nov(ember)?|dec(ember)?)\.?$",
    re.IGNORECASE,
)


def is_month_header_row(row):
    """A row whose value cells are predominantly month names is a column
    header with very high confidence — no data row is a run of bare months.
    Column 0 (row-label column, e.g. "Month") may hold anything."""
    cells = [str(c).strip() for c in list(row)[1:]
             if str(c).strip() not in ("", "nan", "None")]
    if len(cells) < 3:
        return False
    months = sum(1 for c in cells if MONTH_PATTERN.match(c))
    return months >= max(3, len(cells) * 0.7)


def _numeric_density(row):

    cells = [
        str(c).strip()
        for c in row
        if str(c).strip() not in ("", "nan", "None")
    ]

    if not cells:
        return 0.0, 0

    numeric = sum(
        1 for c in cells
        if NUMERIC_CELL.match(c.replace(" ", ""))
    )

    return numeric / len(cells), numeric


def detect_header_rows(df):

    if df.empty:
        return 1

    # Reference/lookup tables (code+text catalogues) have no numeric measure
    # region to anchor the header/data boundary; the numeric-density scan below
    # would read their leading hierarchy rows as multi-level header. Route them
    # to a record-aware detector. Statistical tables fall through unchanged.
    from backend.app.profile.table_profiler import classify_table, reference_header_rows
    if classify_table(df)["archetype"] == "reference":
        return reference_header_rows(df)

    max_scan = min(8, len(df))

    # A row of month names is the strongest header signal there is — charts
    # exported above the grid leave numeric axis debris ("2000 | 2508") in the
    # leading rows that the year-row rule below would mistake for a year
    # header, cutting the band short of the real month header. The LAST month
    # row wins (sources sometimes print the month band twice).
    month_row = None
    for i in range(max_scan):
        if is_month_header_row(df.iloc[i]):
            month_row = i
    if month_row is not None:
        return month_row + 1

    year_row = None
    data_row = None

    for i in range(max_scan):

        row = df.iloc[i].astype(str).tolist()
        text = " ".join(row)

        # legacy Hindi artifact marks the last header row
        if re.search(r"¿\d+À", text):
            return i + 1

        density, count = _numeric_density(row)

        # a row of year labels is a header row, not data
        year_cells = sum(
            1 for c in row
            if YEAR_PATTERN.fullmatch(str(c).strip())
        )

        if year_cells >= 2 and year_row is None:
            year_row = i
            continue

        # mostly-numeric row = first data row
        if density >= 0.5 and count >= 2 and data_row is None:
            data_row = i

    if year_row is not None:
        return year_row + 1

    if data_row is not None:
        return max(data_row, 1)

    # text-heavy tables (company/state listings) have no mostly-numeric
    # row; the header still ends where numeric cells first appear
    # ("Company | State | Capacity" then "ACB Ltd | MP | 12.5")
    for i in range(max_scan):

        density, count = _numeric_density(df.iloc[i].astype(str).tolist())

        if count >= 1 and density >= 0.3:
            return max(i, 1)

    return min(2, len(df))
