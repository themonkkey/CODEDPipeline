"""Table archetype profiler — understand a table before cleaning it.

The cleaning pipeline is tuned for STATISTICAL tables (rows = entities, columns =
numeric measures). A second large family exists: REFERENCE / lookup tables —
hierarchical code + text catalogues (occupation classifications, code
concordances, indices) that carry NO numeric measures. The numeric-density
heuristics that locate the header/data boundary misfire on them (they read the
leading hierarchy rows as multi-level header and shred the table).

`classify_table` labels a frame so the pipeline can route to the right header
strategy. It is deliberately CONSERVATIVE: only a clear reference table is
labelled "reference"; everything else stays "statistical" and keeps the exact
current behaviour, so the statistical corpus is untouched.
"""
import re

# A structured hierarchical code: 3-4 integer digits, a dot, 2-4 decimal digits,
# e.g. "1111.0100", "1111.10". Distinct from a measure like "12.34" (2-digit int)
# by requiring a >=3-digit integer part, and from "100.5" by >=2 decimals.
_STRUCTURED_CODE = re.compile(r"^\d{3,4}\.\d{2,4}$")
# A measure cell: thousands-separated, percent, parenthesised, or a plain decimal
# that is NOT a structured code.
_MEASURE = re.compile(r"^\(?-?\d{1,3}(,\d{3})+(\.\d+)?\)?%?$|^\(?-?\d+\.\d{1,2}\)?%?$|^-?\d{1,2}(\.\d+)?%$")
_WORD = re.compile(r"[A-Za-z]{2,}")
_BLANK = {"", "nan", "none", "-", "–", "—"}


def _cells(df, skip_first_row=True):
    rows = df.values.tolist()
    if skip_first_row and len(rows) > 1:
        rows = rows[1:]
    return rows


def _is_multiword_text(s):
    return len(_WORD.findall(s)) >= 2


def classify_table(df):
    """Return a dict describing the table archetype.

    {archetype: "statistical" | "reference",
     code_row_frac, text_cell_frac, measure_cell_frac}

    reference  — code+text lookup catalogue (no measures)
    statistical — everything else (default; unchanged pipeline behaviour)
    """
    rows = _cells(df)
    populated = [str(v).strip() for r in rows for v in r
                 if str(v).strip().lower() not in _BLANK]
    n_cells = len(populated)
    if n_cells < 4 or len(rows) < 3:
        return {"archetype": "statistical", "code_row_frac": 0.0,
                "text_cell_frac": 0.0, "measure_cell_frac": 0.0}

    code_rows = 0
    for r in rows:
        vals = [str(v).strip() for v in r]
        if any(_STRUCTURED_CODE.match(v) for v in vals):
            code_rows += 1
    code_row_frac = code_rows / len(rows)

    text_cells = sum(1 for v in populated if _is_multiword_text(v))
    # a measure is a numeric value that is NOT a structured hierarchical code
    # (NCO codes like "1111.10" otherwise read as decimals and inflate measures)
    measure_cells = sum(1 for v in populated
                        if _MEASURE.match(v) and not _STRUCTURED_CODE.match(v))
    text_cell_frac = text_cells / n_cells
    measure_cell_frac = measure_cells / n_cells

    # Reference: structured codes on many rows, genuinely text-heavy, and almost
    # no statistical measures. All three gates must hold so a numeric table is
    # never mistaken for a catalogue.
    is_reference = (
        code_row_frac >= 0.4
        and text_cell_frac >= 0.25
        and measure_cell_frac < 0.15
    )

    return {
        "archetype": "reference" if is_reference else "statistical",
        "code_row_frac": round(code_row_frac, 3),
        "text_cell_frac": round(text_cell_frac, 3),
        "measure_cell_frac": round(measure_cell_frac, 3),
    }


def is_reference_table(df):
    return classify_table(df)["archetype"] == "reference"


# any standalone code/id token in a reference record: "1", "11", "1111",
# "1111.0100", "1111.10", "(1)" — a digit-only cell, optionally dotted/parenthesised
_RECORD_CODE = re.compile(r"^\(?\d{1,4}(\.\d{1,4})?\)?$")


def reference_header_rows(df):
    """Header-row count for a reference/lookup table.

    A reference table's data rows are CODE+TEXT records; the header is the short
    run of leading rows that carry NO standalone code (e.g. "NCO 2015 | | | NCO
    2004"). Return the index of the first record row — so the leading hierarchy
    rows (Division/Sub-Division/Group/Family, which DO carry codes) are kept as
    data, never absorbed as multi-level header. 0 = continuation page (data from
    the top, no header)."""
    n = min(8, len(df))
    for i in range(n):
        vals = [str(v).strip() for v in df.iloc[i].tolist()]
        if any(_RECORD_CODE.match(v) and v not in ("", "0", "(0)") for v in vals):
            return i
    return 1 if len(df) > 1 else 0
