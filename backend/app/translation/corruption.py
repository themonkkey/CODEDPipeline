"""
Detect tables whose text is corrupt and un-translatable.

Two failure modes, both font-encoding corruption that no text-layer rule can
fix (the correct characters only exist as rendered glyphs):

  * legacy Kruti-Dev / Chanakya fonts — Camelot reads the font's Latin
    codepoints, so cells arrive as ASCII soup ("ftyk", "';ksiqj").
  * Devanagari with mangled conjuncts — the glyphs round-trip as Unicode
    Devanagari but the cluster order is broken, so it cannot be translated.

`corruption_score` is shared by the OCR-recovery step (which re-reads the
rendered glyphs) and the validator quarantine (which drops what OCR could not
recover), so both judge corruption the same way.
"""

import re

from backend.app.translation.kruti_dev import looks_kruti

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_MISSING = ("", "nan", "none")


def _cell_kind(value):
    """Classify one cell: None (ignore / blank), 'deva', 'kruti', or '' (clean)."""
    s = str(value).strip()
    if s.lower() in _MISSING:
        return None
    if _DEVANAGARI.search(s):
        return "deva"
    tokens = [t for t in s.split() if re.search(r"[A-Za-z]", t)]
    if tokens and sum(1 for t in tokens if looks_kruti(t)) / len(tokens) >= 0.5:
        return "kruti"
    return ""


def corruption_score(df):
    """(fraction of populated text cells that are corrupt, dominant kind).

    Numeric cells carry no script and are ignored, so a numbers-heavy table with
    a few corrupt labels scores low — it is mostly usable. Returns (0.0, None)
    for an empty / all-numeric / all-clean frame."""
    if df is None or df.empty:
        return 0.0, None
    deva = kruti = total = 0
    for row in df.values.tolist():
        for v in row:
            kind = _cell_kind(v)
            if kind is None:
                continue
            total += 1
            if kind == "deva":
                deva += 1
            elif kind == "kruti":
                kruti += 1
    if not total:
        return 0.0, None
    score = (deva + kruti) / total
    dominant = "deva" if deva >= kruti else "kruti"
    return score, (dominant if (deva or kruti) else None)


def is_corrupt(df, thresh=0.3):
    """True when a large share of the table's text cells are corrupt."""
    score, kind = corruption_score(df)
    return score >= thresh, kind
