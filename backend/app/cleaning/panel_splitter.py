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


# ── side-by-side independent panels ───────────────────────────────────────────
#
# A different layout from the merged-cell panels above: government reports often
# print two *separate* narrow tables next to each other on one page (e.g. NFHS
# FR375 prints a small "Result | Urban | Rural | Total" summary on the left and a
# wide "State | Month | Year | …" fieldwork table on the right). Camelot extracts
# them as ONE wide frame, so every right-panel row has an empty label column and
# is counted as an "orphan". Detecting the boundary and emitting two tables
# restores each panel's own label column and removes the orphans.

_NUMERIC_CELL = re.compile(r"^\(?-?[\d,]+(\.\d+)?%?\)?$")


def _populated(c):
    c = str(c).strip()
    return bool(c) and c.lower() not in ("nan", "none")


def _is_numeric(c):
    return bool(_NUMERIC_CELL.match(str(c).strip()))


def _panel_boundary(df, min_rows=8, frac=0.22, min_numrows=5):
    """
    Column index k that splits df into two side-by-side INDEPENDENT data tables,
    or None.

    Two conditions, both required, keep this from firing on ordinary wide tables
    or on misaligned single tables:
      (1) many data rows populate ONLY the left columns [0:k] xor ONLY the right
          columns [k:] — two different row structures means two tables, whereas
          one wide table fills cells across the boundary together; and
      (2) BOTH halves carry numeric data — rules out a single table whose wrapped
          text labels/serials drift into the left columns (DARPG) and prose pages
          that camelot mistakes for a grid (no numbers on either side).
    """
    ncols = df.shape[1]
    if ncols < 4:
        return None

    cells = df.values.tolist()
    pop = [[_populated(v) for v in row] for row in cells]

    best = None
    for k in range(2, ncols - 1):
        if ncols - k < 2:
            continue

        lo = ro = n = 0
        for r in pop:
            left = any(r[:k])
            right = any(r[k:])
            if not (left or right):
                continue
            n += 1
            if left and not right:
                lo += 1
            elif right and not left:
                ro += 1

        if n < min_rows:
            continue

        lof, rof = lo / n, ro / n
        if not (lof >= frac and rof >= frac and (lo + ro) >= 0.55 * n):
            continue

        lnum = sum(1 for row in cells if any(_is_numeric(row[j]) for j in range(k)))
        rnum = sum(1 for row in cells if any(_is_numeric(row[j]) for j in range(k, ncols)))
        if lnum < min_numrows or rnum < min_numrows:
            continue

        score = min(lof, rof)
        if best is None or score > best[1]:
            best = (k, score)

    return best[0] if best else None


def split_side_by_side(df):
    """
    If df is two side-by-side independent tables, return [left_df, right_df];
    otherwise return [df]. Each panel keeps only its own non-empty rows and is
    re-indexed with fresh integer columns so the downstream header pipeline
    treats it as a standalone table.
    """
    if df is None or df.empty or df.shape[1] < 4:
        return [df]

    k = _panel_boundary(df)
    if k is None:
        return [df]

    out = []
    for panel in (df.iloc[:, :k], df.iloc[:, k:]):
        mask = panel.apply(lambda r: any(_populated(v) for v in r), axis=1)
        panel = panel[mask].reset_index(drop=True)
        if len(panel) >= 2:
            panel.columns = list(range(panel.shape[1]))
            out.append(panel)

    return out if len(out) >= 2 else [df]


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
