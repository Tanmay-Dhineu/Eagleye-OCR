"""Whole-drawing extraction: every sheet, then drawing-level consistency."""
import hashlib

from . import cells, checks, ramboll, title
from .drawing_no import consensus

PARSER_VERSION = 3  # 2: drawing title properties; 3: pages that are not drawing sheets are set aside


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def extract(pdf_path, force_ocr=False):
    sheets, problems = [], []
    for page_no, mode, rot, page_cells in cells.pages(pdf_path, force_ocr):
        p = ramboll.parse(page_cells)
        values, status = checks.resolve(p, ocr=mode == "ocr")
        sheets.append({
            "page": page_no, "mode": mode, "rotation": rot, "sheet": p.get("sheet"),
            "kind": "detail" if p.get("dims") or p.get("bbs") else "excavation" if p.get("exc") else "unknown",
            "leg_group": p["leg_group"], "legs": p.get("legs"),
            "drawing_no_readings": sorted(set(p["drawing_no"])),
            "rev": values.get("rev"), "issued_for": p.get("issued_for"), "title": p.get("title"),
            "values": {k: {"value": v, "status": status[k]} for k, v in values.items()},
        })
    # A PDF can carry pages that are not drawing sheets: DocHub's 2003 has a 14-page design report before its two
    # sheets, 2123 a scanned page after them. A page with no sheet marker, drawing number, title or known table is
    # set aside (listed in other_pages). A real sheet read as nothing still fails "sheets_incomplete" below.
    other = [s["page"] for s in sheets if not (s["sheet"] or s["drawing_no_readings"] or s["title"] or s["kind"] != "unknown")]
    sheets = [s for s in sheets if s["page"] not in other]
    drawing_no, dn_status = consensus([s["drawing_no_readings"] for s in sheets])
    if dn_status != "ok":
        problems.append(dn_status)
    totals = {s["sheet"][1] for s in sheets if s["sheet"]}
    numbers = sorted(s["sheet"][0] for s in sheets if s["sheet"])
    if len(totals) != 1 or numbers != list(range(1, next(iter(totals), 0) + 1)):
        problems.append("sheets_incomplete")
    revs = {s["rev"] for s in sheets if s["rev"] is not None}  # a sheet whose revision is flagged does not vote
    if len(revs) != 1:
        problems.append("rev_inconsistent")
    issued = {s["issued_for"] for s in sheets if s["issued_for"]}
    flagged = sum(1 for s in sheets for v in s["values"].values() if v["status"] == "flagged")
    return {
        "parser_version": PARSER_VERSION, "drawing_no": drawing_no, "rev": revs.pop() if len(revs) == 1 else None,
        "issued_for": issued.pop() if len(issued) == 1 else None, "title": title.read([s["title"] for s in sheets]),
        "sheets": sheets, "other_pages": other, "problems": problems,
        "summary": {"fields": sum(len(s["values"]) for s in sheets), "flagged": flagged},
    }
