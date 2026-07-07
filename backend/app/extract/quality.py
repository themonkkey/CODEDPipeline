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
_COL_GENERIC = re.compile(r"^(col|value|label)(_\d+)?$")

# Below this, a table is "low_quality" and worth retrying with another
# extraction strategy (table_extractor) or handing to the header agent
# (batch_extract / header_agent).
LOW_QUALITY_THRESHOLD = 0.70

# Below this even after exhausting every strategy, a table is a candidate for
# human escalation ("review_needed"), not just a quiet best-effort keep.
REVIEW_THRESHOLD = 0.50


def score_table(df):
    """Score 0.0-1.0 for how cleanly a table was extracted.

    Combines three signals:
    - numeric_density: fraction of cells that are clean numbers (higher = cleaner stats table)
    - header_coherence: fraction of column names that are meaningful (not col_N / value_N)
    - fill_rate: fraction of cells that are non-empty

    Returns a dict {score, numeric_density, header_coherence, fill_rate}."""
    cells = df.values.flatten().tolist()
    total = len(cells)
    if total == 0:
        return {"score": 0.0, "numeric_density": 0.0, "header_coherence": 0.0, "fill_rate": 0.0}

    non_empty = [str(c).strip() for c in cells if str(c).strip() not in ("", "nan", "None")]
    fill_rate = len(non_empty) / total
    numeric = sum(1 for c in non_empty if _CLEAN_NUM.match(c))
    numeric_density = numeric / len(non_empty) if non_empty else 0.0

    cols = [str(c) for c in df.columns]
    meaningful = sum(1 for c in cols if not _COL_GENERIC.match(c.lower()))
    header_coherence = meaningful / len(cols) if cols else 0.0

    score = round(0.4 * numeric_density + 0.4 * header_coherence + 0.2 * fill_rate, 3)
    return {
        "score": score,
        "numeric_density": round(numeric_density, 3),
        "header_coherence": round(header_coherence, 3),
        "fill_rate": round(fill_rate, 3),
    }
