"""Ramboll (Anantapur) foundation-drawing layout: which cells hold what.

Every table is located by its header text, never by position on the page, so the same code reads the
text-layer and OCR pages. Values come out as candidate readings; checks.py decides what is released."""
import itertools
import re

from .cells import cands, nums
from .drawing_no import DRG

DIM_KEYS = {"D": "D", "H": "H", "G1": "G1", "G2": "G2", "G3": "G3", "DP": "dp", "S": "S", "B1": "B1", "B2": "B2",
            "B3": "B3", "H1": "H1", "EXT": "Ext", "E": "e"}
# label orders seen per column (DB2 swaps B1/B2); only used when the printed labels cannot be read
DIM_ORDERS = ([["D", "H", "G1", "G2", "G3", "dp"]], [["S", "B2", "B1", "B3", "H1", "Ext", "e"], ["S", "B1", "B2", "B3", "H1", "Ext", "e"]])
OCR_LABEL_FIX = {"81": "B1", "82": "B2", "83": "B3", "BI": "B1", "0P": "DP"}
BBS_HEADERS = {"dia": r"^DIA", "length": r"^LENGTH", "nos_leg": r"NOS\W*LEG\b(?!S)", "nos_legs": r"NOS.*(LEGS|TOWER)",
               "unit": r"^UNIT", "total": r"^TOTAL"}
EXC_HEADERS = {"plx_t": r"PLX.*TRAN", "plx_l": r"PLX.*LONG", "mlx_t": r"MLX.*TRAN", "mlx_l": r"MLX.*LONG", "nlx": r"^NLX"}
SINGLE = {"concrete": r"^CONCRETE \(CUM", "lean": r"^LEAN CONCRETE \(CUM", "reinf": r"^REINFORCEMENT \(KG",
          "excav": r"^EXCAVATION \(CUM", "sbc": r"^LIMIT BEARING", "upper_density": r"^UPPER LAYER DENSITY",
          "lower_density": r"^LOWER LAYER DENSITY", "beta": r"^UPPER LAYER ANGLE", "alfa": r"^LOWER LAYER ANGLE"}


def ext_label(t):
    """'BB(+) -3.0m B.E. + (+ 4.5m LE)' → (-3.0, 4.5); 'BB (+) 0m LE' / '-3m EXTENSION' → (0, n). None if unreadable."""
    t = re.sub(r"(?<![A-Z0-9])O(?=M)", "0", t.replace(" ", ""))
    n = [float(x) for x in re.findall(r"([+-]?\d+(?:\.\d+)?)M", t)]
    if "B.E" in t or "B,E" in t:
        return (n[0], n[1]) if len(n) == 2 else None
    return (0.0, n[0]) if len(n) == 1 else None


def parse(cells):
    T = lambda c: c["text"] if c else ""
    cy = lambda c: (c["box"][1] + c["box"][3]) / 2
    cx = lambda c: (c["box"][0] + c["box"][2]) / 2
    first = lambda pat: next((c for c in cells if re.search(pat, T(c))), None)
    header_row = lambda h: [c for c in cells if abs(c["box"][1] - h["box"][1]) < 20]
    rows_below = lambda h, lo, hi=1e9: sorted([c for c in cells if h["box"][0] <= cx(c) <= h["box"][2] and lo < cy(c) < hi], key=cy)
    at = lambda col, row: next((d for d in cells if col["box"][0] <= cx(d) <= col["box"][2] and d["box"][1] <= cy(row) <= d["box"][3]), None)

    def right(c):
        row = [d for d in cells if d["box"][0] >= c["box"][2] - 5 and d["box"][1] <= cy(c) <= d["box"][3]]
        return min(row, key=lambda d: d["box"][0], default=None)

    def columns(header, names):
        cols = {k: h for h in header_row(header) for k, pat in names.items() if re.search(pat, T(h))}
        return cols if len(cols) == len(names) else None

    p = {"drawing_no": [m for c in cells for t in (T(c), c.get("text2", "")) for m in DRG.findall(t)], "leg_group": "all"}
    if lab := first(r"^REVISION$"):  # revision table: entries stacked in the column above its label
        above = [c for c in cells if lab["box"][0] - 5 <= cx(c) <= lab["box"][2] + 5 and 0 <= lab["box"][1] - c["box"][3] < 400]
        rows = [(r["cands"][0], c) for c in above if (r := cands(c)) and r["agree"] and r["cands"][0] == int(r["cands"][0]) < 30]
        p["rev_table"] = sorted({v for v, _ in rows})
        if rows:  # the latest row's description: "ISSUED FOR APPROVAL", "RELEASED FOR CONSTRUCTION", …
            p["issued_for"] = T(right(max(rows, key=lambda r: r[0])[1])) or None
    for c in cells:
        t = T(c)
        if m := re.search(r"SHEET\s*(\d+)\s*[O0]F\s*(\d+)", t):
            p["sheet"] = (int(m[1]), int(m[2]))
        if m := re.match(r"REV\.?\W*(\d{1,2})\b", t):  # "Rev." and its value usually share a cell
            p["rev"] = {"cands": [float(m[1])], "agree": True}
        elif re.fullmatch(r"REV\.?\W*", t):  # value in its own cell, or in this one but lost by the text read
            own = cands(c)
            p["rev"] = own if own and own["cands"] else cands(right(c))
        if "FOUNDATION DRAWING" in t:  # title block: what the drawing is for, and which legs this sheet covers
            p["title"] = t
            p["leg_group"] = "uplift" if "UPLIFT" in t else "compression" if "COMP" in t else "all"
    for key, pat in SINGLE.items():
        if c := first(pat):
            p[key] = cands(right(c))
    if c := first(r"^SOIL TYPE"):
        p["soil_type"] = T(right(c))
    if c := first(r"^QUANTITY"):
        p["legs"] = 2 if "TWO" in T(c) else 4
    if (mark := first(r"^MARK$")) and (cols := columns(mark, BBS_HEADERS)):
        end = first(r"^TOTAL W")
        p["bbs"] = [{"mark": T(r), **{k: cands(at(col, r)) for k, col in cols.items()}}
                    for r in rows_below(mark, mark["box"][3], end["box"][1] if end else 1e9)]
    if (ext := first(r"LEG\s*&\s*BODY|^EXTENSION$")) and (cols := columns(ext, EXC_HEADERS)):
        p["exc"] = [{"ext": ext_label(T(r)), **{k: cands(at(col, r)) for k, col in cols.items()}}
                    for r in rows_below(ext, ext["box"][3])]
    if dim := first(r"^DIMENSIONS$"):
        p["dims"] = _dims(cells, dim, cx)
    return p


def _dims(cells, dim, cx):
    """Two label|value column pairs under DIMENSIONS → a list of candidate {label: value} readings.
    Labels are read, not assumed. If a column's labels are unreadable, every known order is offered and
    checks.py keeps the one the drawing's arithmetic accepts (and only if exactly one does)."""
    body = sorted([c for c in cells if abs(c["box"][1] - dim["box"][3]) < 40 and dim["box"][0] - 10 <= cx(c) <= dim["box"][2] + 10], key=cx)
    if len(body) != 4:
        return None
    columns = []
    for (lab, val), orders in zip(((body[0], body[1]), (body[2], body[3])), DIM_ORDERS):
        tokens = [OCR_LABEL_FIX.get(t, t) for t in re.sub(r"[.\"']", "", lab["text"]).split()]
        read = [DIM_KEYS[t] for t in tokens] if tokens and all(t in DIM_KEYS for t in tokens) else None
        columns.append([(labels, read is not None, val) for labels in ([read] if read else orders)])
    options = []
    for combo in itertools.product(*columns):
        out = {}
        for labels, label_read, val in combo:
            good = [r for r in nums(val) if len(r) == len(labels)]
            for i, k in enumerate(labels):
                vals = list(dict.fromkeys(r[i] for r in good))
                out[k] = {"cands": vals, "agree": label_read and len(nums(val)) == 1 and len(vals) == 1}
        options.append(out)
    return options
