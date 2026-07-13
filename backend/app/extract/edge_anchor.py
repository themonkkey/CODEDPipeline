"""
Right-edge column anchoring: rebuild a ruled table's grid from the word layer.

Numeric values in ruled statistical tables are right-aligned within their
columns, so the right edges of numeric word boxes cluster tightly per column
— even when camelot's flavors misassign the tokens (stream's whitespace
clustering merges columns; lattice hallucinates phantom columns from
decorative rules). Clustering those right edges gives exact column anchors,
and each numeric token snaps to the nearest anchor. Everything that is not a
value becomes the row label.

Proven on the Rajasthan Budget-At-A-Glance series (2016-2026), whose pages
defeat both camelot flavors; generalized here as a retry strategy for
table_extractor's extract-verify-re-extract loop.

Two word-layer defects observed on real budget PDFs are repaired first:

  * fused tokens — adjacent parenthesized values arrive as ONE word, e.g.
    "(1500000.00)(1500000.00)"; split at the ")(" boundary, apportioning the
    box x-range by character count.
  * split values — one printed value arrives as TWO words, e.g.
    "-1 401108.93" for -1401108.93; merge a short leading numeric fragment
    into its nearly-touching numeric right neighbour when the concatenation
    matches the value pattern.
"""

import re

# a numeric VALUE cell: optional parens (accounting negatives), sign, comma
# grouping, optional decimals. Deliberately excludes bare 1-2 digit integers
# ("1", "12" — serial numbers / footnote marks would seed phantom anchors);
# those still snap to an anchor once a column of real values establishes it.
VALUE_TOKEN = re.compile(
    r"^\(?-?[\d,]{3,}(\.\d+)?\)?$"      # grouped / >=3-digit integers
    r"|^\(?-?\d{1,3}\.\d+\)?$"          # small decimals ("0.95", "-1.20")
    r"|^\(?-?0\)?$"                     # explicit zero
)

# a short numeric fragment that can be the broken-off HEAD of a value
_LEAD_FRAGMENT = re.compile(r"^\(?-?\d{1,3}$")

_LINE_TOL = 3       # words within this many pt of top() share a text line
_EDGE_TOL = 12      # right edges within this many pt join one cluster
_SNAP_TOL = 6       # a value must land this close to an anchor to be slotted
_MIN_CLUSTER = 3    # a column anchor needs at least this many aligned values


def split_fused_tokens(toks):
    """Split words holding several parenthesized values fused at ')('."""
    out = []
    for t in toks:
        parts = re.split(r"(?<=\))(?=\()", t["text"])
        if len(parts) > 1 and all(VALUE_TOKEN.match(p) for p in parts):
            x0, x1 = t["x0"], t["x1"]
            per = (x1 - x0) / len(t["text"])
            pos = 0
            for p in parts:
                out.append({"x0": x0 + pos * per,
                            "x1": x0 + (pos + len(p)) * per,
                            "top": t["top"], "text": p})
                pos += len(p)
        else:
            out.append(t)
    return out


def merge_split_values(toks, gap=6):
    """Merge a short leading numeric fragment into a numeric right neighbour
    when they nearly touch and the concatenation reads as one value."""
    toks = sorted(toks, key=lambda t: (t["top"], t["x0"]))
    merged = []
    for t in toks:
        prev = merged[-1] if merged else None
        if (prev is not None
                and abs(prev["top"] - t["top"]) <= _LINE_TOL
                and 0 <= t["x0"] - prev["x1"] <= gap
                and not VALUE_TOKEN.match(prev["text"])
                and _LEAD_FRAGMENT.match(prev["text"])
                and VALUE_TOKEN.match(prev["text"] + t["text"])):
            prev["text"] += t["text"]
            prev["x1"] = t["x1"]
            continue
        merged.append(dict(t))
    return merged


def cluster_right_edges(edges, tol=_EDGE_TOL, min_size=_MIN_CLUSTER):
    """Sorted right edges -> mean x of each cluster with >= min_size members."""
    clusters = []
    for e in sorted(edges):
        if clusters and e - clusters[-1][-1] <= tol:
            clusters[-1].append(e)
        else:
            clusters.append([e])
    return [sum(c) / len(c) for c in clusters if len(c) >= min_size]


def _to_lines(toks):
    lines = []
    for t in sorted(toks, key=lambda t: (t["top"], t["x0"])):
        if lines and abs(lines[-1]["top"] - t["top"]) <= _LINE_TOL:
            lines[-1]["toks"].append(t)
        else:
            lines.append({"top": t["top"], "toks": [t]})
    return lines


def grid_from_words(words):
    """Word dicts (x0, x1, top, text) -> list-of-rows grid
    [label, v_0, .., v_{n-1}], or None when no confident anchors emerge.

    Repairs fused / split tokens, clusters numeric right edges into column
    anchors, snaps values to anchors, folds the rest into the label column.
    Lines without values (title / header bands inside the crop) are kept as
    label-only rows so downstream header detection still sees them.
    """
    toks = [{"x0": w["x0"], "x1": w["x1"], "top": w["top"],
             "text": w["text"].strip()} for w in words if w["text"].strip()]
    toks = split_fused_tokens(toks)
    toks = merge_split_values(toks)

    anchors = cluster_right_edges(
        t["x1"] for t in toks if VALUE_TOKEN.match(t["text"]))
    if len(anchors) < 2:
        return None

    ncols = len(anchors)
    rows = []
    n_value_rows = 0
    for ln in _to_lines(toks):
        vals = [""] * ncols
        label = []
        for t in sorted(ln["toks"], key=lambda t: t["x0"]):
            if VALUE_TOKEN.match(t["text"]):
                d = [abs(a - t["x1"]) for a in anchors]
                ci = d.index(min(d))
                # a collision (two values snapping to one anchor on one
                # line) means the anchor grid is wrong for this line —
                # demote the later token to the label rather than lose it
                if d[ci] <= _SNAP_TOL and not vals[ci]:
                    vals[ci] = t["text"]
                    continue
            label.append(t["text"])
        if any(vals):
            n_value_rows += 1
        rows.append([" ".join(label)] + vals)

    if n_value_rows < 3:
        return None
    return rows


def extract_by_right_edge(plumber_page, bbox=None):
    """Rebuild the table on a pdfplumber page (optionally restricted to a
    camelot bbox, PDF bottom-up coords) by right-edge anchoring. Returns a
    list-of-rows grid or None when not applicable / not confident."""
    page = plumber_page
    if bbox is not None:
        try:
            x0, y0, x1, y1 = bbox
            h = page.height
            page = page.crop((max(0, x0 - 2),
                              max(0, h - max(y0, y1) - 2),
                              min(page.width, x1 + 2),
                              min(h, h - min(y0, y1) + 2)))
        except Exception:
            page = plumber_page
    words = page.extract_words(use_text_flow=False)
    if not words:
        return None
    return grid_from_words(words)
