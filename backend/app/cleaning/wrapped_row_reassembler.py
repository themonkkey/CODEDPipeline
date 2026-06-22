"""
Camelot's stream flavor splits a single logical table row across several
physical rows whenever an indicator label wraps to 2-3 PDF lines. The
numbers land on their own row while the label fragments sit on separate,
value-less rows:

    ["41. Mothers who received postnatal c...", "", "", "", ""]   <- label start
    ["",                                  "88.6", "80.7", ...]     <- numbers only
    ["personnel within 2 days of delivery+", "", "", "", ""]       <- continuation

This module stitches such fragments back into one row, attaching the
orphaned numbers to the reassembled label. Section sub-headers ("Women",
"Blood Sugar Level among Adults") and the start of the NEXT indicator are
left untouched so reassembly never swallows a real boundary.

Runs AFTER clean_dataframe (empty cells preserved as "") and BEFORE header
detection, so the top header rows — which never look numeric-only — are
never disturbed.
"""

import re

# a bare numeric data cell: "8.0", "12", "1,234", "60.2%", "(8.0)", "-"
_NUMERIC = re.compile(r"^\(?-?[\d,]+(\.\d+)?%?\)?$")

# label that opens a new indicator: "41. Mothers ...", "10 . Households ..."
_NUMBERED = re.compile(r"^\d+\s*\.")


def _cell(v):
    return str(v).strip()


def _numeric_count(values):
    return sum(1 for v in values if _NUMERIC.match(_cell(v)))


def _is_numeric_only(row):
    """First column empty, but the value columns carry the numbers."""
    if _cell(row[0]):
        return False
    values = row[1:]
    if not values:
        return False
    return _numeric_count(values) >= max(2, len(values) // 2)


def _is_label_only(row):
    """First column has text, no numeric values anywhere to the right."""
    if not _cell(row[0]):
        return False
    return _numeric_count(row[1:]) == 0


def _is_section_header(text):
    """
    A group title ("Women", "Blood Sugar Level among Adults (age 15-49)")
    rather than a wrapped indicator fragment. Numbered lines and lowercase
    continuations are NOT section headers.
    """
    if not text or _NUMBERED.match(text):
        return False
    if text[:1].islower() or text.startswith("("):
        return False
    # continuation tails close a wrapped phrase, e.g. "others) (%)"
    if text.rstrip().endswith("(%)"):
        return False
    return True


def reassemble_wrapped_rows(df):
    """
    Merge label-only + numeric-only fragments produced by Camelot stream on
    wrapped indicator rows. Returns a new DataFrame with the same columns.
    """

    if df.empty or df.shape[1] < 2:
        return df

    rows = df.values.tolist()
    n = len(rows)
    out = []
    i = 0

    while i < n:
        row = rows[i]

        if not _is_numeric_only(row):
            out.append(row)
            i += 1
            continue

        # numbers with no label: pull the label fragments that wrap around
        # them — preceding lines (the numbered anchor + any continuations
        # already emitted) and following continuation lines.
        preceding = []
        while out:
            prev = out[-1]
            if not _is_label_only(prev):
                break
            txt = _cell(prev[0])
            if _is_section_header(txt):
                break
            out.pop()
            preceding.insert(0, txt)
            if _NUMBERED.match(txt):  # reached the indicator's start line
                break

        following = []
        j = i + 1
        while j < n:
            nxt = rows[j]
            if not _is_label_only(nxt):
                break
            txt = _cell(nxt[0])
            if _is_section_header(txt) or _NUMBERED.match(txt):
                break
            following.append(txt)
            j += 1

        label = " ".join(p for p in (preceding + following) if p)

        if not label:
            # genuinely orphaned numbers — leave the row as-is
            out.append(row)
            i += 1
            continue

        merged = list(row)
        merged[0] = label
        out.append(merged)
        i = j

    import pandas as pd

    return pd.DataFrame(out, columns=df.columns)
