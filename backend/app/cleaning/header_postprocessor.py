import re

VOWELS = set("aeiou")

KEEP_WORDS = {
    # geography / administration
    "district", "urban", "rural", "total", "number", "percentage",
    "density", "growth", "rate", "per", "capita", "census", "table",
    # demographics
    "population", "male", "males", "female", "females",
    "scheduled", "caste", "tribe", "household",
    # education / literacy
    "literacy", "education", "school", "students", "teachers",
    "primary", "middle", "high", "higher", "secondary",
    # agriculture
    "agriculture", "area", "forest", "cropping", "intensity",
    "gross", "net", "sown", "irrigated", "production", "yield",
    "crop", "food", "grain", "wheat", "rice", "soybean", "soyabean",
    "hect", "hectare",
    # banking / finance
    "banking", "bank", "banks", "loan", "credit", "deposit", "deposite",
    "branches", "branch", "commercial", "cooperative", "schedule",
    "receipts", "revenue", "sales", "tax", "excise", "lakh", "crore",
    # labour / employment
    "work", "worker", "cultivator", "labourers", "employment", "placement",
    # health
    "medical", "health", "hospital", "beds", "allopathic", "institution",
    "institutions",
    # transport / communication
    "telephone", "center", "centre", "connection", "connections",
    "transport", "communication", "post", "office", "offices",
    "road", "roads", "vehicles", "registered",
    # utilities / misc
    "ministry", "ministries", "departments", "pending", "brought",
    "forward", "rank", "score", "grievances", "appeals", "disposal",
    "resolved", "receipt", "officers", "grai",
    "hand", "pump", "pumps", "installed", "thousand",
    "electrified", "villages", "village", "towns", "town",
}


def _vowel_ratio(word):
    if not word:
        return 0
    return sum(1 for c in word if c in VOWELS) / len(word)


def clean_column_name(col):

    col_str = str(col).lower()

    # already-clean NFHS group headers (built by _try_nfhs_headers) —
    # keep verbatim; the generic word filter would drop the vowel-less
    # "nfhs" token and lose the survey-round grouping.
    if re.fullmatch(r"(indicator|nfhs\d(_(urban|rural|total))?(_\d+)?)", col_str):
        return col_str

    # serial-number column ("S. No.", "Sl No", bare "no")
    if re.fullmatch(r"s?l?[._\s]*no[._\s]*", col_str):
        return "s_no"

    # preserve standalone year patterns
    if re.fullmatch(r"\d{4}[_\-]\d{2}", col_str):
        return col_str.replace("-", "_")

    years = re.findall(r"\d{4}[_\-]\d{2}", col_str)

    words = re.findall(r"[a-z]+", col_str)

    # filter: must have vowel and length >= 3
    english_words = [
        w for w in words
        if len(w) >= 3 and any(c in VOWELS for c in w)
    ]

    # keep a word if it is in the vocabulary OR looks like
    # plausible English (vowel ratio >= 30%) — do NOT drop
    # legitimate words (e.g. "telephone center") just because
    # a vocabulary word is also present
    plausible = [
        w for w in english_words
        if w in KEEP_WORDS or _vowel_ratio(w) >= 0.25
    ]

    # cap runaway names (multi-row headers concatenate badly)
    parts = list(dict.fromkeys(plausible))[:6]

    if years:
        for y in years:
            y_clean = y.replace("-", "_")
            if y_clean not in parts:
                parts.append(y_clean)

    if not parts:
        return None

    return "_".join(parts)


_PHANTOM = re.compile(r"col(_\d+)?$")
_NUM_CELL = re.compile(r"^\(?-?[\d,]+(\.\d+)?%?\)?$")


_NUM_PLACEHOLDER = {"", "nan", "none", "-", "–", "—", "−", "n.a.", "na", "n/a",
                    "nil", "..", "...", "*"}


def _is_numeric_column(series):
    """True when >=80% of a column's real (non-placeholder) cells are numeric.
    Runs at clean_headers time, before numeric casting, so cells are strings;
    dash/blank placeholders are ignored so a sparse numeric column still counts."""
    judged = [
        s for s in (str(v).strip() for v in series.tolist())
        if s.lower() not in _NUM_PLACEHOLDER
    ]
    if not judged:
        return False
    return sum(1 for v in judged if _NUM_CELL.match(v)) / len(judged) >= 0.8


_TEXT_CHAR = re.compile(r"[A-Za-zऀ-ॿ]")


def _is_text_label_column(series):
    """True when an unnamed column holds row labels — substantially populated and
    predominantly non-numeric text (state/item/ministry names). Such a column is
    the table's dimension key; naming it "label" makes it addressable instead of
    reading as extraction noise (the text-column analogue of the numeric "value"
    rename)."""
    vals = [str(v).strip() for v in series.tolist()]
    pop = [v for v in vals if v.lower() not in _NUM_PLACEHOLDER]
    if not pop or len(pop) < max(2, 0.5 * len(vals)):
        return False
    texty = sum(1 for v in pop if not _NUM_CELL.match(v) and _TEXT_CHAR.search(v))
    return texty / len(pop) >= 0.6


def _dedupe_columns(columns):
    """Make column names unique so the table loads into pandas/SQL without
    silent value-coalescing.

    Repeated header blocks and side-by-side panels (e.g. an "average MPCE"
    block printed once "with imputation" and once without, whose distinguishing
    label camelot never captured) collapse to identical composite names. The
    second and later occurrences get a numeric suffix — the same convention
    pandas.read_csv uses — chosen so it never collides with an existing name."""
    used = set()
    out = []
    for name in columns:
        name = str(name)
        if name not in used:
            used.add(name)
            out.append(name)
            continue
        k = 2
        while f"{name}_{k}" in used:
            k += 1
        new = f"{name}_{k}"
        used.add(new)
        out.append(new)
    return out


def clean_headers(df):

    new_cols = []
    for i, col in enumerate(df.columns):
        cleaned = clean_column_name(col)
        if cleaned is None:
            cleaned = f"col_{i}"
        if _PHANTOM.fullmatch(cleaned):
            series = df.iloc[:, i]
            # First try a high-confidence content role (state / year / percentage
            # / date / code / level) — a meaningful name beats a generic one for
            # any PDF, and a lost header is exactly where content must speak.
            from backend.app.standardization.column_namer import confident_role
            role = confident_role(series)
            if role:
                cleaned = role
            # A headerless but data-bearing value column ("col_3") is real data
            # whose header was lost upstream — name it "value" so it is
            # addressable instead of reading as extraction noise. Skip column 0
            # (the row-label/dimension column).
            elif i > 0 and _is_numeric_column(series):
                cleaned = "value"
            # A headerless text column holding row labels (the dimension key,
            # usually column 0) — name it "label" for the same reason.
            elif _is_text_label_column(series):
                cleaned = "label"
        new_cols.append(cleaned)

    df.columns = new_cols

    if "district" in df.columns:

        cols = list(df.columns)

        if len(cols) > 2:

            possible_hindi_col = cols[1]

            if possible_hindi_col != "district":

                df = df.drop(
                    columns=[possible_hindi_col]
                )

    df.columns = _dedupe_columns(df.columns)

    return df