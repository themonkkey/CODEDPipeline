"""
Detect and flatten two-panel (side-by-side) tables.

Government statistical publications frequently print wide tables as two
side-by-side panels on one page (e.g. Population of 19 Metropolitan Cities:
cities 1-9 on the left, cities 10-19 on the right).

Camelot extracts these as a table with ~2 data rows where each cell in the
"data" row is a single mega-string containing all the entries from that
panel, concatenated:

    "1 Ahmedabad (Gujarat) 63.52 2 Bengaluru (Karnataka) 84.99 ... 9 Jaipur 30.73"

This module:
  1. Detects such cells (≥3 repetitions of {number} {text} {number} pattern).
  2. Splits each merged cell into individual entries.
  3. Pairs left-panel and right-panel entries and returns a flat dataframe
     with one row per original entry.
"""

import re
import pandas as pd

# Boundary between two consecutive panel entries.
# After a trailing digit/decimal, before the next serial+UppercaseName.
# Handles both "63.52 2 Bengaluru" and "Pradesh 1 15.65 Arunachal".
_ENTRY_BOUNDARY = re.compile(r"(?<=\d)\s+(?=\d+[\s.]\s*[A-Z])")

# A plausible panel entry: number, then name-like text (3+ word-chars), then number.
_PANEL_ENTRY_RE = re.compile(
    r"\d+(?:\.\d+)?"           # leading number (serial or value)
    r"\s+"
    r"[A-Z][A-Za-z\s()&/,.-]{3,}"  # name (city / state)
    r"\d+(?:\.\d+)?"           # trailing number (value or serial)
)


def _split_panel_cell(text):
    """
    Split a concatenated panel cell into a list of individual entry strings.
    Returns None if the cell does not look like a merged panel.
    """
    text = text.strip()
    if len(text) < 20:
        return None

    # Quick check: does the cell have ≥3 panel-entry matches?
    if len(_PANEL_ENTRY_RE.findall(text)) < 3:
        return None

    parts = _ENTRY_BOUNDARY.split(text)
    if len(parts) >= 3:
        return [p.strip() for p in parts if p.strip()]
    return None


def _parse_entry(entry):
    """
    Parse one entry string like "1 Ahmedabad (Gujarat) 63.52"
    or "532.17 Andhra Pradesh 1" into (serial, name, value).

    Returns a dict with keys matching the detected columns, or None.
    """
    entry = entry.strip()
    # Try serial-first: int + name + decimal
    m = re.match(
        r"^(\d+)\s+([A-Z][A-Za-z\s()&/,.-]+?)\s+(\d+(?:\.\d+)?)$", entry
    )
    if m:
        return {"sl": m.group(1), "name": m.group(2).strip(), "value": m.group(3)}

    # Try value-first: decimal + name + int
    m = re.match(
        r"^(\d+(?:\.\d+)?)\s+([A-Z][A-Za-z\s()&/,.-]+?)\s+(\d+)$", entry
    )
    if m:
        return {"sl": m.group(3), "name": m.group(2).strip(), "value": m.group(1)}

    # Fallback: just store raw string
    return {"sl": "", "name": entry, "value": ""}


def split_panels(df):
    """
    If the dataframe looks like a two-panel table, flatten it to one row per
    entry.  Otherwise return the dataframe unchanged.

    Caller should invoke this BEFORE header detection / apply_headers.
    """
    if df.empty or len(df) < 1:
        return df

    # Find all cells that look like merged panel content
    panel_cells = []   # (row_idx, col_idx, entries_list)
    for row_idx in range(len(df)):
        for col_idx in range(df.shape[1]):
            cell = str(df.iloc[row_idx, col_idx])
            entries = _split_panel_cell(cell)
            if entries:
                panel_cells.append((row_idx, col_idx, entries))

    if not panel_cells:
        return df

    # Collect all parsed entries from all panel cells
    all_entries = []
    for _, _, entries in panel_cells:
        for e in entries:
            parsed = _parse_entry(e)
            if parsed:
                all_entries.append(parsed)

    if not all_entries:
        return df

    # Sort by serial number so left+right panels are in order
    def _serial_key(e):
        try:
            return int(e["sl"])
        except (ValueError, TypeError):
            return 9999

    all_entries.sort(key=_serial_key)

    # Infer column names from the original header row (row 0 if data starts at row 1)
    # Use generic names if the header is ambiguous
    col_names = ["s_no", "name", "value"]
    if len(df) >= 1:
        header_row = df.iloc[0].astype(str).str.strip().tolist()
        non_empty = [c for c in header_row if c and c.lower() not in ("nan", "none", "")]
        # Map the first 3 distinct non-empty header cells to our 3 columns
        distinct = []
        seen = set()
        for h in non_empty:
            hl = h.lower()
            if hl not in seen:
                seen.add(hl)
                distinct.append(h)
        if len(distinct) >= 3:
            col_names = [distinct[0], distinct[1], distinct[2]]
        elif len(distinct) == 2:
            col_names = ["s_no", distinct[0], distinct[1]]

    rows = [
        {col_names[0]: e["sl"], col_names[1]: e["name"], col_names[2]: e["value"]}
        for e in all_entries
    ]
    return pd.DataFrame(rows)
