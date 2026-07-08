import re
import ftfy
import pandas as pd


BAD_PATTERNS = [
    r"^nan$",
    r"^unnamed",
    r"^none$",
]

# Narrow-cell PDFs hard-wrap header text MID-WORD ("Grie\nvanc\nes \nbrou\nght")
# and the extractor preserves the real word boundaries as trailing spaces
# BEFORE the newline ("es \n") while mid-word wraps have none ("Grie\n").
# Collapsing every \n to a space (the old behavior) destroys that signal and
# turns "Grievances brought forward" into "Grie vanc es brou ght forw ard" —
# unrecoverable soup that downstream header naming can only call value/code.
# So BEFORE whitespace collapse: a \n flanked by lowercase letters on both
# sides (and no adjacent space) is a mid-word wrap — join with nothing.
# Everything else (space-adjacent, uppercase, digits, punctuation) keeps the
# old \n->space behavior, so stacked numbers ("100\n4") are NOT glued.
_MIDWORD_WRAP = re.compile(r"(?<=[a-z])\n(?=[a-z])")


def remove_empty(df):

    return (
        df
        .dropna(how="all")
        .dropna(axis=1, how="all")
    )


def normalize(df):

    def clean_cell(x):

        if pd.isnull(x):
            return ""

        text = str(x)

        text = ftfy.fix_text(text)

        # rejoin mid-word line wraps first — see _MIDWORD_WRAP above. Real
        # word boundaries survive as literal spaces and are normalized by
        # the \s+ collapse below exactly as before.
        text = _MIDWORD_WRAP.sub("", text)

        text = re.sub(
            r"\s+",
            " ",
            text
        )

        return text.strip()

    return df.map(clean_cell)


def is_garbage_row(row):

    text = " ".join(
        map(
            str,
            row.tolist()
        )
    ).lower()

    if re.fullmatch(
        r"(¿\d+à[\s,]*)+",
        text
    ):
        return True

    for pattern in BAD_PATTERNS:

        if re.search(
            pattern,
            text
        ):
            return True

    return False


def remove_garbage(df):

    rows = []

    for _, row in df.iterrows():

        if not is_garbage_row(row):

            rows.append(
                row.tolist()
            )

    if not rows:

        return pd.DataFrame(
            columns=df.columns
        )

    return pd.DataFrame(
        rows,
        columns=df.columns
    )


def clean_dataframe(df):

    df = remove_empty(df)

    df = normalize(df)

    df = remove_garbage(df)

    df = (
        df
        .drop_duplicates()
        .reset_index(
            drop=True
        )
    )

    return df