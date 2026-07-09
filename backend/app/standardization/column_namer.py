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
_PERCENT = re.compile(r"^\(?-?\d+(\.\d+)?\s*%\)?$")
# calendar month names — a column of these is a time dimension ("month"),
# common in monthly-report annexures whose header row was lost upstream
_MONTH = re.compile(
    r"^(jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|jun(e)?|jul(y)?|"
    r"aug(ust)?|sep(t|tember)?|oct(ober)?|nov(ember)?|dec(ember)?)"
    r"([,'\s\-–]+\d{2,4})?$",
    re.IGNORECASE,
)
# serial-number cell: "1", "1.", "(1)" — only a serial column when the whole
# column is an ascending run (see infer_role), never on lone matches
_SERIAL = re.compile(r"^\(?\d{1,3}\)?\.?$")
_DATE = re.compile(
    r"^\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}$"                       # 01/02/2024
    r"|^\d{1,2}\s*[-/ ]\s*[A-Za-z]{3,9}\s*[-/ ]\s*\d{2,4}$"    # 1 Jan 2024
    r"|^[A-Za-z]{3,9}\s+\d{4}$",                               # January 2024
)

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


# decorations around a state/UT name in govt reports: a "Government of
# [the Union Territory of]" prefix (CPGRAMS-style listings), a leading
# ordinal ("17. Gujarat" in wrapped multi-column state lists) and a trailing
# count annotation ("Haryana- 4"). Stripped before state matching only —
# the raw value is never rewritten.
_STATE_PREFIX = re.compile(
    r"^(?:\d{1,3}\s*[.)]\s*)?(?:government\s+of\s+)?(?:the\s+)?"
    r"(?:union\s+territory\s+of\s+)?(?:nct\s+of\s+)?",
    re.IGNORECASE,
)
_STATE_SUFFIX = re.compile(r"\s*[-–]\s*\d{1,3}$")


def _state_core(value):
    """The bare state/UT name inside a decorated cell, lowercased."""
    v = _STATE_SUFFIX.sub("", _STATE_PREFIX.sub("", value.strip()))
    return v.strip().lower()


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

    dotted = sum(1 for v in vals if _DOTTED_CODE.match(v))
    if dotted / n >= 0.6:
        return "code"

    # bare 3-4 digit integers are only codes when they look ASSIGNED rather
    # than measured: classification codes are fixed-width (every NCO group
    # code in a column has the same digit count, "111"/"112" or
    # "1111"/"1112") or carry leading zeros. A column of 3-4 digit COUNTS
    # (grievance receipts: 186, 1280, 2285) varies in width — that is a
    # measure, and calling it "code" mislabels it.
    group = [v for v in vals if _GROUP_CODE.match(v)]
    if group and len(group) / n >= 0.6:
        widths = {len(v) for v in group}
        if len(widths) == 1 or any(v.startswith("0") for v in group):
            return "code"

    # serial-number column: every cell a short bare number AND the sequence
    # ascends from ~1 in unit steps — the "S. No." column whose header text
    # was lost. Ascent is what separates a serial column from a measure of
    # small counts (which varies non-monotonically).
    serial = [v for v in vals if _SERIAL.match(v)]
    if n >= 3 and len(serial) / n >= 0.8:
        nums = [int(re.sub(r"[^\d]", "", v)) for v in serial]
        if nums and nums[0] <= 2 and all(0 <= b - a <= 3 for a, b in zip(nums, nums[1:])):
            return "s_no"

    month = sum(1 for v in vals if _MONTH.match(v))
    if month / n >= 0.6:
        return "month"

    level = sum(1 for v in vals if v.lower() in _LEVEL_WORDS)
    if level / n >= 0.6:
        return "level"

    state = sum(1 for v in vals if _state_core(v) in _STATES)
    if state / n >= 0.6:
        return "state"

    year = sum(1 for v in vals if _YEAR.match(v))
    if year / n >= 0.6:
        return "year"

    pct = sum(1 for v in vals if _PERCENT.match(v))
    if pct / n >= 0.6:
        return "percentage"

    date = sum(1 for v in vals if _DATE.match(v))
    if date / n >= 0.6:
        return "date"

    multiword = sum(1 for v in vals if len(_WORD.findall(v)) >= 2)
    if multiword / n >= 0.5:
        return "name"

    return None


# High-confidence roles safe to name ANY archetype's generic col_N column with.
# "name" is excluded — too generic to assert on an arbitrary text column (the
# existing "label" fallback covers that case).
_CONFIDENT = {"code", "level", "state", "year", "percentage", "date", "month", "s_no"}


def confident_role(series):
    """A column role only when it is unambiguous enough to rename a generic
    col_N column on any table (state, year, percentage, date, code, level)."""
    role = infer_role(series)
    return role if role in _CONFIDENT else None


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
