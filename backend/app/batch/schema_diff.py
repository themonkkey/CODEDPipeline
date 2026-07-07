"""Detect and document how a table's schema changes across reporting periods.

Stacking the same table across years is only safe if you KNOW what changed:
a column added, dropped, renamed, two columns combined into one, or one column
bifurcated into two. Silently `concat`-ing columns that look alike but mean
different things is the cardinal sin this module exists to prevent.

It never mutates data. It produces (a) a canonical variable map — how each
period's columns align to one unified set — and (b) a changelog of every
detected change, each tagged with a confidence level. `combined` / `split` are
heuristic and deliberately low-confidence: they are surfaced for the analyst to
confirm, never applied automatically.
"""
import difflib

from backend.app.standardization.table_stitcher import normalize_colname
from backend.app.agents import rename_agent

_FUZZY_RENAME = 0.82   # difflib ratio over normalized names to call a rename
_SAME_COL_EDIT = 2     # Levenshtein-like: names differing by <=2 chars are identical (plural drift)


def _norm(c):
    return normalize_colname(c)


def _same_col(a, b):
    """True when two normalized column names are the same variable.
    Tolerates plural/singular drift and minor OCR noise (<=2 char difference)
    so 'nodal_officers_accounts' and 'nodal_officer_accounts' are not reported
    as renames."""
    na, nb = _norm(a), _norm(b)
    if na == nb:
        return True
    # edit distance approximation via difflib ratio:
    # ratio = 2*M / T where M=matches, T=total chars
    # for short strings, ratio < 1 iff chars differ; <=2 edits ~= ratio >= 0.9
    r = difflib.SequenceMatcher(None, na, nb).ratio()
    return r >= 0.90


def _period_columns(group):
    """Ordered (period -> column list). When a period has several member tables
    in the group, take the widest (most complete) one."""
    by_period = {}
    for m in group["members"]:
        cur = by_period.get(m["period"])
        if cur is None or len(m["columns"]) > len(cur):
            by_period[m["period"]] = m["columns"]
    return [(p, by_period[p]) for p in sorted(by_period)]


def align(group):
    """Align every period's columns to a canonical variable set.

    Returns:
      canon      — ordered list of canonical variable display names (first-seen)
      presence   — {canon_display: set(periods present)}
      colmap     — {period: {original_column: canon_display}}
      renames    — list of (period, old_display, new_display, ratio)
    """
    periods = _period_columns(group)
    canon = []                 # ordered display names
    canon_norm = []            # parallel normalized keys
    presence = {}
    colmap = {}
    renames = []

    for pi, (period, cols) in enumerate(periods):
        colmap[period] = {}
        used_canon = set()
        # pass 1: exact OR near-identical normalized-name match (tolerates plural drift)
        leftover = []
        for c in cols:
            nc = _norm(c)
            hit = next((i for i, k in enumerate(canon_norm)
                        if _same_col(k, nc) and i not in used_canon), None)
            if hit is not None:
                used_canon.add(hit)
                disp = canon[hit]
                colmap[period][c] = disp
                presence.setdefault(disp, set()).add(period)
            else:
                leftover.append(c)

        # pass 2: fuzzy match leftovers to canonical vars not seen THIS period
        # and absent from this period so far (likely renames of an existing var)
        absent_idx = [i for i in range(len(canon)) if i not in used_canon
                      and period not in presence.get(canon[i], set())]
        still = []
        for c in leftover:
            nc = _norm(c)
            best, best_r = None, 0.0
            for i in absent_idx:
                if i in used_canon:
                    continue
                r = difflib.SequenceMatcher(None, nc, canon_norm[i]).ratio()
                if r > best_r:
                    best, best_r = i, r
            if best is not None and best_r >= _FUZZY_RENAME and pi > 0:
                used_canon.add(best)
                disp = canon[best]
                colmap[period][c] = disp
                presence.setdefault(disp, set()).add(period)
                if _norm(c) != _norm(disp):
                    renames.append((period, disp, c, round(best_r, 2)))
            else:
                still.append(c)

        # pass 3: genuinely new columns -> new canonical vars
        for c in still:
            canon.append(c)
            canon_norm.append(_norm(c))
            colmap[period][c] = c
            presence.setdefault(c, set()).add(period)

    return canon, presence, colmap, renames


def diff(group):
    """Full schema changelog for one panel group.

    Returns {signature, label, periods, canonical, changes} where changes is a
    list of {period, kind, variables, confidence, note}, kind in
    {added, dropped, renamed, combined, split}."""
    periods = [p for p, _ in _period_columns(group)]
    canon, presence, colmap, renames = align(group)
    first_period = periods[0] if periods else None
    changes = []

    # renamed (recorded during alignment) — medium confidence (name-similarity)
    for period, old, new, ratio in renames:
        changes.append({
            "period": period, "kind": "renamed",
            "variables": [old, new], "confidence": "medium",
            "note": f"'{old}' -> '{new}' (name similarity {ratio})",
        })

    renamed_targets = {new for _, _, new, _ in renames}

    # added / dropped — high confidence (pure presence)
    for var in canon:
        present = sorted(presence.get(var, set()))
        if not present:
            continue
        if present[0] != first_period and var not in renamed_targets:
            changes.append({
                "period": present[0], "kind": "added",
                "variables": [var], "confidence": "high",
                "note": f"first appears in {present[0]}",
            })
        # dropped: contiguous from start then gone before the end
        if present[-1] != periods[-1]:
            after = periods[periods.index(present[-1]) + 1]
            changes.append({
                "period": after, "kind": "dropped",
                "variables": [var], "confidence": "high",
                "note": f"last present in {present[-1]}, absent from {after}",
            })

    # combined / split — heuristic, low confidence. Look per transition at the
    # residual adds/drops NOT explained by a rename.
    for i in range(1, len(periods)):
        prev, cur = periods[i - 1], periods[i]
        dropped = [v for v in canon
                   if prev in presence.get(v, set())
                   and cur not in presence.get(v, set())
                   and v not in renamed_targets]
        added = [v for v in canon
                 if cur in presence.get(v, set())
                 and prev not in presence.get(v, set())
                 and v not in renamed_targets]
        if len(dropped) >= 2 and len(added) == 1:
            # Ask the rename agent to confirm before flagging as low-confidence
            confirmed_map = rename_agent.confirm_rename(dropped, added, prev, cur)
            if confirmed_map:
                for old_col, new_col in confirmed_map.items():
                    changes.append({
                        "period": cur, "kind": "renamed",
                        "variables": [old_col, new_col], "confidence": "medium",
                        "note": f"agent-confirmed rename: '{old_col}' -> '{new_col}'",
                        "agent_rename_map": confirmed_map,
                    })
            else:
                changes.append({
                    "period": cur, "kind": "combined",
                    "variables": dropped + ["->", added[0]], "confidence": "low",
                    "note": f"{len(dropped)} variables vanish as 1 appears; verify it is a sum/merge",
                })
        elif len(dropped) == 1 and len(added) >= 2:
            confirmed_map = rename_agent.confirm_rename(dropped, added, prev, cur)
            if confirmed_map:
                for old_col, new_col in confirmed_map.items():
                    changes.append({
                        "period": cur, "kind": "renamed",
                        "variables": [old_col, new_col], "confidence": "medium",
                        "note": f"agent-confirmed rename: '{old_col}' -> '{new_col}'",
                        "agent_rename_map": confirmed_map,
                    })
            else:
                changes.append({
                    "period": cur, "kind": "split",
                    "variables": [dropped[0], "->"] + added, "confidence": "low",
                    "note": f"1 variable vanishes as {len(added)} appear; verify the bifurcation",
                })

    changes.sort(key=lambda c: (str(c["period"]),
                                ["added", "dropped", "renamed", "combined", "split"].index(c["kind"])))
    return {
        "signature": group["signature"],
        "label": group["label"],
        "periods": periods,
        "canonical": canon,
        "colmap": colmap,
        "changes": changes,
    }
