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

    # unnamed fallbacks appear as both "col" and "col_<n>"
    return sum(
        not re.fullmatch(r"col(_\d+)?", c) for c in cols
    ) / max(len(cols), 1)


_STRONG_TITLE = re.compile(
    r"^(table|tabel|statement|annexure|appendix)\b|^\d{1,2}(\.\d{1,2})+\s",
    re.IGNORECASE,
)


def _strong_title(name):
    return bool(name) and bool(_STRONG_TITLE.match(str(name).strip()))


def _continues(prev, cur):
    """cur is a continuation of prev if it is on the next page with the
    same shape, and shares either the extracted title or the header row."""

    if cur["page"] - prev["pages"][-1] != 1:
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

    # identical, meaningfully-named header row repeated on the next page —
    # but only for substantial tables: small KPI strips often share a
    # generic year header (2022 / 2023 / Total) while being unrelated.
    cols_a = [str(c) for c in a.columns]
    cols_b = [str(c) for c in b.columns]

    # header word-wrap differs page to page ("brough t forwar d" /
    # "resolved within t" vs "brought forward" / "resolved within
    # time") — compare letters-only and tolerate truncation: a column
    # matches when one normalised name is a prefix of the other
    norm_a = [re.sub(r"[^a-z0-9]", "", c.lower()) for c in cols_a]
    norm_b = [re.sub(r"[^a-z0-9]", "", c.lower()) for c in cols_b]

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
    if (
        not cur["name"]
        and _named_frac(cols_b) < 0.2
        and bool(prev["name"])
        and len(a) >= 4
        and len(b) >= 2
    ):
        return True

    return False


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
                merged.columns = prev["df"].columns
            else:
                # width differs by ≤2 (one fragment lost a separator column):
                # align on column names so no column is silently dropped
                cont = it["df"].copy()
                cont.columns = prev["df"].columns[:cont.shape[1]]
                merged = pd.concat([prev["df"], cont], ignore_index=True)
            prev["df"] = merged
            prev["pages"].append(it["page"])

            if not prev["name"] and it["name"]:
                prev["name"] = it["name"]

        else:
            out.append(it)

    # Drop exact duplicate rows that accumulate when the same data appears
    # in both a summary table and a detail table (Econ Survey pattern).
    for it in out:
        df = it["df"]
        deduped = df.drop_duplicates()
        if len(deduped) < len(df):
            it["df"] = deduped.reset_index(drop=True)

    return out
