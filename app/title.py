"""The drawing's title says what the drawing is for: the properties Eagleye's per-pit drawing picker matches on.

'… FOR DA(0° - 2°) SUSPENSION TOWER DA-3M TO +9M EXTENSION FOR DRY FISSURED ROCK 1M RAISED CHIMNEY' →
tower type DA, angle 0-2°, extension -3 to +9 m, raised chimney 1 m, leg type Normal, soil DRY FISSURED ROCK (DFR).

Read from the title cell only, never from the file name (DocHub names are not reliable: "…-2127-CAT-A(1).pdf").
Anything not understood is left None and the title is 'partial', so the picker never matches on a guess."""
import re

NUM = r"[+-]?\d+(?:\.\d+)?"
# soil as printed → a short code. ponytail: only wordings seen on the CAT-A drawings; a new wording gives
# soil_code None ('partial') until it is added here.
SOIL_CODES = {
    "DRY FISSURED ROCK": "DFR", "DFR": "DFR", "WET FISSURED ROCK": "WFR", "WFR": "WFR",
    "DRY SOIL": "DRY", "WET SOIL": "WET", "PARTIALLY SUBMERGED SOIL": "PS", "FULLY SUBMERGED SOIL": "FS",
    "DRY BLACK COTTON SOIL": "DBC", "WET BLACK COTTON SOIL": "WBC",
    "SANDY DRY SOIL": "SANDY DRY", "SANDY WET SOIL": "SANDY WET", "SANDY FS SOIL": "SANDY FS",
}
PROPS = ("tower_type", "angle_from", "angle_to", "ext_from", "ext_to", "raised_chimney", "leg_type", "soil", "soil_code", "variant")


def parse(text):
    t = " ".join(text.upper().replace("_", " ").split())
    out = dict.fromkeys(PROPS)
    out["leg_type"] = "Unequal" if "UNEQUAL LEG" in t else "Normal"
    if m := re.search(r"\bFOR\s+(D[A-Z]\d?)\s*\(\s*(\d+)[^\d-]{0,3}-\s*(\d+)", t):
        out["tower_type"], out["angle_from"], out["angle_to"] = m[1], int(m[2]), int(m[3])
    # "-3M TO +9M" and "+6M & +9M" are ranges (the folders file "-3M & +3M" under "-3M TO +3"); "+9M" alone is one value
    if m := re.search(rf"({NUM})\s*M\s*(?:TO|&|AND)\s*({NUM})\s*M\s*EXTENSION", t):
        out["ext_from"], out["ext_to"] = float(m[1]), float(m[2])
    elif m := re.search(rf"({NUM})\s*M\s*EXTENSION", t):
        out["ext_from"] = out["ext_to"] = float(m[1])
    rc = re.search(r"(\d+(?:\.\d+)?)\s*M\s*RAISED\s*CHIMNEY", t)
    out["raised_chimney"] = float(rc[1]) if rc else 0.0  # no mention = normal chimney
    # the soil is what follows the last "FOR", unless that "FOR" introduces the tower itself
    tail = t.rsplit(" FOR ", 1)[-1] if t.count(" FOR ") >= 2 else ""
    if tail and not re.match(r"D[A-Z]\d?\s*\(", tail):
        tail = re.sub(r"[-\s]*\d+(?:\.\d+)?\s*M\s*RAISED\s*CHIMNEY.*$", "", tail)
        if v := re.search(r"\(([^)]*)\)", tail):  # "(ANCHOR TYPE)", "(SELF DRILL ANCHOR PRECAST BLOCK)"
            out["variant"] = "block" if "BLOCK" in v[1] else "anchor" if "ANCHOR" in v[1] else v[1].strip().lower()
            tail = tail[:v.start()]
        out["soil"] = tail.strip(" -.,") or None
        out["soil_code"] = SOIL_CODES.get(out["soil"])
    has_ext = out["ext_from"] is not None or out["leg_type"] == "Unequal"  # unequal-leg titles give no extension
    out["status"] = "ok" if out["tower_type"] and out["soil_code"] and has_ext else "partial"
    return {"text": text, **out}


def read(texts):
    """One title for the drawing from its sheets' title cells. Sheets that disagree on any property → 'conflict'."""
    texts = list(dict.fromkeys(t for t in texts if t))
    if not texts:
        return {"text": None, **dict.fromkeys(PROPS), "status": "missing"}
    parsed = [parse(t) for t in texts]
    first = parsed[0]
    if any({k: p[k] for k in PROPS} != {k: first[k] for k in PROPS} for p in parsed[1:]):
        return {**first, "status": "conflict", "texts": texts}
    return first
