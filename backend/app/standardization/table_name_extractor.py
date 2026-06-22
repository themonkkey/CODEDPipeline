import re

from backend.app.translation.kruti_dev import looks_kruti


# Known Indian states/UTs for state-name repair.
# The NFHS-6 PDF stores some names with embedded spaces ("Ha ryana",
# "J harkhand") due to font/glyph rendering; _clean_title_words drops
# the short prefix fragment. We repair by suffix-matching or explicit lookup.
_INDIA_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
    "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
    "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu",
    "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
    "Andaman and Nicobar Islands", "Chandigarh",
    "Dadra and Nagar Haveli and Daman and Diu", "Dadra Nagar Haveli and Daman Diu",
    "Jammu and Kashmir", "Ladakh", "Lakshadweep", "NCT of Delhi", "Puducherry",
]

# Explicit map for cases where suffix-matching alone can't recover the name
# (e.g. "Tam Nadu" ← "Tamil Nadu", "Tel angana" ← "Telangana").
_STATE_FRAGMENT_MAP = {
    "tam nadu":   "Tamil Nadu",
    "tel angana": "Telangana",
    "of delhi":   "NCT of Delhi",
}


def _repair_state_name(name: str) -> str:
    """Fix truncated state names in NFHS-style table titles.

    Tries (in order):
    1. Exact known-state prefix match — already correct, return as-is.
    2. Explicit fragment map for multi-word edge cases.
    3. Suffix match: find the known state whose name ends with the fragment.
    """
    # Extract the part before "Key Indicators" / "Indicators"
    m = re.match(r"^(.*?)\s*(Key\s+)?Indicators\b", name, re.IGNORECASE)
    if not m:
        return name
    fragment = m.group(1).strip()
    suffix = name[m.start(2) or m.end(1):].strip()  # "Key Indicators" or "Indicators"

    # 1. Already a known state name
    for state in _INDIA_STATES:
        if fragment.lower() == state.lower():
            return name  # no repair needed

    # 2. Explicit lookup (for split-word cases like "Tam Nadu", "Tel angana")
    frag_low = fragment.lower()
    if frag_low in _STATE_FRAGMENT_MAP:
        return f"{_STATE_FRAGMENT_MAP[frag_low]} Key Indicators"

    # 3. Suffix match: known state ends with the fragment (e.g. "ryana" ← "Haryana")
    for state in _INDIA_STATES:
        if state.lower().endswith(frag_low) and len(frag_low) >= 3:
            return f"{state} Key Indicators"

    return name  # no match found — leave unchanged


TITLE_PATTERN = re.compile(
    r"(Table|Tabel|Statement|Annexure|Appendix)"
    r"[\s\-:.]*"
    r"\(?\s*(\d+([.\-]\d+)*)\s*\)?"
    r"[\s\-:.]*"
    r"(.*)",
    re.IGNORECASE,
)

VOWELS = set("aeiou")

ALLOWED_LOWER = {
    "of", "per", "and", "the", "in", "on", "for", "to", "no", "by", "at",
}


def _looks_english(word):

    bare = re.sub(r"[^A-Za-z]", "", word)

    if not bare or not any(c in VOWELS for c in bare.lower()):
        return False

    if bare.lower() in ALLOWED_LOWER:
        return True

    if len(bare) < 3:
        return False

    return (
        (bare[0].isupper() and bare[1:].islower())
        or bare.isupper()
        or bare.islower()
    )


def _clean_title_words(text, limit=10):

    words = [
        w for w in text.split()
        if _looks_english(w) and not looks_kruti(w) and "`" not in w
    ]

    # drop consecutive duplicates ("Number Number", "Telephone Telephone")
    deduped = []
    for w in words:
        if not deduped or deduped[-1].lower() != w.lower():
            deduped.append(w)

    return " ".join(deduped[:limit])


def _match_title(text):

    m = TITLE_PATTERN.search(text)

    if not m:
        return None

    label = m.group(1).title()
    number = m.group(2).replace("-", ".")
    rest = _clean_title_words(m.group(4))

    name = f"{label} {number}"

    if rest:
        name += " " + rest

    return name


def extract_table_name(df, header_rows, caption=None):

    # 1) explicit "Table X.Y ..." pattern — caption first, then header cells
    if caption:

        title = _match_title(caption)

        if title:
            return title

    header_df = df.iloc[:header_rows]

    for value in header_df.astype(str).values.flatten():

        title = _match_title(value)

        if title:
            return title

    # 2) numbered section heading in the caption: "3.1 Ranking of ..."
    if caption:

        m = re.match(
            r"\s*(\d+(?:\.\d+)+)[\s\-–:]+([^(\n]{3,90})", str(caption)
        )

        if m:
            words = m.group(2).strip().rstrip("–- ").split()
            lettered = [w for w in words if re.search(r"[A-Za-z]", w)]
            if len(lettered) >= 2:
                return f"{m.group(1)} " + " ".join(words[:10])

    # 2b) caption text — but only if it reads like a TITLE, not prose:
    #    a short line (2–10 words) without sentence punctuation.
    if caption:

        for line in str(caption).splitlines():

            line = line.strip()
            cleaned = _clean_title_words(line, limit=12)
            n_words = len(cleaned.split())

            if (
                2 <= n_words <= 10
                and not re.search(r"[.;:]\s|\.$", line)
                and len(cleaned) >= 0.6 * len(line)
            ):
                # Repair truncated NFHS state names ("ryana" → "Haryana")
                if re.search(r"\bIndicators?\b", cleaned, re.IGNORECASE):
                    cleaned = _repair_state_name(cleaned)
                return cleaned

    # 3) no confident title — caller assigns a sequential "Table N"
    return None
