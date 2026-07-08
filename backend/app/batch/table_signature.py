"""Cross-PDF table identity: decide which tables, across many same-source
reports, are "the same table" so they can be stacked into one panel.

This generalizes the within-PDF continuation test (`table_stitcher._continues`,
vertical: rows flowing to the next page) to the cross-PDF case (horizontal in
time: the same table reprinted in successive reports). The shared signal is the
column set; the shared normalization (`normalize_colname`) is reused so the two
notions of "same table" stay consistent.

Grouping is greedy and deterministic: members are visited in (period, title)
order and joined to an existing group when the titles agree or the column sets
overlap strongly. The result is a PROPOSAL — the skill shows it to the analyst
for confirmation before any data is stacked.
"""
import difflib
import hashlib
import re

from backend.app.standardization.table_stitcher import _strong_title, normalize_colname
from backend.app.agents import grouping_agent

_GENERIC = {"col", "value", "label", "nco", ""}
# Rolling-window reports embed the month in column names ("status_oct",
# "receipts_nov"), and the whole set shifts by one month every issue — so the
# SAME table never shares a single column name with itself across periods and
# fragments into per-period singletons. For identity comparison only, strip
# month tokens (and bare years) so "status_oct" and "status_nov" both become
# "status". Data columns are untouched; this affects grouping, not output.
_MONTH_TOKEN = re.compile(
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)(uary|ruary|ch|il|e|y|ust|tember|ober|ember)?"
)
_YEAR_TOKEN = re.compile(r"(19|20)\d{2}")


def _strip_period_tokens(n):
    """Remove month/year tokens from a normalized column name. Normalized
    names have no separators (normalize_colname strips them), so month names
    appear as embedded substrings ("statusoct"). Only strip when the residue
    keeps some identity (>= 2 chars) — a column literally named "jan" or
    "2024" reduces to "" and is treated like a generic name (dropped from
    the identity set) rather than colliding every month-named column into
    one empty key."""
    stripped = _YEAR_TOKEN.sub("", _MONTH_TOKEN.sub("", n))
    return stripped if len(stripped) >= 2 else ""
# loose enough to survive a single added/dropped column year-to-year, strict
# enough not to fuse genuinely different tables of similar width.
_JACCARD = 0.6
# deliberately stricter than the join threshold above: a group is only
# auto-approved (Loop Spec 5) when every join is at least this confident, or
# a strong title match. [0.6, 0.8) still joins the group, just flagged
# needs-review — a human should eyeball the more marginal column overlaps.
_JACCARD_AUTO = 0.8
# edit-distance threshold: normalized names within this ratio are the same column
_FUZZY_COL = 0.88


def _real_colset(columns):
    """Normalized, generic-stripped, period-token-stripped set of a table's
    column names — the table's identity for cross-PDF comparison."""
    out = set()
    for c in columns:
        n = normalize_colname(c)
        # drop the col_N / value_N / label_N fallbacks (carry no identity)
        base = n.rstrip("0123456789")
        if base in _GENERIC or n in _GENERIC:
            continue
        n = _strip_period_tokens(n)
        if n:
            out.add(n)
    return out


def _jaccard(a, b):
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def _fuzzy_jaccard(a, b):
    """Jaccard on fuzzy-matched column sets. Each column in `a` is matched to
    the closest column in `b` (by difflib ratio >= _FUZZY_COL); matched pairs
    count as intersection. This tolerates minor spelling drift across months
    (e.g. 'nodal_officers' vs 'nodal_officer') that exact Jaccard misses."""
    if not a or not b:
        return _jaccard(a, b)
    matched_a, matched_b = set(), set()
    for ca in a:
        best, best_r = None, 0.0
        for cb in b:
            if cb in matched_b:
                continue
            r = difflib.SequenceMatcher(None, ca, cb).ratio()
            if r > best_r:
                best, best_r = cb, r
        if best is not None and best_r >= _FUZZY_COL:
            matched_a.add(ca)
            matched_b.add(best)
    intersection = len(matched_a)
    # Union of a fuzzy-matched pairing is |A| + |B| - M (M = matched pairs),
    # NOT len(a | b) - M. len(a | b) already dedupes EXACT-string matches
    # (they collapse to one element in the set union), so subtracting M again
    # double-counts them and can push the score above 1.0 whenever any matched
    # pair happens to be an exact match rather than a fuzzy near-match (e.g. a
    # near-subset column set where most columns are identical strings).
    # |A| + |B| - M is correct for both cases: it counts every element once
    # per set, then removes the double-count for each matched pair regardless
    # of whether that pair was an exact or a fuzzy match.
    union = len(a) + len(b) - intersection
    return intersection / max(union, 1)


# Rolling-window reports embed the covered range in the TITLE too:
# "Annexure 1.3 Maximum Number of Receipts Jan to April," becomes
# "... January to August," five issues later — same table, different strong
# title every month, so title-based grouping splits one panel into a group
# per issue (observed: 12 groups for one Annexure 1.3 across 44 reports).
# Strip a trailing month/range/year suffix from the RAW title before
# normalizing. Anchored at the end so month names in the middle of a real
# title ("... in March quarter review of X") are never touched.
_MON = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_TITLE_DATE_SUFFIX = re.compile(
    rf"[\s,–-]*{_MON}(?:\s*(?:to|till|[-–])\s*{_MON})?[\s,]*(?:\d{{4}})?[\s,]*$",
    re.IGNORECASE,
)


def _norm_title(name):
    if not name:
        return ""
    return normalize_colname(_TITLE_DATE_SUFFIX.sub("", str(name)))


def _titles_same(a, b):
    """Strong-title identity, tolerant of the two noise modes real reports
    show: a trailing date-range suffix (stripped in _norm_title) and title
    TRUNCATION by the extractor ("Annexure 1.3 Maximum Number" vs the full
    "Annexure 1.3 Maximum Number of Receipts"). A prefix match only counts
    when the first divergent character is a letter — a digit there means the
    shorter title's trailing NUMBER continues ("Annexure 1" vs "Annexure
    1.3" normalizes to annexure1 / annexure13), which is a different
    annexure, never a truncation."""
    na, nb = _norm_title(a), _norm_title(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    short, long = (na, nb) if len(na) <= len(nb) else (nb, na)
    return len(short) >= 8 and long.startswith(short) and long[len(short)].isalpha()


def _matches(member, group):
    """Can `member` join `group`? Strong matching titles win outright; a strong
    title conflict blocks; otherwise fall back to column-set overlap.

    Returns (matched, reason). `reason` records HOW the join happened, so
    group_tables() can later grade the group's confidence (Loop Spec 5):
    "title", "jaccard_strong" (>= _JACCARD_AUTO), "jaccard_weak"
    ([_JACCARD, _JACCARD_AUTO)), or "agent" (0.40-0.59 band, agent-confirmed).
    """
    m_strong = _strong_title(member["name"])
    g_strong = _strong_title(group["title"])
    if m_strong and g_strong:
        if _titles_same(member["name"], group["title"]):
            return True, "title"
        return False, None
    # both untitled (or weak): cluster on column content (fuzzy-aware)
    j = _fuzzy_jaccard(member["_colset"], group["_colset"])
    if j >= _JACCARD_AUTO:
        return True, "jaccard_strong"
    if j >= _JACCARD:
        return True, "jaccard_weak"
    # uncertain zone — ask the grouping agent (cached; costs 0 tokens on re-run)
    if grouping_agent.should_fire(j):
        rep = group["members"][0] if group["members"] else None
        verdict = grouping_agent.are_same_table(
            list(member["_colset"]), member["period"],
            list(rep["_colset"] if rep else group["_colset"]),
            rep["period"] if rep else "",
            j,
        )
        if verdict is True:
            return True, "agent"
    return False, None


def _signature(group):
    """A stable id for a group, for filenames / cross-run reference."""
    if _strong_title(group["title"]):
        key = "title:" + _norm_title(group["title"])
    else:
        key = "cols:" + group["archetype"] + ":" + "|".join(sorted(group["_colset"]))
    return hashlib.sha1(key.encode()).hexdigest()[:10]


def group_tables(manifests):
    """Greedy cross-PDF clustering. `manifests` is the list of per-PDF manifest
    dicts from batch_extract. Returns a list of group dicts:

      {signature, label, archetype, periods, columns_union, confidence, members:[...]}

    Each member: {pdf, stem, period, table_id, name, rows, cols, columns, csv}.

    `confidence` (Loop Spec 5) is "auto" when every member that joined the
    group did so via a strong title match or a >= _JACCARD_AUTO fuzzy Jaccard
    overlap — the analyst can skim these. It is "review" when any join came
    from the 0.40-0.59 agent-assisted band, the [_JACCARD, _JACCARD_AUTO)
    weaker overlap, or the singleton rescue pass below — those need a human
    look. This is purely a display hint on the same PROPOSAL: re-running this
    function (Step 1) re-derives everything from scratch, so there is no
    stale "auto" verdict to worry about — see the module docstring.
    """
    members = []
    for mani in manifests:
        for t in mani["tables"]:
            members.append({
                "pdf": mani["pdf"], "stem": mani["stem"], "period": mani["period"],
                "table_id": t["table_id"], "name": t.get("name"),
                "rows": t["rows"], "cols": t["cols"], "columns": t["columns"],
                "archetype": t.get("archetype", "statistical"), "csv": t["csv"],
                "_colset": _real_colset(t["columns"]),
            })
    # deterministic visit order
    members.sort(key=lambda m: (m["period"], str(m["name"] or ""), m["stem"], m["table_id"]))

    groups = []
    for m in members:
        placed, join_reason = None, None
        for g in groups:
            if g["archetype"] == m["archetype"]:
                matched, reason = _matches(m, g)
                if matched:
                    placed, join_reason = g, reason
                    break
        if placed is None:
            placed = {"title": m["name"], "archetype": m["archetype"],
                      "_colset": set(m["_colset"]), "members": [], "_joins": []}
            groups.append(placed)
        else:
            placed["_joins"].append(join_reason)
        placed["members"].append(m)
        # grow the group's column set so later members can still match after a
        # year quietly adds a column
        placed["_colset"] |= m["_colset"]
        if not _strong_title(placed["title"]) and _strong_title(m["name"]):
            placed["title"] = m["name"]

    # Rescue pass: singletons that share a strong column-set overlap with a
    # multi-period group get absorbed into that group, even when their title
    # conflicted. This recovers tables whose Annexure number drifted (e.g.
    # "Annexure 1.3" vs "Annexure 1 Performance") but whose columns match.
    # Only fires when the singleton colset is a near-subset of the target group
    # (fuzzy Jaccard of singleton ÷ group >= _JACCARD, using the singleton as
    # the smaller set so we don't penalise the group for having extra columns).
    multi = [g for g in groups if len(g["members"]) >= 2]
    singles = [g for g in groups if len(g["members"]) == 1]
    for sg in singles[:]:
        m = sg["members"][0]
        if not m["_colset"]:
            continue
        best_g, best_score = None, 0.0
        for mg in multi:
            if mg["archetype"] != sg["archetype"]:
                continue
            # directional: how much of the singleton is covered by the group
            score = _fuzzy_jaccard(m["_colset"], mg["_colset"])
            if score > best_score:
                best_g, best_score = mg, score
        if best_g is not None and best_score >= _JACCARD:
            best_g["members"].append(m)
            best_g["_colset"] |= m["_colset"]
            # rescued joins are never auto-approved, however strong the score —
            # the title conflict that stranded this singleton in the first
            # place is exactly the kind of thing a human should eyeball.
            best_g["_joins"].append("rescue")
            groups.remove(sg)

    # finalize: sort members by period, build labels + union, drop scratch keys
    _AUTO_REASONS = {"title", "jaccard_strong"}
    out = []
    for g in groups:
        g["members"].sort(key=lambda m: (m["period"], m["stem"]))
        cols_union = []
        seen = set()
        for mem in g["members"]:
            for c in mem["columns"]:
                if c not in seen:
                    seen.add(c)
                    cols_union.append(c)
            mem.pop("_colset", None)
        periods = sorted({mem["period"] for mem in g["members"]})
        label = g["title"] or f"({g['members'][0]['cols']}-col {g['archetype']} table)"
        # a group with no joins at all (singleton, nothing merged) has nothing
        # to second-guess, so it is trivially "auto".
        confidence = "auto" if all(r in _AUTO_REASONS for r in g["_joins"]) else "review"
        out.append({
            "signature": _signature(g),
            "label": label,
            "archetype": g["archetype"],
            "periods": periods,
            "n_periods": len(periods),
            "n_members": len(g["members"]),
            "columns_union": cols_union,
            "confidence": confidence,
            "members": g["members"],
        })
    # most-covered panels first — the analyst cares about long series
    out.sort(key=lambda g: (-g["n_periods"], -g["n_members"], g["label"]))
    return out
