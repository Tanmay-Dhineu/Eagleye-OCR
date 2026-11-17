import shutil
from pathlib import Path

import pdfplumber
import pytest

from app.extract import extract
from tests.truth import dc1_truth, score, text_truth

SAMPLES = Path(__file__).parent / "samples"
TEXT_PDFS = ["DA -3m to +3m - WFR.pdf", "DA -3m to +9m - 1.5M RC DFR.pdf", "DB1 ULE 1M RC DFR.pdf", "DB2 ULE 1M RC DRY_R1.pdf"]


@pytest.mark.parametrize("name", TEXT_PDFS)
def test_text_pdf_is_exact(name):
    path = SAMPLES / name
    result = extract(path)
    with pdfplumber.open(path) as pl:
        truths = [text_truth(pg) for pg in pl.pages]
    tally, bad = score(result, truths)
    assert result["problems"] == [] and result["drawing_no"] == truths[0]["drawing_no"][0]
    assert all(s["mode"] == "text" for s in result["sheets"])
    assert tally["wrong"] == 0 and tally["correct"] == sum(tally.values()), "\n".join(bad)


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="needs tesseract")
def test_dc1_ocr():
    result = extract(SAMPLES / "DC1 1M RC - DFR.pdf")
    tally, bad = score(result, [dc1_truth(n) for n in range(1, 5)])
    assert result["problems"] == [] and result["drawing_no"] == "RL-A2TL-TL-CIV-FND-2312"
    assert all(s["mode"] == "ocr" for s in result["sheets"])
    assert tally["wrong"] == 0, "\n".join(bad)
    assert tally["correct"] >= 0.98 * sum(tally.values()), "\n".join(bad)


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="needs tesseract (the blank page has no text layer)")
def test_pages_that_are_not_drawing_sheets_are_set_aside_but_a_lost_sheet_still_blocks(tmp_path):
    """DocHub's 2003 carries a 14-page design report, 2123 a scanned page after its sheets."""
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(SAMPLES / "DA -3m to +3m - WFR.pdf")
    pdf.new_page(842, 595)  # an extra page with nothing on it
    pdf.save(tmp_path / "extra.pdf")
    result = extract(tmp_path / "extra.pdf")
    assert result["problems"] == [] and result["other_pages"] == [3] and len(result["sheets"]) == 2
    pdf = pdfium.PdfDocument(SAMPLES / "DA -3m to +3m - WFR.pdf")
    pdf.del_page(1)
    pdf.new_page(842, 595)  # sheet 2 replaced by a page that reads as nothing
    pdf.save(tmp_path / "lost.pdf")
    assert "sheets_incomplete" in extract(tmp_path / "lost.pdf")["problems"]
