"""Drawing numbers: what the PDFs print (RL-A2TL-TL-CIV-FND-2009) and what site engineers type."""
import re

DRG = re.compile(r"(?<![A-Z0-9])[A-Z]{2}-[A-Z0-9]{3,6}-[A-Z]{2}-[A-Z]{3}-F[ND]{2}-\d{4}(?!\d)")  # never a truncated serial


def canonical(typed):
    """Upper-case, `-` as the only separator (spaces around dashes dropped, other spaces and `_` → `-`),
    no `REV n` suffix; a well-formed number embedded in extra text ("…-2109_DA 6m & 9m") is cut out."""
    s = re.sub(r"\s*-\s*", "-", typed.strip().upper())
    s = re.sub(r"-*REV\s*-?\s*\d+$", "", s)
    s = re.sub(r"-+", "-", re.sub(r"[\s_]+", "-", s)).strip("-")
    m = DRG.search(s)
    return m.group() if m else s


def split(typed):
    """One typed field can name several drawings: "…-2102 & …-2122", "…-2202, …-2232"."""
    parts = [p for p in re.split(r"[,&;/]|\bAND\b", typed.upper()) if re.search(r"\d{4}", p)]
    return parts or [typed]


def loose_key(s):
    """Project code + serial: what survives the FDN/FND swap and a dropped `TL-`."""
    parts = canonical(s).split("-")
    m = re.search(r"\d{4}$", parts[-1])
    return ("-".join(parts[:2]), m.group()) if len(parts) >= 3 and m else None


def consensus(per_sheet):
    """per_sheet: the numbers read on each sheet (title block, note 7, OCR re-read). The drawing number is the
    one number found on every sheet; a misread on one sheet is outvoted, but never guessed."""
    if not per_sheet or not all(per_sheet):
        return None, "drawing_no_missing" if not any(per_sheet) else "drawing_no_incomplete"
    common = set.intersection(*(set(r) for r in per_sheet))
    return (common.pop(), "ok") if len(common) == 1 else (None, "drawing_no_conflict")


def match(typed, index):
    """index: {canonical drawing no: entry}. Exact → matched; same project+serial → probable; several →
    ambiguous; else missing. A field naming several drawings → multiple, with one match per number."""
    parts = split(typed)
    if len(parts) > 1:
        return {"status": "multiple", "drawing_no": None, "candidates": [], "parts": [match(p, index) | {"typed": p.strip()} for p in parts]}
    c = canonical(typed)
    if c in index and index[c].get("ambiguous"):
        same_rev = [{**o, "drawing_no": c} for o in index[c]["others"] if o["rev"] == index[c]["rev"]]
        return {"status": "ambiguous", "drawing_no": None, "candidates": [index[c], *same_rev]}
    if c in index and not index[c].get("approved", True):
        return {"status": "not_approved", "drawing_no": None, "candidates": [index[c]]}
    if c in index:
        return {"status": "matched", "drawing_no": c, "candidates": [index[c]]}
    key = loose_key(c)
    cands = [e for k, e in index.items() if key and loose_key(k) == key]
    if len(cands) == 1:
        return {"status": "probable", "drawing_no": None, "candidates": cands}
    if cands:
        return {"status": "ambiguous", "drawing_no": None, "candidates": cands}
    return {"status": "missing", "drawing_no": None, "candidates": []}
