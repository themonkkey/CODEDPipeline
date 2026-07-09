"""
Stitch multi-page continuation tables back together.

Long report tables (PLFS state matrices, DARPG GRAI grids) are printed
across consecutive pages, repeating the same title and/or the same
header row on every page. Camelot returns one table per page; this
module merges those fragments into one continuous table.
"""

import re

import pandas as pd


def _named_frac(cols):

    cols = [str(c) for c in cols]

    # machine fallbacks: clean_headers names unrecognizable columns "col",
    # "value", "code" or "label" (numbered when repeated). None of these
    # carry identity — a table whose columns are all value_N is exactly as
    # headerless as one whose columns are all col_N, and the continuation
    # test below must see both the same way.
    return sum(
        not re.fullmatch(r"(col|value|code|label)(_\d+)?", c) for c in cols
    ) / max(len(cols), 1)


_STRONG_TITLE = re.compile(
    r"^(table|tabel|statement|annexure|appendix)\b|^\d{1,2}(\.\d{1,2})+\s",
    re.IGNORECASE,
)


def _strong_title(name):
    return bool(name) and bool(_STRONG_TITLE.match(str(name).strip()))


def normalize_colname(c):
    """Letters+digits only, lowercased — the canonical form used to compare
    column names across word-wrap and punctuation noise. Shared by the
    within-PDF continuation test and the cross-PDF signature/grouping logic."""
    return re.sub(r"[^a-z0-9]", "", str(c).lower())


_FALLBACK_NAME = re.compile(r"^(col(_\d+)?|_ext_\d+)$")


def _upgrade_names(base_cols, cont_cols):
    """
    Merge two column-name lists positionally, preferring a real name over an
    unnamed col_N / _ext_N fallback. Returns base length. Keeps base names
    except where base is a fallback and the continuation supplies a real name.
    """
    base = list(base_cols)
    cont = list(cont_cols)
    out = []
    for i, b in enumerate(base):
        c = cont[i] if i < len(cont) else None
        if _FALLBACK_NAME.fullmatch(str(b)) and c is not None and not _FALLBACK_NAME.fullmatch(str(c)):
            out.append(c)
        else:
            out.append(b)
    return out


def _entity_variant_titles(a, b):
    """True when two titles name DIFFERENT ENTITIES of the same table series:
    a shared tail of >= 2 words with different leading words — "Arunachal
    Pradesh Key Indicators" vs "Assam Key Indicators", "Haryana - Key
    Indicators" vs "Karnataka - Key Indicators". Entity-per-page report
    series (state fact sheets, ministry profiles, district handbooks) print
    IDENTICAL headers for every entity, so the header-equality merge below
    would otherwise stitch different states into one blob (observed on
    NFHS-6: Assam's pages absorbed into an "Arunachal Pradesh" table).
    Requires a real shared tail AND a differing first word, so genuinely
    unrelated weak names (section headings like "Maternal and Child Health")
    don't trip it and legitimate continuations still merge."""
    ta = [w for w in re.split(r"[\s\-–:]+", str(a).lower()) if w]
    tb = [w for w in re.split(r"[\s\-–:]+", str(b).lower()) if w]
    if len(ta) < 3 or len(tb) < 3 or ta == tb:
        return False
    return ta[-2:] == tb[-2:] and ta[0] != tb[0]


def _continues(prev, cur):
    """cur is a continuation of prev if it is on the next page with the
    same shape, and shares either the extracted title or the header row."""

    if cur["page"] - prev["pages"][-1] > 2:
        return False

    a, b = prev["df"], cur["df"]

    if abs(a.shape[1] - b.shape[1]) > 2:
        return False

    # same explicit title repeated on the next page
    if prev["name"] and cur["name"] and prev["name"] == cur["name"]:
        return True

    # two DIFFERENT *strong* titles (Table X.Y / numbered headings) are
    # two different tables even when the column structure is identical
    # (3.1 Group A vs 3.2 Group B share the exact same grid). Weak
    # prose-derived names vary across continuation pages and must NOT
    # block the header-equality merge below.
    if (
        _strong_title(prev["name"])
        and _strong_title(cur["name"])
        and prev["name"] != cur["name"]
    ):
        return False

    # entity-variant titles ("<State> Key Indicators") are different tables
    # even though the header row is identical for every entity in the series
    if _entity_variant_titles(prev["name"], cur["name"]):
        return False

    # identical, meaningfully-named header row repeated on the next page —
    # but only for substantial tables: small KPI strips often share a
    # generic year header (2022 / 2023 / Total) while being unrelated.
    cols_a = [str(c) for c in a.columns]
    cols_b = [str(c) for c in b.columns]

    # header word-wrap differs page to page ("brough t forwar d" /
    # "resolved within t" vs "brought forward" / "resolved within
    # time") — compare letters-only and tolerate truncation: a column
    # matches when one normalised name is a prefix of the other
    norm_a = [normalize_colname(c) for c in cols_a]
    norm_b = [normalize_colname(c) for c in cols_b]

    def _col_match(x, y):
        if x == y:
            return True
        if len(x) >= 4 and len(y) >= 4:
            return x.startswith(y) or y.startswith(x)
        return False

    matched = sum(_col_match(x, y) for x, y in zip(norm_a, norm_b))

    if (
        matched / len(norm_a) >= 0.8
        and _named_frac(cols_a) >= 0.5
        and len(a) >= 6
        and len(b) >= 2
    ):
        return True

    # headerless continuation: wide matrices (DARPG indicator grids)
    # print data from row one on follow-on pages — no title, no header,
    # every column an unnamed fallback. Same width + adjacent page +
    # a substantial, titled predecessor is the only signal available.
    # A WEAK name on the continuation doesn't block: with no header row,
    # title extraction routinely promotes a data cell ("Consumer Affairs",
    # a ministry name from the first row) into a bogus title — only a
    # strong Table X.Y / Annexure heading is evidence of a genuinely
    # separate table.
    if (
        not _strong_title(cur["name"])
        and _named_frac(cols_b) < 0.2
        and bool(prev["name"])
        and len(a) >= 4
        and len(b) >= 2
    ):
        return True

    return False


def _cols_similar(a_cols, b_cols, thresh=0.5):
    """Two column lists describe the same table when most positional names match
    (letters-only, prefix-tolerant for page-to-page word-wrap)."""
    a = [re.sub(r"[^a-z0-9]", "", str(c).lower()) for c in a_cols]
    b = [re.sub(r"[^a-z0-9]", "", str(c).lower()) for c in b_cols]
    if not a or abs(len(a) - len(b)) > 2:
        return False
    matched = sum(
        1 for x, y in zip(a, b)
        if x == y or (len(x) >= 4 and len(y) >= 4 and (x.startswith(y) or y.startswith(x)))
    )
    return matched / len(a) >= thresh


def _inherit_continuation_titles(out):
    """Give an untitled fragment its parent table's title.

    A continuation page often prints no heading (the title sits on an earlier
    page), and when the structural gates kept it from merging it ships untitled.
    An untitled table on the next page of a titled one with the SAME column
    shape is that table continued — adopt the title with a "(cont.)" marker.
    Conservative: requires page-adjacency, equal width, and column similarity,
    so an unrelated neighbour is never mislabelled."""
    last_named = None
    for it in out:
        name = it.get("name")
        if name and not _FALLBACK_NAME.fullmatch(str(name)):
            last_named = it
            continue
        if name or last_named is None:
            continue
        gap = it["pages"][0] - last_named["pages"][-1]
        same_width = it["df"].shape[1] == last_named["df"].shape[1]
        headerless = _named_frac(it["df"].columns) < 0.2
        if 1 <= gap <= 1 and same_width and (
            _cols_similar(last_named["df"].columns, it["df"].columns) or headerless
        ):
            base = re.sub(r"\s*\(cont\.\)$", "", str(last_named["name"])).strip()
            it["name"] = f"{base} (cont.)"
    return out


def stitch_tables(items):
    """
    items: list of dicts with keys table_id, name, page, df —
    in page order. Returns the same structure with continuation
    fragments concatenated; each item gains a "pages" list.
    """

    out = []

    for it in items:

        it = dict(it)
        it.setdefault("pages", [it["page"]])

        if out and _continues(out[-1], it):

            prev = out[-1]
            # concat positionally: label-based concat raises
            # InvalidIndexError when columns contain duplicates
            # (e.g. several unnamed "col" columns); _continues already
            # guarantees equal width
            if prev["df"].shape[1] == it["df"].shape[1]:
                # equal width: positional concat preserves column names exactly
                cont = it["df"].set_axis(range(it["df"].shape[1]), axis=1)
                base = prev["df"].set_axis(range(prev["df"].shape[1]), axis=1)
                merged = pd.concat([base, cont], ignore_index=True)
                # adopt real names from the continuation when the base column
                # is an unnamed col_N fallback — the header often lands on a
                # later page fragment while the first fragment is a headerless
                # mid-table slice (RBI wide state/sector matrices)
                merged.columns = _upgrade_names(prev["df"].columns, it["df"].columns)
            else:
                # width differs by ≤2: use positional concat (avoids duplicate-
                # column label errors), then restore the wider frame's column names
                base_df, cont_df = prev["df"], it["df"]
                wider_cols = (base_df.columns if base_df.shape[1] >= cont_df.shape[1]
                              else cont_df.columns)
                w = max(base_df.shape[1], cont_df.shape[1])
                b = base_df.set_axis(range(base_df.shape[1]), axis=1)
                c = cont_df.set_axis(range(cont_df.shape[1]), axis=1)
                merged = pd.concat([b, c], ignore_index=True)
                merged.columns = list(wider_cols) + [f"_ext_{i}" for i in range(len(wider_cols), merged.shape[1])]
            prev["df"] = merged
            prev["pages"].append(it["page"])

            if not prev["name"] and it["name"]:
                prev["name"] = it["name"]

        else:
            out.append(it)

    # Untitled continuation fragments inherit their parent table's title.
    _inherit_continuation_titles(out)

    # Drop exact duplicate rows that accumulate when the same data appears
    # in both a summary table and a detail table (Econ Survey pattern).
    for it in out:
        df = it["df"]
        deduped = df.drop_duplicates()
        if len(deduped) < len(df):
            it["df"] = deduped.reset_index(drop=True)

    # Reference/lookup tables: now that pages are merged, infer semantic column
    # names from the COMPLETE column content (code / level / name / state / …).
    # Done post-merge because per-page inference is unstable (a page may lack
    # the hierarchy rows that identify the "level" column). The archetype is read
    # from the item flag (set pre-normalization, where codes are still strings)
    # and falls back to live classification. Statistical tables are untouched.
    from backend.app.profile.table_profiler import is_reference_table
    from backend.app.standardization.column_namer import infer_reference_columns
    for it in out:
        is_ref = it.get("archetype") == "reference" or is_reference_table(it["df"])
        if is_ref:
            it["df"].columns = infer_reference_columns(it["df"])

    return out
