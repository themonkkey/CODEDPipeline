"""Rename Agent — confirms or rejects combined/split schema change hypotheses.

schema_diff.py flags transitions where N columns vanish as M appear (combined
or split). These are heuristic low-confidence flags — the agent elevates them
to confirmed renames (or dismisses them) so build_panel can apply the mapping.

Input: the before/after column lists at a period boundary (~60 input tokens).
Output: JSON {"confirmed": true/false, "map": {"old_col": "new_col", ...}}
  - confirmed=true + map → apply renames in panel_builder before concat.
  - confirmed=false      → leave as NaN alignment (current behaviour).

Max ~70 output tokens. Cached by prompt hash.
"""

import json

from backend.app.agents import base

_SYSTEM = (
    "You are a schema-change classifier for Indian government statistical PDFs. "
    "Given columns that disappeared and columns that appeared at a period boundary, "
    "decide if this is a real rename/merge or unrelated columns. "
    "Return ONLY JSON: {\"confirmed\": true/false, \"map\": {\"old\": \"new\"}}. "
    "map may be empty. Max 70 tokens output."
)


def confirm_rename(
    dropped: list[str],
    added: list[str],
    period_before: str,
    period_after: str,
) -> dict[str, str]:
    """Return a rename map {old_col: new_col} if the agent confirms, else {}."""
    if not dropped or not added:
        return {}

    prompt = (
        f"Period boundary: {period_before} → {period_after}\n"
        f"Columns dropped: {', '.join(dropped)}\n"
        f"Columns added:   {', '.join(added)}\n"
        "Is this a rename/merge? Return JSON map."
    )

    raw = base.call("rename_agent", prompt, system=_SYSTEM)
    if not raw:
        return {}

    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start == -1 or end == 0:
            return {}
        result = json.loads(raw[start:end])
        if not result.get("confirmed"):
            return {}
        rename_map = result.get("map", {})
        # sanitise: only valid string-to-string entries
        return {str(k): str(v) for k, v in rename_map.items()
                if isinstance(k, str) and isinstance(v, str)}
    except Exception:
        return {}
