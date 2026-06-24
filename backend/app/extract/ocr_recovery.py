"""
Recover a font-corrupted table by OCR-ing the rendered glyphs.

When a table's text layer is Kruti-Dev soup or mangled Devanagari, the correct
characters exist only as rendered pixels. We render the table's page region,
run Tesseract (hin+eng), bucket the recognised words back into Camelot's column
x-ranges and row y-ranges, and transliterate Devanagari to readable ASCII Latin
(so the cell is usable and no longer trips the corruption checks).

Best-effort: returns None when Tesseract is unavailable or recognises nothing,
so the caller falls back to quarantining the table.
"""

import re
import shutil
import subprocess
import tempfile
import os

# ---- Devanagari -> ASCII (Hunterian-ish) transliteration -------------------
_VOWEL = {
    "अ": "a", "आ": "aa", "इ": "i", "ई": "ii", "उ": "u", "ऊ": "uu",
    "ऋ": "ri", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "ऑ": "o", "ऍ": "e",
}
_MATRA = {
    "ा": "aa", "ि": "i", "ी": "ii", "ु": "u", "ू": "uu", "ृ": "ri",
    "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ॉ": "o", "ॅ": "e",
}
_CONS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "ng",
    "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "ny",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ल": "l", "व": "v", "ळ": "l",
    "श": "sh", "ष": "sh", "स": "s", "ह": "h",
    "क़": "q", "ख़": "kh", "ग़": "g", "ज़": "z", "ड़": "r", "ढ़": "rh", "फ़": "f",
}
_SIGN = {"ं": "n", "ः": "h", "ँ": "n", "ॐ": "om", "।": ".", "॥": ".", "़": ""}
_DIGIT = {"०": "0", "१": "1", "२": "2", "३": "3", "४": "4",
          "५": "5", "६": "6", "७": "7", "८": "8", "९": "9"}
_VIRAMA = "्"
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def transliterate(text):
    """Devanagari -> ASCII Latin; non-Devanagari characters pass through."""
    if not _DEVANAGARI.search(str(text)):
        return text
    s = str(text)
    out = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        nxt = s[i + 1] if i + 1 < n else ""
        if ch in _CONS:
            base = _CONS[ch]
            if nxt == _VIRAMA:
                out.append(base)
                i += 2
                continue
            if nxt in _MATRA:
                out.append(base + _MATRA[nxt])
                i += 2
                continue
            out.append(base + "a")
            i += 1
            continue
        if ch in _VOWEL:
            out.append(_VOWEL[ch])
        elif ch in _SIGN:
            out.append(_SIGN[ch])
        elif ch in _DIGIT:
            out.append(_DIGIT[ch])
        elif ch == _VIRAMA:
            pass
        else:
            out.append(ch)
        i += 1
    return re.sub(r"\s+", " ", "".join(out)).strip()


# ---- OCR -------------------------------------------------------------------
def tesseract_available():
    return shutil.which("tesseract") is not None


def _ocr_words(pil_image):
    """Run Tesseract TSV on a PIL image; return [(cx, cy, text), …] in image px
    for confident word boxes (cx, cy = word-box centre)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    try:
        pil_image.save(tmp.name)
        tmp.close()
        proc = subprocess.run(
            ["tesseract", tmp.name, "stdout", "-l", "hin+eng", "--psm", "6", "tsv"],
            capture_output=True, text=True, timeout=90,
        )
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    words = []
    for line in proc.stdout.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 12:
            continue
        try:
            left, top, w, h = (int(parts[6]), int(parts[7]), int(parts[8]), int(parts[9]))
            conf = float(parts[10])
        except ValueError:
            continue
        text = parts[11].strip()
        if not text or conf < 40:
            continue
        words.append((left + w / 2.0, top + h / 2.0, text))
    return words


def recover_table(plumber_page, table, dpi=300):
    """Rebuild a corrupt Camelot table from OCR of its rendered region.

    table: the Camelot Table (needs ._bbox, .cols, .rows). Returns a list-of-rows
    grid (transliterated), or None when recovery is not possible / not confident.
    """
    if not tesseract_available():
        return None
    bbox = getattr(table, "_bbox", None)
    cols = getattr(table, "cols", None)
    rows = getattr(table, "rows", None)
    if not bbox or not cols or not rows:
        return None

    try:
        x0, y0, x1, y1 = bbox
        ph = plumber_page.height
        crop_top = max(0, ph - y1 - 2)
        crop_bot = min(ph, ph - y0 + 2)
        region = plumber_page.crop((max(0, x0 - 2), crop_top,
                                    min(plumber_page.width, x1 + 2), crop_bot))
        image = region.to_image(resolution=dpi).original
    except Exception:
        return None

    words = _ocr_words(image)
    if not words:
        return None

    scale = dpi / 72.0
    grid = [["" for _ in cols] for _ in rows]
    for cx_px, cy_px, text in words:
        # image px (relative to the crop) -> PDF coords of the page
        pdf_x = (x0 - 2) + cx_px / scale
        pdf_y = y1 + 2 - cy_px / scale            # camelot y is bottom-up
        ci = next((j for j, (a, b) in enumerate(cols) if a - 3 <= pdf_x <= b + 3), None)
        ri = next((k for k, (t, b) in enumerate(rows) if b - 3 <= pdf_y <= t + 3), None)
        if ci is None or ri is None:
            continue
        cell = grid[ri][ci]
        grid[ri][ci] = (cell + " " + text).strip() if cell else text

    # transliterate every cell; drop fully-empty rows
    out = [[transliterate(c) for c in row] for row in grid]
    out = [row for row in out if any(c.strip() for c in row)]
    return out or None
