"""Content-based column-role inference.

When a column's header is missing or generic (common in reference/lookup
catalogues whose sub-columns carry no individual header text), the only way to
name it well is to read what it CONTAINS. This module infers a semantic role
from a column's values — code, level, name, state, year — so the output is
addressable with meaningful names instead of col_N / value / label.
"""
import re

_BLANK = {"", "nan", "none", "-", "–", "—"}

# structured identifier: dotted hierarchical code (1111.0100, 1111.10) or a
# 3-4 digit group code (111, 1111). Short 1-2 digit ints are too ambiguous to
# call a code on their own.
_DOTTED_CODE = re.compile(r"^\(?\d{1,4}\.\d{1,4}\)?$")
_GROUP_CODE = re.compile(r"^\d{3,4}$")
_YEAR = re.compile(r"^(19|20)\d{2}([-–/]\d{2,4})?$")
_WORD = re.compile(r"[A-Za-z]{2,}")

# hierarchy-level vocabulary (occupation / industry classification ladders)
_LEVEL_WORDS = {
    "division", "sub-division", "subdivision", "sub division", "group",
    "sub-group", "subgroup", "sub group", "family", "major", "minor", "unit",
    "section", "sub-major", "submajor", "class", "subclass", "sub-class",
}

# Indian states/UTs — a column that is mostly these is a "state" dimension
_STATES = {
    "andhra pradesh", "arunachal pradesh", "assam", "bihar", "chhattisgarh",
    "goa", "gujarat", "haryana", "himachal pradesh", "jharkhand", "karnataka",
    "kerala", "madhya pradesh", "maharashtra", "manipur", "meghalaya", "mizoram",
    "nagaland", "odisha", "punjab", "rajasthan", "sikkim", "tamil nadu",
    "telangana", "tripura", "uttar pradesh", "uttarakhand", "west bengal",
    "jammu and kashmir", "ladakh", "delhi", "puducherry", "chandigarh",
}


def _populated(series):
    return [str(v).strip() for v in series.tolist()
            if str(v).strip().lower() not in _BLANK]


def infer_role(series):
    """Infer a column's semantic role from its values, or None if unclear.

    Returns one of: "code", "level", "state", "year", "name".
    Thresholds are majority-based so a few stray cells never flip the role."""
    vals = _populated(series)
    if not vals:
        return None
    n = len(vals)

    code = sum(1 for v in vals if _DOTTED_CODE.match(v) or _GROUP_CODE.match(v))
    if code / n >= 0.6:
        return "code"

    level = sum(1 for v in vals if v.lower() in _LEVEL_WORDS)
    if level / n >= 0.6:
        return "level"

    state = sum(1 for v in vals if v.lower() in _STATES)
    if state / n >= 0.6:
        return "state"

    year = sum(1 for v in vals if _YEAR.match(v))
    if year / n >= 0.6:
        return "year"

    multiword = sum(1 for v in vals if len(_WORD.findall(v)) >= 2)
    if multiword / n >= 0.5:
        return "name"

    return None


_GENERIC = re.compile(r"^(col(_\d+)?|value(_\d+)?|label(_\d+)?|nco(_\d+)?)$")


def infer_reference_columns(df):
    """Semantic column names for a (merged) reference table, inferred from the
    full column content. Run AFTER cross-page merge so every column sees all its
    values — per-page inference is unstable when a page lacks hierarchy rows.

    A confidently-inferred role wins; an already-meaningful header name is kept;
    only generic fallbacks (col_N / value / label / nco) are replaced. Duplicate
    roles are disambiguated with a numeric suffix."""
    existing = [str(c) for c in df.columns]
    names = []
    for c in range(df.shape[1]):
        role = infer_role(df.iloc[:, c])
        if role:
            names.append(role)
        elif not _GENERIC.fullmatch(existing[c]):
            names.append(existing[c])          # keep a real header name
        else:
            names.append(existing[c])          # leave generic fallback as-is
    # dedupe positionally (code -> code, code_2)
    seen, out = {}, []
    for nm in names:
        if nm in seen:
            seen[nm] += 1
            out.append(f"{nm}_{seen[nm]}")
        else:
            seen[nm] = 0
            out.append(nm)
    return out
