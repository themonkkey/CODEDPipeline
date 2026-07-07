"""Assemble a confirmed table group into one long-format panel dataframe.

Each period's table is relabelled to the canonical variable set (from
schema_diff.align), tagged with `period` + `source_file`, and stacked. A
variable absent in a period is NaN there — never silently dropped or
back-filled. The canonical column order is preserved so the panel reads
consistently across periods.

Fix 5: entity key columns (ministry/state names) are fuzzy-normalised before
concat so "Dept. of Personnel" and "Department of Personnel" match to the same
row key and don't fragment the entity dimension.

Fix 7: after stacking, drop columns that are entirely NaN (header extraction
artifacts — col, code, value fallbacks that never carried data).

Fix 8: cast columns whose values are ≥80 % numeric to float so downstream
pivot / Excel formulas work without manual conversion.

Fix 9: inject a clean `state_name` column (common-prefix stripped) next to any
`name_state` column so analysts can groupby without regex.

Fix 10: insert a MISSING sentinel row for each period in the group's declared
period range that is absent from the stacked data, making gaps explicit.
"""
import difflib
import os
import re

import pandas as pd

from backend.app.batch.schema_diff import diff

# prefixes stripped to produce a clean state/entity name (longest first so
# the more-specific "Union Territory of" strip fires before "of").
_STATE_PREFIXES = re.compile(
    r"^(Government\s+of\s+Union\s+Territory\s+of|Government\s+of)\s+",
    re.IGNORECASE,
)
# columns that are identity/grouping keys — never coerced to numeric
_TEXT_COLS = frozenset({
    "period", "source_file", "name_state", "state_name",
    "label", "category", "level", "portal_type",
    "grievance_state_portal_forward", "grievance_state_portal_reverse",
    "link_state_portal",
})
_NUMERIC_THRESHOLD = 0.80   # fraction of non-null values that must parse as number


def _strip_state_prefix(val):
    if not isinstance(val, str):
        return val
    return _STATE_PREFIXES.sub("", val.strip()).strip()


def _inject_state_name(panel):
    """Insert a clean `state_name` column immediately after `name_state`."""
    if "name_state" not in panel.columns:
        return panel
    idx = panel.columns.get_loc("name_state")
    clean = panel["name_state"].map(_strip_state_prefix)
    panel.insert(idx + 1, "state_name", clean)
    return panel


def _drop_all_nan_cols(panel):
    """Drop columns that are 100 % NaN — header extraction artifacts."""
    all_nan = [c for c in panel.columns if panel[c].isna().all()]
    return panel.drop(columns=all_nan) if all_nan else panel


def _coerce_numeric_cols(panel):
    """Cast columns whose values are ≥ _NUMERIC_THRESHOLD parseable as number."""
    for col in panel.columns:
        if col in _TEXT_COLS:
            continue
        series = panel[col].replace("", pd.NA).dropna()
        if len(series) == 0:
            continue
        numeric = pd.to_numeric(series, errors="coerce")
        hit_rate = numeric.notna().sum() / len(series)
        if hit_rate >= _NUMERIC_THRESHOLD:
            panel[col] = pd.to_numeric(panel[col], errors="coerce")
    return panel


def _add_missing_sentinels(panel, group_periods):
    """Insert a MISSING sentinel row for each period declared in the group
    that has no actual rows after stacking. Makes gaps explicit in the output
    instead of silently absent — critical for time-series correctness."""
    present = set(panel["period"].dropna().unique())
    missing = [p for p in sorted(group_periods) if p not in present]
    if not missing:
        return panel
    sentinel_rows = []
    for p in missing:
        row = {c: pd.NA for c in panel.columns}
        row["period"] = p
        row["source_file"] = "MISSING"
        sentinel_rows.append(row)
    sentinels = pd.DataFrame(sentinel_rows, columns=panel.columns)
    return pd.concat([panel, sentinels], ignore_index=True)

# canonical stopwords to strip when normalising entity names for comparison
_STOP = re.compile(
    r"\b(of|the|and|for|in|on|to|a|an|department|ministry|dept|min)\b",
    re.IGNORECASE,
)
_NONALPHA = re.compile(r"[^a-z0-9\s]")


def _norm_entity(val):
    """Normalised form of a ministry/state/organisation name for fuzzy matching."""
    s = str(val).lower()
    s = _NONALPHA.sub(" ", s)
    s = _STOP.sub(" ", s)
    return " ".join(s.split())


def _is_entity_col(series):
    """True if this column looks like an entity key (ministry/state names):
    majority text, long average length, not a numeric series."""
    vals = [str(v).strip() for v in series if str(v).strip() not in ("", "nan")]
    if not vals:
        return False
    avg_len = sum(len(v) for v in vals) / len(vals)
    text_frac = sum(1 for v in vals if re.search(r"[A-Za-z]{3,}", v)) / len(vals)
    return avg_len > 8 and text_frac > 0.6


def _fuzzy_align_entity_col(frames, col):
    """Across all frames, build a canonical label map for an entity column so
    'Ministry of Railways' and 'Railways Ministry' map to the same key.

    Strategy: collect all unique normalised values; greedily cluster values
    within difflib ratio >= 0.82 and pick the longest raw label as canonical.
    Then replace each frame's column values with the canonical form."""
    # collect all raw values
    raw_vals = []
    for df in frames:
        if col in df.columns:
            raw_vals.extend(df[col].dropna().unique().tolist())
    unique = list(dict.fromkeys(str(v) for v in raw_vals))
    if len(unique) <= 1:
        return frames

    # greedy canonical clustering on normalised forms
    norm_to_canon = {}
    clusters = []  # list of (canonical_raw, set of normed aliases)
    for raw in unique:
        nv = _norm_entity(raw)
        matched = None
        for i, (canon_raw, aliases) in enumerate(clusters):
            rep_norm = _norm_entity(canon_raw)
            r = difflib.SequenceMatcher(None, nv, rep_norm).ratio()
            if r >= 0.82:
                matched = i
                break
        if matched is None:
            clusters.append((raw, {nv}))
            norm_to_canon[nv] = raw
        else:
            canon_raw, aliases = clusters[matched]
            aliases.add(nv)
            # prefer longer raw label as canonical
            if len(raw) > len(canon_raw):
                clusters[matched] = (raw, aliases)
                for alias in aliases:
                    norm_to_canon[alias] = raw
            else:
                norm_to_canon[nv] = clusters[matched][0]

    # apply map to every frame
    out = []
    for df in frames:
        df = df.copy()
        if col in df.columns:
            df[col] = df[col].apply(
                lambda v: norm_to_canon.get(_norm_entity(str(v)), str(v))
                if pd.notna(v) and str(v).strip() not in ("", "nan") else v
            )
        out.append(df)
    return out


def _numeric_outlier_flags(panel, canon):
    """Fix 6: per-column outlier annotation. For each numeric canonical column,
    flag rows whose value is >4 std deviations from the column mean with an
    adjacent '<col>_flag' column containing 'outlier'. Analyst-visible only;
    never modifies the data values."""
    flag_cols = {}
    for col in canon:
        if col not in panel.columns:
            continue
        numeric = pd.to_numeric(panel[col], errors="coerce")
        if numeric.isna().all():
            continue
        mean, std = numeric.mean(), numeric.std()
        if std == 0 or pd.isna(std):
            continue
        z = (numeric - mean).abs() / std
        flags = z.map(lambda v: "outlier" if pd.notna(v) and v > 4 else "")
        if flags.any():
            flag_cols[col + "_flag"] = flags
    return flag_cols


def build_panel(group, workdir):
    """Return (panel_df, diff_result). panel_df columns:
    [period, source_file, <canonical variables in first-seen order>]."""
    d = diff(group)
    canon = d["canonical"]
    colmap = d["colmap"]

    frames = []
    for m in group["members"]:
        csv_path = os.path.join(workdir, m["csv"])
        if not os.path.exists(csv_path):
            continue
        df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
        period_map = colmap.get(m["period"], {})
        new_cols = []
        for c in df.columns:
            new_cols.append(period_map.get(str(c), str(c)))
        df.columns = new_cols
        df = df.loc[:, ~pd.Index(df.columns).duplicated()]
        df.insert(0, "source_file", m["stem"])
        df.insert(0, "period", m["period"])
        frames.append(df)

    if not frames:
        return pd.DataFrame(columns=["period", "source_file"] + canon), d

    # Fix 5: fuzzy-align entity key columns across frames before concat
    all_cols = {c for df in frames for c in df.columns}
    for col in all_cols:
        if col in ("period", "source_file"):
            continue
        sample = pd.concat([df[[col]] for df in frames if col in df.columns], ignore_index=True)
        if _is_entity_col(sample[col]):
            frames = _fuzzy_align_entity_col(frames, col)

    panel = pd.concat(frames, ignore_index=True)
    ordered = ["period", "source_file"] + [c for c in canon if c in panel.columns]
    extra = [c for c in panel.columns if c not in ordered]
    panel = panel[ordered + extra]

    # Fix 6: append outlier flag columns after canonical vars
    flag_cols = _numeric_outlier_flags(panel, canon)
    for fcol, fseries in flag_cols.items():
        panel[fcol] = fseries.values

    # Fix 7: drop columns that are entirely NaN (header artifact fallbacks)
    panel = _drop_all_nan_cols(panel)

    # Fix 8: cast numeric columns so Excel pivot tables work without conversion
    panel = _coerce_numeric_cols(panel)

    # Fix 9: clean state_name column next to name_state
    panel = _inject_state_name(panel)

    # Fix 10: explicit MISSING sentinels for absent periods
    all_periods = sorted({m["period"] for m in group["members"]})
    panel = _add_missing_sentinels(panel, all_periods)

    return panel, d
