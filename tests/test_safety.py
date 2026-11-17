"""The promise is not "never misreads" but "never releases a misread value". These tests break the input on
purpose and check that every released value is still right; anything the pipeline cannot vouch for is flagged."""
import random
import shutil
import zlib
from pathlib import Path

import pdfplumber
import pytest
from PIL import Image, ImageFilter

from app import cells
from app.extract import extract
from tests.truth import dc1_truth, score, text_truth

SAMPLES = Path(__file__).parent / "samples"
DA = SAMPLES / "DA -3m to +3m - WFR.pdf"
DC1 = SAMPLES / "DC1 1M RC - DFR.pdf"
TEXT_PDFS = ["DA -3m to +3m - WFR.pdf", "DA -3m to +9m - 1.5M RC DFR.pdf", "DB1 ULE 1M RC DFR.pdf", "DB2 ULE 1M RC DRY_R1.pdf"]
needs_tesseract = pytest.mark.skipif(shutil.which("tesseract") is None, reason="needs tesseract")
REPORT = []


def flat(result):
    return {(s["page"], k): v for s in result["sheets"] for k, v in s["values"].items()}


@pytest.fixture(scope="module")
def clean_da():
    return flat(extract(DA))


# ---- 1. one wrong number in the drawing (text path): its whole group must be flagged, nothing else may change ----
@pytest.mark.parametrize("old,new,group", [
    (4330.0, 4380.0, "bbs0."),   # BBS mark A length  → weight check fails
    (84.0, 48.0, "bbs0."),       # BBS nos/leg        → nos per tower check fails
    (4.43, 4.48, "dims"),        # B1                 → concrete + lean identities fail
    (0.24, 0.42, "dims"),        # G2                 → D = H+G3+G2+G1+dp fails
    (12342.0, 12392.0, "exc0."), # NLX                → NLX = √(MLX_T² + MLX_L²) fails
    (9403.0, 9408.0, "exc0."),   # MLX,TRAN           → same
])
def test_a_corrupted_number_is_flagged_not_released(monkeypatch, clean_da, old, new, group):
    orig = cells._read_text

    def corrupted(c, pg, scale):
        orig(c, pg, scale)
        c["nums"] = [[new if v == old else v for v in r] for r in c["nums"]]

    monkeypatch.setattr(cells, "_read_text", corrupted)
    bad = flat(extract(DA))
    assert bad.keys() == clean_da.keys()
    changed = {k for k in bad if bad[k] != clean_da[k]}
    assert changed, "the corruption had no effect"
    for k in changed:  # every difference is a refusal, never a different released value
        assert bad[k] == {"value": None, "status": "flagged"}, (k, bad[k], clean_da[k])
    dims = {"D", "H", "G1", "G2", "G3", "dp", "S", "B1", "B2", "B3", "H1", "concrete", "lean"}
    # the bad number's own group; a flagged BBS row also leaves the reinforcement total unverifiable
    allowed = lambda f: f.startswith(group) or (group == "dims" and f in dims) or (group.startswith("bbs") and f == "reinf")
    assert all(allowed(k[1]) for k in changed), sorted(k for k in changed if not allowed(k[1]))


# ---- 2. OCR misreads digits (simulated on top of real Tesseract output) ----
@needs_tesseract
def test_ocr_digit_errors_are_never_released(monkeypatch):
    """10% of numeric OCR readings get one digit changed (deterministically per image)."""
    orig = cells.ocr

    def noisy(img, cfg):
        out = orig(img, cfg)
        h = zlib.crc32(img.tobytes() + cfg.encode())
        digits = [i for i, ch in enumerate(out) if ch.isdigit()]
        if cfg in (cells.NUM, cells.NUMS) and digits and h % 10 == 0:
            i = digits[h // 10 % len(digits)]
            out = out[:i] + str((int(out[i]) + 1 + h // 100 % 9) % 10) + out[i + 1:]
        return out

    monkeypatch.setattr(cells, "ocr", noisy)
    tally, bad = score(extract(DC1), [dc1_truth(n) for n in range(1, 5)])
    REPORT.append(("DC1 + 10% simulated digit errors", tally))
    assert tally["wrong"] == 0, "\n".join(bad)


# ---- 3. worse scans than DC1 ----
def _noise_jpeg(img):
    rng = random.Random(7)
    noisy = img.point(lambda p: p)  # copy
    px = noisy.load()
    for _ in range(img.width * img.height // 40):  # salt-and-pepper speckle on 2.5% of pixels
        px[rng.randrange(img.width), rng.randrange(img.height)] = rng.choice((0, 255))
    from io import BytesIO
    buf = BytesIO()
    noisy.save(buf, "JPEG", quality=35)
    return Image.open(BytesIO(buf.getvalue())).convert("L")


def _scanned_at(dpi):
    """A scan made at `dpi`: the service still renders at 400 dpi, so the page arrives enlarged and soft."""
    return lambda im: im.resize((im.width * dpi // 400, im.height * dpi // 400), Image.BILINEAR).resize(im.size, Image.BICUBIC)


def _skew(deg):
    return lambda im: im.rotate(deg, resample=Image.BICUBIC, expand=True, fillcolor=255)


DEGRADE = {
    "scanned at 300 dpi": _scanned_at(300),
    "scanned at 200 dpi": _scanned_at(200),
    "blur r=1.2": lambda im: im.filter(ImageFilter.GaussianBlur(1.2)),
    "speckle + jpeg q35": _noise_jpeg,
    "skew 0.4°": _skew(0.4),
    "skew -1.5°": _skew(-1.5),
    "200 dpi + skew 0.8° + speckle": lambda im: _noise_jpeg(_skew(0.8)(_scanned_at(200)(im))),
}


@needs_tesseract
@pytest.mark.parametrize("name", DEGRADE)
def test_degraded_scans_release_nothing_wrong(monkeypatch, name):
    orig = cells._ocr_page
    monkeypatch.setattr(cells, "_ocr_page", lambda img, dpi: orig(DEGRADE[name](img), dpi))
    result = extract(DC1)
    tally, bad = score(result, [dc1_truth(n) for n in range(1, 5)])
    REPORT.append((f"DC1 {name}", tally))
    assert result["drawing_no"] in ("RL-A2TL-TL-CIV-FND-2312", None)  # a wrong drawing number would be the worst failure
    assert tally["wrong"] == 0, "\n".join(bad)


# ---- 4. the four text PDFs, OCR'd as if they were scans (the code was never tuned on these) ----
@needs_tesseract
@pytest.mark.parametrize("name", TEXT_PDFS)
def test_unseen_drawings_through_ocr(name):
    path = SAMPLES / name
    result = extract(path, force_ocr=True)
    with pdfplumber.open(path) as pl:
        truths = [text_truth(pg) for pg in pl.pages]
    tally, bad = score(result, truths)
    REPORT.append((f"{name} forced OCR", tally))
    assert result["drawing_no"] == truths[0]["drawing_no"][0]
    assert tally["wrong"] == 0, "\n".join(bad)
    assert tally["correct"] >= 0.85 * sum(tally.values()), "\n".join(bad)  # measured 90-100%; the misses are flagged


def teardown_module():
    if REPORT:
        print("\n\nscenario | correct | wrong | unverified_wrong | flagged | missing | withheld | filled")
        for name, t in REPORT:
            print(f"{name} | {t['correct']} | {t['wrong']} | {t['unverified_wrong']} | {t['flagged']} | {t['missing']} | {t['withheld']} | {t['correct'] / sum(t.values()):.0%}")
