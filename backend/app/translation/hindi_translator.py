"""
Translate legacy Kruti Dev / Chanakya font-encoded Hindi text
(which Camelot extracts as ASCII soup like "ftyk", "';ksiqj")
into English.

The mapping was learned by pairing the Hindi district column with
the English "District" column across the source PDF, plus common
header / summary terms added manually.
"""

import re

from backend.app.translation.glossary import DEV_PHRASES, DEV_WORDS
from backend.app.translation.kruti_dev import (
    kruti_to_unicode,
    looks_kruti,
    unicode_to_ascii,
)

# legacy-encoded Hindi -> English
LEGACY_MAP = {
    # header / common terms
    "ftyk": "District",
    "ftys": "District",
    "o\"kz": "Year",
    ";ksx": "Total",
    "dqy": "Total",
    "Ø- l-": "S.No",
    "Ø-l-": "S.No",
    "dz- l-": "S.No",
    # state
    "e/;izns'k": "Madhya Pradesh",
    # districts (Madhya Pradesh)
    "';ksiqj": "Sheopur",
    "eqjSuk": "Morena",
    "fHk.M": "Bhind",
    "Xokfy;j": "Gwalior",
    "nfr;k": "Datia",
    "f'koiqjh": "Shivpuri",
    "xquk": "Guna",
    "x quk": "Guna",
    "x uk q": "Guna",
    "x q uk": "Guna",
    "v'kksduxj": "Ashok Nagar",
    "Vhdex<+": "Tikamgarh",
    "Nrjiqj": "Chhatarpur",
    "fuokMh": "Nivari",
    "fuokM+h": "Nivari",
    "iUuk": "Panna",
    "lkxj": "Sagar",
    "Lkkxj": "Sagar",
    "neksg": "Damoh",
    "lruk": "Satna",
    "jhok": "Rewa",
    "mefj;k": "Umaria",
    "'kgMksy": "Shahadol",
    "vuqiiqj": "Anuppur",
    "lh/kh": "Sidhi",
    "flaxjkSyh": "Singrauli",
    "uhep": "Neemuch",
    "eanlkSj": "Mandsaur",
    "jryke": "Ratlam",
    "mTtSu": "Ujjain",
    "'kktkiqj": "Shajapur",
    "vkxj ekyok": "Agar Malwa",
    "nsokl": "Dewas",
    ">kcqvk": "Jhabua",
    "vyhjktiqj": "Alirajpur",
    "/kkj": "Dhar",
    "bUnkSj": "Indore",
    "cM+okuh": "Badwani",
    "[k.Mok": "Khandwa",
    "cqjgkuiqj": "Burhanpur",
    "[kjxkSu": "Khargaone",
    "jktx<+": "Rajgarh",
    "fofn'kk": "Vidisha",
    "Hkksiky": "Bhopal",
    "jk;lsu": "Raisen",
    "lhgksj": "Sehore",
    "cSrwy": "Betul",
    "gjnk": "Harda",
    "ueZnkiqje": "Narmadapuram",
    "dVuh": "Katni",
    "tcyiqj": "Jabalpur",
    "ujflagiqj": "Narsinghpur",
    "e.Myk": "Mandla",
    "fMaMksjh": "Dindori",
    "fNUnokM+k": "Chhindwara",
    "flouh": "Seoni",
    "ckyk?kkV": "Balaghat",
}

#
# Unicode Devanagari support (e.g. MoSPI PLFS Hindi tables).
# PDF extraction mangles conjuncts (वर्ष -> वषि, और -> औि), so the
# glossary carries both clean and observed-mangled spellings.
#

_DEV_RE = re.compile(r"[ऀ-ॿ]")

_QUOTE_FIXES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})


def _translate_devanagari(text):
    """Glossary translation for Unicode-Hindi cells: phrases first,
    then word-by-word; unknown words pass through unchanged."""

    out = text

    for hi, en in DEV_PHRASES:
        out = out.replace(hi, en)

    if _DEV_RE.search(out):
        tokens = []
        for tok in out.split(" "):
            bare = tok.strip(":,;()|.")
            if bare in DEV_WORDS:
                rep = DEV_WORDS[bare]
                if rep:
                    tokens.append(tok.replace(bare, rep))
            elif _DEV_RE.search(tok):
                tokens.append(_decode_mojibake(tok))
            else:
                tokens.append(tok)
        out = " ".join(tokens)

    return re.sub(r"\s+", " ", out).strip()


_ENGLISH_SHAPE = re.compile(r"^[A-Za-z][A-Za-z0-9./()&,-]*$")

# observed low-vowel lowercase mojibake words that are real English
_MOJIBAKE_LOWER_OK = {
    "blocks", "ltd", "trg", "pvt", "dept", "sub", "respectively",
}


def _decode_mojibake(token):
    """
    Devanagari that the glossary does not know may be ENGLISH typed in
    a Kruti-slot font ("ज्वजंस" = Total). Decode via the inverse glyph
    map and keep the result only when it is English-shaped — real Hindi
    decodes to consonant junk and passes through untouched.
    """
    decoded = unicode_to_ascii(token)

    if _DEV_RE.search(decoded) or not _ENGLISH_SHAPE.match(decoded):
        return token

    bare = re.sub(r"[^A-Za-z]", "", decoded)

    if len(bare) < 2:
        return token

    # validate case shape per hyphen/punct part: "Non-Coking",
    # "ub-Total", "ACB(India)", "FSUs" are all fine compounds.
    # all-lowercase decodes are the dangerous class — real Hindi can
    # decode to lowercase junk with a stray vowel ("dkexkf"), so they
    # must clear a higher vowel bar or be known words.
    parts = [p for p in re.split(r"[^A-Za-z]+", decoded) if p]

    def _cased(p):
        if p.istitle() or p.isupper():
            return True
        if p[:-1].isupper() and p[-1] == "s":          # FSUs
            return True
        if p.islower():
            low_vowels = sum(c in "aeiou" for c in p)
            return (
                p in _MOJIBAKE_LOWER_OK
                or len(p) <= 3
                or low_vowels / len(p) >= 0.3
            )
        return False

    plausible = bool(parts) and all(_cased(p) for p in parts)

    return decoded if plausible else token


def _normalize(text):
    text = str(text).translate(_QUOTE_FIXES)
    return re.sub(r"\s+", " ", text).strip()


# case-insensitive, whitespace-insensitive secondary index
_LOOSE_MAP = {
    re.sub(r"\s+", "", k).lower(): v for k, v in LEGACY_MAP.items()
}


_ASCII_WORD = re.compile(r"^[A-Za-z`'\";.+?/-]+$")


def _soup_by_glossary(token):
    """
    Some soup ("izfr", "iathd`r") evades looks_kruti's heuristics.
    Convert the token and accept it as soup ONLY when the glossary
    recognises the resulting Devanagari — real English ("utilization")
    converts to junk the glossary has never seen, so it stays intact.
    """
    if len(token) < 3 or not _ASCII_WORD.match(token):
        return False
    bare = re.sub(r"[^A-Za-z]", "", token).lower()
    if bare in _ENGLISH_SAFE:
        return False
    dev = kruti_to_unicode(token)
    return dev in DEV_WORDS


_ENGLISH_SAFE = {
    w.lower() for w in (
        list(LEGACY_MAP.values())
        + ["of", "per", "and", "the", "in", "on", "for", "to", "no",
           "by", "at", "total", "number", "year", "rate", "district"]
    )
}


# a list marker glued straight onto a real word with no space ("A.Settlement",
# "B.Payment" — section-heading numbering in English-only PDFs). looks_kruti's
# internal-capital/CamelCase check reads the stray leading capital before the
# period as a broken compound and flags the whole thing as soup (observed:
# RBI Table 61's "A.Settlement Systems" / "B.Payment Systems" category labels
# lost "Settlement"/"Payment" entirely). A short list marker is never itself
# Kruti soup, so this shape overrides the soup checks below.
_LIST_MARKER_GLUED = re.compile(r"^[A-Za-z]{1,3}\.[A-Z][a-z]{2,}")


def _is_english_shaped(tok):
    """True when tok does NOT read as Kruti-Dev soup — the general test
    used to grow a trailing English span token-by-token. Checks the
    glossary decode too (not just the looks_kruti shape heuristic): a
    short soup word can evade looks_kruti's markers entirely while still
    decoding to a real, known Devanagari word ("vuqnku" = "अनुदान" =
    Grants-in-aid) — without this it would be swept into the English
    suffix and shown untranslated right next to its own translation."""
    if _LIST_MARKER_GLUED.match(tok):
        return True
    bare = re.sub(r"[^A-Za-z]", "", tok)
    return bool(bare) and not (looks_kruti(tok) or _soup_by_glossary(tok))


def _split_bilingual_suffix(text):
    """Indian government bilingual reports routinely print a Hindi label
    immediately followed by its OWN English translation on the same line
    ("jktLo izkfIr;ka Revenue Receipts" = Kruti-Dev "Revenue Receipts"
    label + the literal English "Revenue Receipts"). Word-by-word
    Hindi->English dictionaries can never cover arbitrary report content,
    but the document is already handing us the authoritative translation —
    trust it. Walk the cell backward collecting a maximal trailing run of
    non-soup tokens; if that run contains real English content and the
    remaining prefix is genuine Kruti soup, the prefix is the untranslated
    label and the suffix IS its translation. Returns None when the cell
    doesn't fit this shape (falls back to the decode/glossary path)."""
    tokens = text.split(" ")
    i = len(tokens)
    while i > 0 and _is_english_shaped(tokens[i - 1]):
        i -= 1
    suffix = tokens[i:]
    prefix = tokens[:i]
    if not suffix or not any(re.search(r"[A-Za-z]{3,}", t) for t in suffix):
        return None
    if not prefix or not any(looks_kruti(t) for t in prefix):
        return None
    return " ".join(suffix)


def translate_text(text):
    """Return English translation if known, else the original text."""
    norm = _normalize(text)
    if not norm:
        return text

    bilingual = _split_bilingual_suffix(norm)
    if bilingual is not None:
        return bilingual

    if norm in LEGACY_MAP:
        return LEGACY_MAP[norm]
    loose = re.sub(r"\s+", "", norm).lower()
    if loose in _LOOSE_MAP:
        return _LOOSE_MAP[loose]
    if _DEV_RE.search(norm):
        return _translate_devanagari(norm)

    # legacy Kruti Dev soup? convert token-by-token so mixed
    # Hindi+English cells keep their English parts intact
    tokens = norm.split(" ")
    is_soup = [looks_kruti(t) or _soup_by_glossary(t) for t in tokens]
    if any(is_soup):
        out = [
            kruti_to_unicode(t) if s else t
            for t, s in zip(tokens, is_soup)
        ]
        return _translate_devanagari(" ".join(out))

    return text


def translate_dataframe(df):
    """Translate legacy-Hindi cells in all text columns; unknown text is kept as-is."""
    import pandas.api.types as ptypes

    # iterate positionally — df[col] breaks on duplicate column names.
    # newer pandas returns StringDtype (not object) from cleaning, so
    # match any string-like column, not just object.
    for i in range(df.shape[1]):
        col = df.iloc[:, i]
        if col.dtype == object or ptypes.is_string_dtype(col):
            df.iloc[:, i] = col.map(
                lambda v: translate_text(v) if isinstance(v, str) else v
            )
    return df
