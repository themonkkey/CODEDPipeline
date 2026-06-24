"""
Make extracted numeric columns analysis-ready.

Camelot returns every cell as a string, so a column of numbers ships as
"45,544", "3348245*", "(7.5)", "12.3%", "-".  pandas reads those as object
dtype, and df.sum() / df.mean() silently fail or concatenate.  This module
casts a column to real numbers when it is predominantly numeric, stripping
thousands separators and footnote markers and turning dash/blank placeholders
into NaN.

Deliberately conservative: a column is left untouched when any cell carries a
MERGED multi-value continuation (e.g. the RBI "16411581 (15878397)" provisional
figure produced by merge_continuation_values), so that reshaping never destroys
a value the rest of the pipeline built on purpose.

Runs at the very end of the per-table pipeline (after merge_continuation_values),
so headers are already set and only data cells are affected.
"""

import re

import pandas as pd

# a single formatted numeric value:
#   45,544   3348245*   (7.5)   12.3%   -45   1.2†   1,234.5‡   −5 (unicode minus)
_NUMERIC_CELL = re.compile(r"^[(\-+−]?\s*[\d,]+(\.\d+)?\s*[)%*†§¶#‡]?$")

# two or more numbers in one cell — a merged / continuation value that must NOT
# be coerced to NaN ("16411581 (15878397)", "10 20 30")
_MERGED_CELL = re.compile(r"\d[\d,]*(\.\d+)?\s+[(]?\d")

# textual stand-ins for "no value"
_PLACEHOLDERS = {
    "", "nan", "none", "-", "–", "—", "−", "n.a.", "na", "n/a",
    "nil", "..", "...", "*", "neg", "negligible",
}

_FOOTNOTE_MARKERS = "%*†§¶#‡"


def _to_number(cell):
    """Parse one formatted numeric string to a Python int (when whole) or
    float, or None when it is not a number.

    Returning an int for whole values keeps the exported cell clean ("45544",
    not "45544.0") which matters for serial / ID / count columns that analysts
    join on; decimals stay float ("7.5"). None becomes a blank cell on export.
    """
    s = str(cell).strip()
    if s.lower() in _PLACEHOLDERS:
        return None
    # unicode minus / en-dash used as a leading sign -> ascii hyphen
    if s[:1] in ("−", "–"):
        s = "-" + s[1:]
    negative = s.startswith("(") and s.rstrip(_FOOTNOTE_MARKERS).endswith(")")
    s = s.strip("()").strip()
    s = s.rstrip(_FOOTNOTE_MARKERS).strip()
    s = s.replace(",", "")
    if not s:
        return None
    try:
        value = float(s)
    except ValueError:
        return None
    if negative:
        value = -value
    # collapse whole-valued floats to int so the cell exports without ".0"
    if value.is_integer():
        return int(value)
    return value


def _column_is_castable(values):
    """True when the (non-empty) cells are predominantly clean single numbers
    and none is a merged multi-value cell."""
    non_empty = [v for v in values if v.lower() not in ("", "nan", "none")]
    if not non_empty:
        return False
    if any(_MERGED_CELL.search(v) for v in non_empty):
        return False
    # placeholders don't count for or against — judge the real values
    judged = [v for v in non_empty if v.lower() not in _PLACEHOLDERS]
    if not judged:
        return False
    numeric = sum(1 for v in judged if _NUMERIC_CELL.match(v))
    return numeric / len(judged) >= 0.8


def normalize_numeric_columns(df, threshold=0.8):
    """Cast predominantly-numeric columns to float; leave label, mixed, and
    merged-continuation columns as-is.  Returns a new DataFrame."""
    if df is None or df.empty or df.shape[1] == 0:
        return df

    out = df.copy()
    for i in range(out.shape[1]):
        values = out.iloc[:, i].astype(str).str.strip().tolist()
        if _column_is_castable(values):
            # object dtype preserves per-cell int vs float vs None so the
            # export stays clean ("100" / "7.5" / "") instead of pandas
            # upcasting the whole column to float ("100.0").
            out.isetitem(i, pd.Series(
                [_to_number(v) for v in out.iloc[:, i].astype(str)],
                index=out.index, dtype=object,
            ))
    return out
