"""Header Agent — infers real column names from raw cell content.

Fires only when extraction_quality.score < 0.70, meaning the deterministic
header detector produced low-confidence names (col, value, code fallbacks).

Input to the LLM: the table's sample rows as CSV + the current (bad) names.
Callers control how many rows to send (3 normally; batch_extract's
verify-and-retry loop sends 5 on the one retry it allows, after the 3-row
first attempt's rename fails to actually improve the score).
Output: a JSON mapping {bad_name: real_name} covering only the columns that
need renaming. Columns the agent doesn't mention are left as-is.

Token budget: ~150 input, ~60 output. Cached by (pdf_hash, table_id).
"""

import json

from backend.app.agents import base

_SYSTEM = (
    "You are a column-name inference engine for Indian government statistical PDFs. "
    "Given bad column names and sample rows, return ONLY a JSON object mapping "
    "bad names to real names. Omit columns that look correct. "
    "Use snake_case. Max 60 tokens output."
)

_QUALITY_THRESHOLD = 0.70


def should_fire(table_meta: dict) -> bool:
    score = table_meta.get("extraction_quality", {}).get("score", 1.0)
    return score < _QUALITY_THRESHOLD


def fix_headers(table_meta: dict, sample_rows: list[dict]) -> dict[str, str]:
    """Return a rename map {original_col: better_col}. Empty dict if agent skips/fails.

    Sends every row the caller passes in (no internal truncation) — the
    caller decides the row budget, e.g. `df.head(3)` for a first attempt or
    `df.head(5)` for batch_extract's richer retry prompt.
    """
    if not should_fire(table_meta):
        return {}

    cols = table_meta.get("columns", [])
    if not cols or not sample_rows:
        return {}

    # build a minimal prompt — column names + whatever rows the caller sent
    rows_text = "\n".join(
        ", ".join(f"{k}={v}" for k, v in row.items() if k in cols)
        for row in sample_rows
    )
    prompt = (
        f"Columns: {', '.join(cols)}\n"
        f"Sample rows:\n{rows_text}\n\n"
        "Return JSON rename map for bad column names only."
    )

    raw = base.call("header_agent", prompt, system=_SYSTEM)
    if not raw:
        return {}

    try:
        # extract JSON object from response
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start == -1 or end == 0:
            return {}
        rename_map = json.loads(raw[start:end])
        # sanitise: only keep entries where both keys are strings
        return {str(k): str(v) for k, v in rename_map.items()
                if k in cols and isinstance(v, str)}
    except Exception:
        return {}
