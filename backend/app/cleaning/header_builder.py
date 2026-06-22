import re
import pandas as pd
from backend.app.cleaning.data_start_detector import detect_data_start
from backend.app.cleaning.header_detector import YEAR_PATTERN

DISTRICT_NAMES = {
    "sheopur",
    "morena",
    "bhind",
    "gwalior",
    "datia",
    "shivpuri",
    "guna",
    "ashok nagar",
    "tikamgarh",
    "chhatarpur",
    "panna",
    "sagar",
    "damoh",
    "satna",
    "rewa",
    "sidhi",
    "singrauli",
    "shahdol",
    "anuppur",
    "umaria",
    "katni",
    "jabalpur",
    "narsinghpur",
    "indore",
    "badwani",
    "khargone",
    "rajgarh",
    "vidisha",
    "bhopal",
    "raisen",
    "sehore",
    "betul",
    "harda",
    "burhanpur",
    "khandwa",
    "dewas",
    "ujjain",
    "mandsaur",
    "neemuch",
    "ratlam"
}


def is_year(text):

    text = str(text)

    return bool(
        re.search(
            r"\d{4}[-–]\d{2}",
            text
        )
    )


VOWELS = set("aeiou")


def _has_vowel(word):
    return any(c in VOWELS for c in word.lower())


#
# Lowercase words allowed in headers. Legacy Kruti Dev
# soup ("tula", "gtkj", "dh") is lowercase or has internal
# capitals ("gSaMiaiksa"), while real English header text
# in these PDFs is Titlecase ("Number of Installed Hand
# Pumps"). So a word is kept only if it is Titlecase,
# ALL-CAPS, or a known lowercase English word.
#

ALLOWED_LOWER = {
    "of", "per", "and", "the", "in", "on", "for", "to",
    "no", "by", "at",
    # statistical-report header vocabulary: modern Unicode reports
    # (PLFS, DARPG, Energy) print headers in lowercase, which the
    # Titlecase-only rule (an anti-Kruti defence) was discarding
    "rural", "urban", "male", "female", "person", "persons",
    "age", "ages", "group", "groups", "years", "year", "status",
    "industry", "sector", "work", "worker", "workers", "activity",
    "education", "level", "general", "current", "weekly", "usual",
    "principal", "subsidiary", "labour", "force", "participation",
    "rate", "rates", "unemployment", "employment", "employed",
    "distribution", "percentage", "monthly", "earnings", "wages",
    "wage", "self", "casual", "regular", "salaried", "household",
    "households", "size", "religion", "social", "category",
    "expenditure", "class", "state", "india", "division", "code",
    "item", "broad", "each", "according", "total", "number",
    "all", "category", "workers", "occupation", "quintile",
}


def _looks_english(word):

    bare = re.sub(r"[^A-Za-z]", "", word)

    if not bare or not _has_vowel(bare):
        return False

    if bare.lower() in ALLOWED_LOWER:
        return True

    if len(bare) < 3:
        return False

    # Titlecase: Number, Telephone
    if bare[0].isupper() and bare[1:].islower():
        return True

    # ALL CAPS: DISTRICT
    if bare.isupper():
        return True

    # mixed internal caps (gSaMiaiksa) or lowercase soup (tula)
    return False


def extract_english(text):

    text = str(text)

    matches = re.findall(
        r"[A-Za-z][A-Za-z\s&().,\-/]{2,}",
        text
    )

    filtered = []
    for chunk in matches:
        words = [
            w for w in chunk.split()
            if _looks_english(w)
        ]
        if words:
            filtered.append(" ".join(words))

    return " ".join(filtered).strip()


#
# Table-title fragments ("Table 6.2", "Tabel 6-2") leak into header
# cells on DES-style pages where the title row spans the grid; they
# are names, not column semantics — strip before building headers.
#

_TITLE_FRAGMENT = re.compile(
    r"\b(table|tabel|statement|annexure|appendix)\b"
    r"(\s*\(?\s*\d+([.\-]\d+)*\s*\)?)?",
    re.IGNORECASE,
)

# age / size-class column headers: "0-4", "15-29", "60+", "5 - 9"
_RANGE_TOKEN = re.compile(r"\d{1,3}\s*[-–]\s*\d{1,3}|\d{1,3}\s*\+")


def clean_header(text):

    text = str(text).lower()

    text = re.sub(
        r"[^a-z0-9]+",
        "_",
        text
    )

    text = re.sub(
        r"_+",
        "_",
        text
    )

    # bilingual sources emit both spellings / repeated words
    # ("telephone_centre_center", "number_number") — normalise and
    # drop consecutive duplicates
    text = text.replace("centre", "center")

    tokens = []
    for tok in text.strip("_").split("_"):
        if not tokens or tokens[-1] != tok:
            tokens.append(tok)

    return "_".join(tokens)


# Subdivision-level words that mark a real column-header row (not a title row)
_SUBDIVISION_VOCAB = re.compile(
    r"\b(rural|urban|males?|females?|persons?|transgender|total|combined|state|"
    r"district|zone|region|sector|category|quarterly|annual|monthly|"
    r"workers?|employed|employment|rate|number|percentage|industry)\b",
    re.IGNORECASE,
)
# Serial-index cells like (1) (2) (3) used as column-numbering rows
_INDEX_CELL = re.compile(r"^\(?[0-9]{1,2}\.?\)?$")

_NFHS_GROUP = re.compile(r"nfhs[\s\-–]*([0-9])", re.IGNORECASE)
_NFHS_SUBLABELS = ("urban", "rural", "total")


def _try_nfhs_headers(df, header_rows):
    """
    NFHS factsheet tables carry a fixed two-level header that the generic
    builder mangles (the section title in the last header row leaks into
    col0's name, and the vowel-less "nfhs" group label is later dropped):

        row: ['',          '',      'NFHS-6', '',      'NFHS-5']
        row: ['Indicators','',      '(2023-24)','',    '(2019-21)']
        row: ['<section>', 'Urban', 'Rural',  'Total', 'Total']

    Produce deterministic names — indicator | nfhs6_urban | nfhs6_rural |
    nfhs6_total | nfhs5_total — by reading the group row and the
    Urban/Rural/Total sub-row directly.

    Returns the headered DataFrame, or None when the table is not an NFHS
    factsheet (so the generic path runs untouched for every other source).
    """

    header_df = df.iloc[:header_rows]

    group_row_idx = None
    sub_row_idx = None

    for i in range(len(header_df)):
        cells = [str(v).strip().lower() for v in header_df.iloc[i].tolist()]
        if group_row_idx is None and any(_NFHS_GROUP.search(c) for c in cells):
            group_row_idx = i
        # Gap A: accept both separate "urban"/"rural" cells and a merged "urban rural" cell
        if "urban" in cells and "rural" in cells:
            sub_row_idx = i
        elif sub_row_idx is None and any("urban" in c and "rural" in c for c in cells):
            sub_row_idx = i

    if group_row_idx is None or sub_row_idx is None:
        # Continuation-page detection: NFHS table continued from previous page
        # with no repeated group/sub header.  Signature: exactly 5 columns,
        # ≥2 numbered indicators in col0, ≥50% numeric values in cols 1-4.
        if df.shape[1] == 5:
            data_rows = df.iloc[header_rows:]
            _NUM_IND = re.compile(r"^\d{1,3}\.")
            _NUM_VAL = re.compile(r"^\(?-?[\d*,]+(\.\d+)?%?\)?$")
            numbered = sum(1 for v in data_rows.iloc[:, 0].astype(str)
                           if _NUM_IND.match(v.strip()))
            if numbered >= 2:
                value_cells = [
                    v for row in data_rows.astype(str).values.tolist()
                    for v in row[1:]
                    if v.strip() not in ("", "nan", "None")
                ]
                num_frac = (sum(1 for v in value_cells if _NUM_VAL.match(v.strip()))
                            / len(value_cells)) if value_cells else 0
                if num_frac >= 0.5:
                    cont_df = data_rows.reset_index(drop=True)
                    cont_df.columns = [
                        "indicator", "nfhs6_urban", "nfhs6_rural",
                        "nfhs6_total", "nfhs5_total",
                    ]
                    return cont_df
        return None

    # per-column group: scan the group row, forward-fill, then backfill the
    # leading value columns to the first group seen (NFHS-6 sits visually
    # over Urban/Rural/Total but is anchored a column to the right).
    group_cells = [str(v).strip() for v in header_df.iloc[group_row_idx].tolist()]
    groups = []
    current = None
    for c in group_cells:
        m = _NFHS_GROUP.search(c)
        if m:
            current = f"nfhs{m.group(1)}"
        groups.append(current)
    first = next((g for g in groups if g), None)
    groups = [g or first for g in groups]

    sub_cells = [str(v).strip().lower() for v in header_df.iloc[sub_row_idx].tolist()]

    columns = []
    seen = {}
    _pending_rural = False  # set when merged "Urban Rural" is at col 1
    for col in range(df.shape[1]):
        if col == 0:
            name = "indicator"
            _pending_rural = False
        else:
            grp = groups[col] if col < len(groups) else None
            if _pending_rural:
                # Previous col got "urban" from a col-1 merged cell; this is rural.
                sub = "rural"
                _pending_rural = False
            else:
                sub_c = sub_cells[col] if col < len(sub_cells) else ""
                # Gap A fix: handle camelot-merged "Urban Rural" in one cell
                if "urban" in sub_c and "rural" in sub_c:
                    if col == 1:
                        # Merged at first value col → assign Urban here, Rural next
                        sub = "urban"
                        _pending_rural = True
                    else:
                        # Merged at later col → preceding empty col was Urban
                        sub = "rural"
                        if columns and re.fullmatch(r"col_\d+", columns[-1]):
                            prev_grp = groups[col - 1] if col - 1 < len(groups) else grp
                            columns[-1] = (f"{prev_grp or grp}_urban"
                                           if (prev_grp or grp) else "urban")
                else:
                    sub = next((s for s in _NFHS_SUBLABELS if s in sub_c), None)
            if sub and grp:
                name = f"{grp}_{sub}"
            elif sub:
                name = sub
            else:
                name = f"col_{col}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        columns.append(name)

    data_df = df.iloc[header_rows:].reset_index(drop=True)

    # Drop trailing artifact columns that camelot emits from merged header cells
    # (West Bengal, Ladakh, Puducherry show 7 cols instead of 5).
    # Keep a column beyond position 4 only if it has >20% populated cells
    # AND its name is not a generic col_N / urban_rural artifact.
    if data_df.shape[1] > 5:
        _ARTIFACT = re.compile(r"^(col_?\d*|urban_rural)$")
        keep = list(range(min(5, data_df.shape[1])))
        for c in range(5, data_df.shape[1]):
            col_data = data_df.iloc[:, c].astype(str).str.strip()
            populated = (~col_data.isin(["", "nan", "None"])).sum()
            frac = populated / len(col_data) if len(col_data) else 0
            col_name = columns[c] if c < len(columns) else f"col_{c}"
            if frac > 0.2 and not _ARTIFACT.match(col_name):
                keep.append(c)
        if len(keep) < data_df.shape[1]:
            data_df = data_df.iloc[:, keep]
            columns = [columns[i] for i in keep if i < len(columns)]

    data_df.columns = columns[:data_df.shape[1]]
    return data_df


_ALREADY_NAMED = re.compile(r"^(indicator|nfhs\d_)")


def apply_headers(df, header_rows):

    # Docling pre-names columns (nfhs6_urban, indicator, …) — skip header detection
    if sum(1 for c in list(df.columns)[:2] if _ALREADY_NAMED.match(str(c))) >= 1:
        return df

    # Gap C fix: never consume all rows — leave at least 1 data row
    if len(df) > 0:
        header_rows = min(header_rows, len(df) - 1)

    nfhs = _try_nfhs_headers(df, header_rows)
    if nfhs is not None:
        return nfhs

    #
    # Find actual start of data
    #

    data_start = detect_data_start(df)

    #
    # Trust data detector only when
    # it finds data BEFORE header detector.
    # data_start == 0 is real: continuation pages print data from the
    # first row (no header) — consuming a row as header loses data.
    #

    if (
        data_start is not None
        and data_start < header_rows
    ):
        header_rows = data_start

    header_df = df.iloc[:header_rows].copy()

    #
    # Merged (spanning) header cells: camelot puts the
    # group label only in the FIRST cell of the span and
    # leaves the rest empty. Forward-fill each header row
    # horizontally so every sub-column (e.g. each year)
    # inherits its parent group label.
    #
    # Skip rows that are table titles (only one non-empty
    # cell), otherwise the title would leak into every column.
    #

    #
    # Never fill the LAST header row (the sub-header row,
    # e.g. years) — its cells are per-column, not spans,
    # and filling would leak values into unrelated columns.
    #

    data_df = (
        df.iloc[header_rows:]
        .reset_index(drop=True)
    )

    columns = []

    # Build the set of rows that should NOT contribute to column names:
    # (a) single-cell spanning rows (table title / subtitle)
    # (b) prose-description rows — short word-fragments spread across cells
    #     with no subdivision vocabulary (Rural/Urban/Male/Female/…) and no years
    # (c) serial index rows like (1) (2) (3) used as column-numbering bands
    # (d) original _TITLE_FRAGMENT pattern (Table X.Y lone cell)
    title_rows = set()

    for i in range(len(header_df)):

        cells = [str(v).strip() for v in df.iloc[i].tolist()]
        non_empty = [c for c in cells if c and c not in ("nan", "None")]

        # (a) single-cell spanning row
        if len(non_empty) <= 1:
            title_rows.add(i)
            continue

        # (d) Table/Tabel X.Y in the first non-empty cell → title row even if
        #     Camelot spread the rest of the title across sibling cells
        first_ne = non_empty[0] if non_empty else ""
        if _TITLE_FRAGMENT.search(first_ne):
            title_rows.add(i)
            continue

        # (c) serial index row: majority of non-empty cells are (1)/(2)/…
        index_like = sum(1 for c in non_empty if _INDEX_CELL.match(c))
        if index_like >= max(2, len(non_empty) * 0.5):
            title_rows.add(i)
            continue

        # (b) prose-description row: all cells are 1-2 words, no statistical
        #     subdivision vocabulary, no year labels → word-fragments of a title
        full_text = " ".join(non_empty)
        all_short = all(len(c.split()) <= 2 for c in non_empty)
        has_subdiv = bool(_SUBDIVISION_VOCAB.search(full_text))
        has_year = bool(YEAR_PATTERN.search(full_text))
        if all_short and not has_subdiv and not has_year and len(non_empty) >= 3:
            title_rows.add(i)

    # Forward-fill parent group labels into sibling columns.
    # Skip title/description rows (their word-fragments must not propagate).
    # Never fill the LAST non-title header row (sub-label row).
    active_header_rows = [i for i in range(len(header_df)) if i not in title_rows]
    last_active = active_header_rows[-1] if active_header_rows else None

    for i in range(len(header_df)):

        if i in title_rows or i == last_active:
            continue

        row = header_df.iloc[i].astype(str).str.strip()

        non_empty = (row != "").sum()

        if non_empty < 2:
            continue

        filled = (
            header_df.iloc[i]
            .replace("", None)
            .ffill()
        )

        header_df.iloc[i] = filled.fillna("")

    for col in range(df.shape[1]):

        parts = []
        range_tokens = []

        for i, value in enumerate(header_df.iloc[:, col]):

            if i in title_rows:
                continue

            raw = str(value).strip()

            value = _TITLE_FRAGMENT.sub(" ", raw).strip()

            if not value:
                continue

            english_text = extract_english(value)

            if english_text:

                #
                # Ignore district names
                #

                if (
                    english_text.lower()
                    in DISTRICT_NAMES
                ):
                    continue

                parts.append(
                    english_text
                )

            elif is_year(value):

                parts.append(
                    value
                )

            elif _RANGE_TOKEN.fullmatch(value):

                # age/size-class headers are pure ranges ("0-4",
                # "15-29", "60+") with no letters to extract
                range_tokens.append(value)

        if range_tokens:

            # the range is the distinguishing part of the header
            # ("0-4" vs "5-9") — keep it even when group labels exist
            parts.append(range_tokens[-1])

        if parts:

            header = clean_header(
                "_".join(parts)
            )

        else:

            header = f"col_{col}"

        columns.append(header)

    data_df.columns = columns

    #
    # Drop ghost columns: camelot sometimes emits columns whose data
    # cells are all empty (split header cells create them). They only
    # add clutter and duplicate header names.
    #

    if data_df.shape[1] > 2:

        keep = [
            c for c in range(data_df.shape[1])
            if not data_df.iloc[:, c].astype(str).str.strip().eq("").all()
        ]

        if len(keep) >= 2 and len(keep) < data_df.shape[1]:
            data_df = data_df.iloc[:, keep]

    #
# First column should be serial number,
# not table title
#

    if len(data_df.columns) > 0:

        first_col = data_df.columns[0]

    first_values = (
        data_df.iloc[:, 0]
        .astype(str)
        .head(10)
        .tolist()
    )

    numeric_count = sum(
        v.strip().isdigit()
        for v in first_values
    )

    if numeric_count >= 5:

        cols = list(
            data_df.columns
        )

        cols[0] = "s_no"

        data_df.columns = cols
    #
    # Replace Hindi district column
    # with English district column
    #

    district_cols = []

    for i, col in enumerate(data_df.columns):

        if "district" in str(col).lower():

            district_cols.append(i)

    if len(district_cols) >= 2:

        english_col = district_cols[-1]

        if english_col != 1:

            data_df.iloc[:, 1] = (
                data_df.iloc[:, english_col]
            )

            #
            # drop by POSITION — both columns may share the
            # name "district" (translated Hindi header), and
            # dropping by name would remove them all
            #

            keep_idx = [
                i for i in range(data_df.shape[1])
                if i != english_col
            ]

            data_df = data_df.iloc[:, keep_idx]

        cols = list(
            data_df.columns
        )

        cols[1] = "district"

        data_df.columns = cols

    return data_df