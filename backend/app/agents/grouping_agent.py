"""Grouping Agent — resolves ambiguous cross-period table identity.

The deterministic clusterer in table_signature.py uses Jaccard ≥ 0.6 as a
hard threshold. Tables in the 0.4–0.6 zone are uncertain: the column sets
overlap but not strongly. This agent breaks the tie.

Input: the two column sets (names only — no data). ~80 input tokens.
Output: JSON {"same": true/false, "reason": "..."} (~30 tokens).

Fires only for uncertain pairs. Results are cached so a re-run with the same
PDFs costs zero additional tokens.
"""

import json

from backend.app.agents import base

_SYSTEM = (
    "You are a table-identity classifier for Indian government statistical PDFs. "
    "Given two sets of column names from the same report series, decide if they "
    "are the same table printed in different months. "
    "Return ONLY JSON: {\"same\": true/false, \"reason\": \"<10 words>\"}. "
    "Max 30 tokens output."
)

_UNCERTAIN_LOW  = 0.40
_UNCERTAIN_HIGH = 0.60


def should_fire(jaccard: float) -> bool:
    return _UNCERTAIN_LOW <= jaccard < _UNCERTAIN_HIGH


def are_same_table(cols_a: list[str], period_a: str,
                   cols_b: list[str], period_b: str,
                   jaccard: float) -> bool | None:
    """Return True/False if the agent is confident, None if it can't decide or fails."""
    if not should_fire(jaccard):
        return None

    prompt = (
        f"Table A ({period_a}): {', '.join(cols_a)}\n"
        f"Table B ({period_b}): {', '.join(cols_b)}\n"
        f"Column overlap (Jaccard): {jaccard:.2f}\n"
        "Same table across months?"
    )

    raw = base.call("grouping_agent", prompt, system=_SYSTEM)
    if not raw:
        return None

    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        result = json.loads(raw[start:end])
        return bool(result.get("same"))
    except Exception:
        return None
