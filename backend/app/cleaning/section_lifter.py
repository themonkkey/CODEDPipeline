"""
Lift in-table section headings into a `category` column.

Many statistical factsheets (NFHS, PLFS by-group tables) print section banners
as their own row — a label in column 0 with every value column blank:

    Marriage and Fertility            |      |      |      |
    1. Women age 20-24 married before…| 11.4 | 14.7 | 12.6 | 14.6
    2. Total fertility rate            |  1.6 |  2.1 |  1.9 |  2.0
    Infant and Child Mortality Rates  |      |      |      |
    3. Neonatal mortality rate         | 18.6 | 24.5 | 22.0 | 24.9

An analyst who groups by these rows silently corrupts the result: the banner row
carries no values and the section meaning is lost the moment rows are sorted or
filtered. This module pulls each banner into a leading `category` column,
forward-fills it onto the data rows beneath, and drops the banner rows — turning
the sheet into tidy data that `groupby("category")` handles correctly.

Conservative by design: it fires ONLY when at least two banner rows each head a
block of real data (genuine sectioning), so a stray sub-title or a continuation
label never triggers a spurious column. Every other table passes through
untouched.
"""

_MISSING = ("", "nan", "None")


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, float) and v != v:  # nan
        return ""
    return str(v).strip()


def _category_name(columns):
    """Pick a header for the new column that doesn't collide with an existing one."""
    existing = {str(c).lower() for c in columns}
    for name in ("category", "section", "section_category"):
        if name not in existing:
            return name
    n = 2
    while f"category_{n}" in existing:
        n += 1
    return f"category_{n}"


def lift_section_rows(df):
    """Return a copy of df with a leading `category` column built from in-table
    section banners, or the original df unchanged when there is no real
    sectioning to lift."""
    if df is None or df.empty or df.shape[1] < 2:
        return df

    rows = [[_cell(v) for v in row] for row in df.values.tolist()]
    n = len(rows)

    def is_label_only(r):
        return bool(r[0]) and r[0] not in _MISSING and all(
            c in _MISSING for c in r[1:]
        )

    def is_data(r):
        return any(c and c not in _MISSING for c in r[1:])

    lo = [is_label_only(r) for r in rows]
    dt = [is_data(r) for r in rows]

    # A banner row is a section header only when a real data row follows it
    # before the next banner row (i.e. it actually heads a block of data).
    section_idx = set()
    for i in range(n):
        if not lo[i]:
            continue
        for j in range(i + 1, n):
            if lo[j]:
                break
            if dt[j]:
                section_idx.add(i)
                break

    # Need genuine sectioning: at least two banners each heading a data block.
    if len(section_idx) < 2:
        return df

    current = ""
    categories = []
    keep = []
    for i in range(n):
        if lo[i]:
            # Once we are lifting, every value-less label row goes: section
            # banners become the category, and stray notes / footnotes
            # ("Note: …") are dropped — they carry no data either way.
            if i in section_idx:
                current = rows[i][0]
            continue
        categories.append(current if dt[i] else "")
        keep.append(i)

    # Require the lift to actually classify several data rows, otherwise leave
    # the table alone rather than prepend a near-empty column.
    classified = sum(1 for idx, c in zip(keep, categories) if c and dt[idx])
    if classified < 2:
        return df

    out = df.iloc[keep].reset_index(drop=True)
    out.insert(0, _category_name(out.columns), categories)
    return out
