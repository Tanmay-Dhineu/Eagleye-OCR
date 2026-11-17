"""Page → table cells.

Cells are found on the rendered page as blank rectangles bounded by ruling lines. Their text comes from
the PDF's text layer when the page has one (exact), otherwise from Tesseract. Numeric cells are read
lazily (see nums()); on the OCR path they are read twice (1× and 2×) so a disagreement can be settled
by the drawing's own arithmetic in checks.py instead of a guess."""
import re
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pdfplumber
import pypdfium2 as pdfium
import pytesseract
from PIL import Image, ImageFilter
from scipy import ndimage as nd

from . import settings
from .drawing_no import DRG

FLOAT = re.compile(r"\d+(?:\.\d+)?")
NUM = "--psm 7 -c tessedit_char_whitelist=0123456789."
NUMS = "--psm 6 -c tessedit_char_whitelist=0123456789.m"
_pool = ThreadPoolExecutor(settings.OCR_THREADS)


def norm(t):
    return re.sub(r"\s+", " ", t).strip().upper()


def ocr(img, cfg):
    try:
        return pytesseract.image_to_string(img, config=cfg, timeout=10).strip()
    except RuntimeError:  # ponytail: 10 s cap per cell; dense stamps and notes are not values we read
        return ""


def x2(img):
    return img.resize((img.width * 2, img.height * 2), Image.LANCZOS)


def find_cells(img, dpi):
    """Closed, mostly blank rectangles bounded by long ruling lines."""
    a = (np.array(img) < 140).astype(np.uint8)
    H, W = a.shape
    k = round(dpi * 0.15)  # a ruling line is at least this long (60 px at 400 dpi)
    # scans: bridge 1-2 px breaks (speckle, faint toner) along the line direction before measuring runs
    h = nd.maximum_filter1d(nd.minimum_filter1d(nd.maximum_filter1d(a, 3, axis=1), k, axis=1), k, axis=1)
    v = nd.maximum_filter1d(nd.minimum_filter1d(nd.maximum_filter1d(a, 3, axis=0), k, axis=0), k, axis=0)
    lab, _ = nd.label(~nd.binary_dilation((h | v).astype(bool), iterations=2))
    cells = []
    for i, sl in enumerate(nd.find_objects(lab), 1):
        hh, ww = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        if not (20 < hh < 0.15 * H and 50 < ww < 0.3 * W) or not 0.003 < a[sl].mean() < 0.25:
            continue
        if nd.binary_fill_holes(lab[sl] == i).mean() < 0.95:  # not a rectangle
            continue
        cells.append({"box": (sl[1].start, sl[0].start, sl[1].stop, sl[0].stop)})
    return cells


def _read_text(c, pg, scale):
    x0, y0, x1, y1 = (v / scale for v in c["box"])
    raw = pg.crop((max(0, x0), max(0, y0), min(pg.width, x1), min(pg.height, y1))).extract_text() or ""
    c["text"] = norm(raw)
    c["nums"] = [[float(n) for n in FLOAT.findall(raw)]]


def _padded(page, box):
    x0, y0, x1, y1 = box
    out = Image.new("L", (x1 - x0 + 40, y1 - y0 + 40), 255)
    out.paste(page.crop(box), (20, 20))
    return out


def _read_ocr(c):
    c["text"] = norm(ocr(c["img"], "--psm 6") or ocr(c["img"], "--psm 7"))
    if re.search(r"DRG|A2TL|-F[ND]{2}-", c["text"]):  # title cells: a second, independent reading of the number
        c["text2"] = norm(ocr(x2(c["img"]), "--psm 6"))


def nums(c):
    """Distinct numeric readings of a cell, each a list of floats. Text path: one exact reading."""
    if "nums" not in c:
        reads = [ocr(im, NUM if len(c["text"]) <= 8 else NUMS) for im in (c["img"], x2(c["img"]))]
        c["nums"] = [list(t) for t in dict.fromkeys(tuple(float(n) for n in FLOAT.findall(r)) for r in reads)]
    return c["nums"]


def cands(c):
    """Candidate single values of a cell and whether its readings agreed. None if no cell."""
    if c is None:
        return None
    vals = list(dict.fromkeys(r[0] for r in nums(c) if len(r) == 1))
    return {"cands": vals, "agree": len(nums(c)) == 1 and len(vals) == 1}


def deskew(img):
    """Scans are never square to the page. Pick the small angle that makes dark rows sharpest (table lines
    and text lines then run exactly horizontal); 0 when the page is already straight."""
    small = img.resize((img.width // 4, img.height // 4))

    def sharpness(angle):
        rows = (np.array(small.rotate(angle, fillcolor=255)) < 140).sum(axis=1).astype(np.int64)
        return int((rows ** 2).sum())

    angle = max(np.round(np.arange(-2, 2.01, 0.1), 1), key=lambda a: (sharpness(a), -abs(a)))
    return (img.rotate(angle, resample=Image.BICUBIC, fillcolor=255), float(angle)) if angle else (img, 0.0)


def _ocr_page(img, dpi):
    img, _ = deskew(img.filter(ImageFilter.MedianFilter(3)))  # median: scanner speckle out, strokes kept
    try:
        osd = pytesseract.image_to_osd(img.resize((img.width // 2, img.height // 2)), config="--psm 0")
        rot = int(re.search(r"Rotate: (\d+)", osd).group(1))
    except (pytesseract.TesseractError, AttributeError):
        rot = 0
    for extra in (0, 180, 90, 270):  # OSD can be 180° off on a drawing; the drawing number tells us
        r = (rot + extra) % 360
        page = img.rotate(-r, expand=True) if r else img
        cells = find_cells(page, dpi)
        for c in cells:
            c["img"] = _padded(page, c["box"])
        list(_pool.map(_read_ocr, cells))
        if any(DRG.search(c["text"]) for c in cells):
            return cells, r
    return cells, rot


def page_texts(pdf_path):
    """{page_no: whole-page text} for pages with a text layer. The general notes (stub cover, …) are free text outside
    the ruled cells, so pages() never sees them. Scanned pages are left out: their notes are not read."""
    with pdfplumber.open(str(pdf_path)) as pl:
        return {i + 1: p.dedupe_chars().extract_text() or "" for i, p in enumerate(pl.pages) if len(p.chars) > 50}


def pages(pdf_path, force_ocr=False):
    """Yield (page_no, mode, rotation, cells) for every page of the PDF."""
    dpi, scale = settings.DPI, settings.DPI / 72
    doc = pdfium.PdfDocument(str(pdf_path))
    with pdfplumber.open(str(pdf_path)) as pl:
        for i in range(len(doc)):
            img = doc[i].render(scale=scale).to_pil().convert("L")
            if not force_ocr and len(pl.pages[i].chars) > 50:
                pg = pl.pages[i].dedupe_chars()  # overprinted rows would otherwise read "33661100" for 3610
                cells = find_cells(img, dpi)
                for c in cells:
                    _read_text(c, pg, scale)
                yield i + 1, "text", 0, cells
            else:
                cells, rot = _ocr_page(img, dpi)
                yield i + 1, "ocr", rot, cells
    doc.close()
