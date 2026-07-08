"""Shared extraction-quality scorer.

Originally lived only in `batch_extract._extraction_quality`, computed AFTER
`extract_tables` returned and read only by `header_agent.should_fire`. Loop
Spec 1 pulls it out here so `table_extractor.extract_tables` can use the SAME
formula internally to decide whether a low-scoring lattice/stream attempt is
worth retrying with another strategy — one scorer, two consumers, no drift.

Caveat for callers that score a table BEFORE header naming has run (i.e.
`table_extractor`, scoring camelot's raw `table.df`): camelot's raw frame
keeps a plain integer RangeIndex as its columns, so `header_coherence` below
is always 1.0 there (no column is literally named "col"/"value"/"label" yet).
The retry decision still works — `numeric_density` and `fill_rate` vary with
extraction quality — but the header signal only becomes meaningful once
`batch_extract`/`main` score the fully-headered df. This is a property of
reusing one formula across two pipeline stages, not a bug; the weights are
intentionally left untouched (see CLAUDE.md / task spec — do not re-tune).
"""
import re

_CLEAN_NUM = re.compile(r"^\(?-?[\d,]+(\.\d+)?%?\)?$")
# Indian statistical reports carry data VALUES that are neither bare numbers
# nor prose: fiscal-year ranges ("2024-25", "2023-2024"), currency/unit
# amounts ("Rs. 40 lakh", "1,234 crore", "₹ 12.5 Cr"), and dates
# ("01.04.2024", "31/12/2023"). Before this pattern existed, a grants table
# whose every cell was one of these scored numeric_density 0.0 — flagged
# low_quality with pointless strategy retries (observed on DARPG's recurring
# Sevottam-grants annexure, score 0.43 across ~10 reports). These are clean,
# analysis-ready values and count toward numeric_density.
_CLEAN_VALUE = re.compile(
    r"^\(?("
    r"\d{4}\s*[-–]\s*\d{2,4}"                                  # 2024-25 / 2023-2024
    r"|(rs\.?|₹|inr)\s*[\d,]+(\.\d+)?\s*(lakh|lakhs|crore|cr\.?|thousand|k)?"  # Rs. 40 lakh
    r"|[\d,]+(\.\d+)?\s*(lakh|lakhs|crore|cr\.?|%)"            # 1,234 crore / 12 %
    r"|\d{1,2}[./-]\d{1,2}[./-]\d{2,4}"                        # 01.04.2024
    r")\)?$",
    re.IGNORECASE,
)
_COL_GENERIC = re.compile(r"^(col|value|label)(_\d+)?$")
# a cell that is recognizably TEXT content (words, names, Yes/No, URLs) —
# at least two consecutive letters somewhere
_TEXTY = re.compile(r"[A-Za-z]{2,}")
# text-table detection bars (see score_table docstring)
_TEXT_ND_MAX = 0.30
_TEXT_FRAC_MIN = 0.60
_TEXT_FILL_MIN = 0.60

# Below this, a table is "low_quality" and worth retrying with another
# extraction strategy (table_extractor) or handing to the header agent
# (batch_extract / header_agent).
LOW_QUALITY_THRESHOLD = 0.70

# Below this even after exhausting every strategy, a table is a candidate for
# human escalation ("review_needed"), not just a quiet best-effort keep.
REVIEW_THRESHOLD = 0.50


def score_table(df):
    """Score 0.0-1.0 for how cleanly a table was extracted.

    Statistical tables (the default) combine three signals:
    - numeric_density: fraction of cells that are clean numbers
    - header_coherence: fraction of column names that are meaningful (not col_N / value_N)
    - fill_rate: fraction of cells that are non-empty

    TEXT tables (Yes/No status grids, name/link catalogues — e.g. DARPG's
    "Annexure 5 Status of Integration") legitimately have almost no numbers;
    grading them on numeric_density punished perfectly-extracted tables
    (observed: nd=0.17, hc=1.0, fill=0.96 scoring 0.66 -> pointless strategy
    retries that could never help, and a false low_quality flag). When a
    filled table's content is clearly words rather than numbers, swap the
    numeric_density term for text_density (recognizable text content). The
    detection is deliberately conservative — sparse tables (fill < 0.6) and
    mixed tables (nd >= 0.3) keep the statistical formula, so a badly
    fragmented extraction cannot sneak into the text branch just because its
    debris is alphabetic. Kruti-corrupted text scores "texty" here too, but
    corruption is quarantined by validate_table / OCR-recovered upstream —
    a strategy retry never fixes a font-level problem anyway.

    Returns {score, numeric_density, header_coherence, fill_rate, text_mode}."""
    cells = df.values.flatten().tolist()
    total = len(cells)
    if total == 0:
        return {"score": 0.0, "numeric_density": 0.0, "header_coherence": 0.0,
                "fill_rate": 0.0, "text_mode": False}

    non_empty = [str(c).strip() for c in cells if str(c).strip() not in ("", "nan", "None")]
    fill_rate = len(non_empty) / total
    numeric = sum(1 for c in non_empty if _CLEAN_NUM.match(c) or _CLEAN_VALUE.match(c))
    numeric_density = numeric / len(non_empty) if non_empty else 0.0

    cols = [str(c) for c in df.columns]
    meaningful = sum(1 for c in cols if not _COL_GENERIC.match(c.lower()))
    header_coherence = meaningful / len(cols) if cols else 0.0

    texty = sum(1 for c in non_empty if _TEXTY.search(c))
    text_density = texty / len(non_empty) if non_empty else 0.0
    text_mode = (
        numeric_density < _TEXT_ND_MAX
        and text_density >= _TEXT_FRAC_MIN
        and fill_rate >= _TEXT_FILL_MIN
    )

    if text_mode:
        score = round(0.4 * text_density + 0.4 * header_coherence + 0.2 * fill_rate, 3)
    else:
        score = round(0.4 * numeric_density + 0.4 * header_coherence + 0.2 * fill_rate, 3)
    return {
        "score": score,
        "numeric_density": round(numeric_density, 3),
        "header_coherence": round(header_coherence, 3),
        "fill_rate": round(fill_rate, 3),
        "text_mode": text_mode,
    }
