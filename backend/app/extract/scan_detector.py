"""Detect scanned / image-only PDFs (and pages) that carry no text layer.

Camelot reads a PDF's TEXT layer; a scanned document is just page images with
no extractable text, so Camelot returns nothing. This module flags such PDFs up
front so the pipeline can route them to OCR (or tell the user) instead of
silently producing zero tables.

Signals, per page (via pdfplumber):
  - char_count: glyphs with a real text layer
  - image_area_ratio: fraction of the page covered by raster images

A page is:
  - "blank"   : almost no text and no big image
  - "image"   : almost no text but a large image (a scan)
  - "text"    : has a usable text layer
A PDF is "scanned" when image-only pages dominate the non-blank pages.
"""

# a page with fewer than this many characters has effectively no text layer
# (a true scan has ~0; digital infographic pages still carry labels/captions)
_MIN_CHARS = 6
# an image must cover ~the whole page to mark it a scan — partial figures and
# charts on otherwise-digital pages cover far less
_IMG_COVER = 0.8


def _page_kind(page):
    chars = len(page.chars)
    if chars >= _MIN_CHARS:
        return "text", chars
    # near-empty text layer — image or blank?
    page_area = (page.width or 1) * (page.height or 1)
    img_area = 0.0
    for im in (page.images or []):
        w = max(0.0, float(im.get("x1", 0)) - float(im.get("x0", 0)))
        h = max(0.0, float(im.get("bottom", 0)) - float(im.get("top", 0)))
        img_area += w * h
    cover = img_area / page_area if page_area else 0.0
    if cover >= _IMG_COVER:
        return "image", chars
    return "blank", chars


def analyze_pdf(path, max_pages=60):
    """Classify a PDF's pages. Returns a summary dict.

    {pages_total, pages_checked, text, image, blank, scanned_fraction, verdict,
     scanned_page_numbers}
    verdict ∈ {"text", "scanned", "mixed", "empty"}.
    """
    import pdfplumber

    text = image = blank = 0
    scanned_pages = []
    pages_total = 0
    pages_checked = 0
    with pdfplumber.open(path) as pdf:
        pages_total = len(pdf.pages)
        for i, page in enumerate(pdf.pages):
            if i >= max_pages:
                break
            pages_checked += 1
            try:
                kind, _ = _page_kind(page)
            except Exception:
                # a page whose fonts/content stream fail to parse still HAS a
                # text layer (pdfminer choked decoding it) — treat as text, not
                # a scan, so a parser quirk never mislabels a digital PDF.
                kind = "text"
            if kind == "text":
                text += 1
            elif kind == "image":
                image += 1
                scanned_pages.append(i + 1)
            else:
                blank += 1

    content = text + image  # ignore blank pages when judging
    scanned_fraction = round(image / content, 3) if content else 0.0
    if content == 0:
        verdict = "empty"
    elif scanned_fraction >= 0.6:
        verdict = "scanned"
    elif scanned_fraction >= 0.15:
        verdict = "mixed"
    else:
        verdict = "text"

    return {
        "pages_total": pages_total,
        "pages_checked": pages_checked,
        "text": text,
        "image": image,
        "blank": blank,
        "scanned_fraction": scanned_fraction,
        "verdict": verdict,
        "scanned_page_numbers": scanned_pages[:50],
    }


def is_scanned(path, max_pages=60):
    """True when the PDF is image-only enough that Camelot will find no tables."""
    return analyze_pdf(path, max_pages)["verdict"] in ("scanned", "empty")


if __name__ == "__main__":
    import json
    import sys
    print(json.dumps(analyze_pdf(sys.argv[1],
                                 int(sys.argv[2]) if len(sys.argv) > 2 else 60), indent=1))
