"""Independent answer keys. Text PDFs: parsed from the PDF's own text layer with pdfplumber (never used by the
extractor to locate anything). DC1 has no text layer: values read off the drawing by hand."""
import re

from app.cells import FLOAT
from app.drawing_no import DRG

SINGLE = {"concrete": r"(?<!LEAN )CONCRETE \(cum\) ([\d.]+)", "lean": r"LEAN CONCRETE \(cum\) ([\d.]+)",
          "reinf": r"REINFORCEMENT \(kg\) ([\d.]+)", "excav": r"EXCAVATION \(cum\) ([\d.]+)",
          "sbc": r"CAPACITY\(T/m\^2\) ([\d.]+)", "upper_density": r"UPPER LAYER DENSITY OF SOIL \(kg/m\^3\) ([\d.]+)",
          "lower_density": r"LOWER LAYER DENSITY OF SOIL \(kg/m\^3\) ([\d.]+)", "beta": r"\(BETA\) \(DEG\) ([\d.]+)",
          "alfa": r"\(ALFA\) \(DEG\) ([\d.]+)"}
BBS = ("mark", "dia", "length", "nos_leg", "nos_legs", "unit", "total")
EXC = ("plx_t", "plx_l", "mlx_t", "mlx_l", "nlx")


def text_truth(pg):
    pg = pg.dedupe_chars()
    t = pg.extract_text()
    T = {"drawing_no": sorted(set(DRG.findall(t)))}
    if m := re.search(r"SHEET (\d+) OF (\d+)", t):
        T["sheet"] = (int(m[1]), int(m[2]))
    if m := re.search(r"Rev\. (\d+)", t):
        T["rev"] = float(m[1])
    rows = []  # a row's mark and its numbers can sit on different text lines, so use table cells
    for tb in pg.find_tables():
        for r in tb.extract():
            c = [x.strip() for x in r if x and x.strip()]
            if len(c) >= 7 and re.fullmatch(r"[A-E]\d?", c[0]) and all(FLOAT.fullmatch(x) for x in [c[1]] + c[-5:]) and c not in rows:
                rows.append(c)
    for i, c in enumerate(rows):
        T |= {f"bbs{i}.{k}": (v if k == "mark" else float(v)) for k, v in zip(BBS, [c[0], c[1]] + c[-5:])}
    for m in re.finditer(r"(?<![\w.])(D|H|G1|G2|G3|dp|S|B1|B2|B3|H1|Ext\.|e) (\d+(?:\.\d+)?) (m|mm)\b", t):
        T[m[1].rstrip(".")] = float(m[2])
    for k, pat in SINGLE.items():
        if m := re.search(pat, t):
            T[k] = float(m[1])
    if m := re.search(r"SOIL TYPE ([A-Z ]+?)\s*$", t, re.M):
        T["soil_type"] = m[1].strip()
    for i, m in enumerate(re.finditer(r"(?m)\S*X\S*X\S* (\d{4,5}) (\d{4,5}) (\d{4,5}) (\d{4,5}) (\d{4,5})", t)):
        T |= {f"exc{i}.{k}": float(v) for k, v in zip(EXC, m.groups())}
    return T


# ---- DC1 = RL-A2TL-TL-CIV-FND-2312, 4 sheets, read off the drawing ----
_COMMON_BBS = [("D", 25, 5650, 8, 16, 3.85, 348.0), ("D1", 20, 5350, 8, 16, 2.47, 211.4), ("E1", 8, 2590, 17, 34, 0.39, 34.3),
               ("E2", 8, 2030, 34, 68, 0.39, 53.8), ("E3", 8, 1930, 17, 34, 0.39, 25.6)]
_EXC = [((0, -3), 10450, 11377, 16089), ((0, -1.5), 10800, 11727, 16584), ((0, 0), 11150, 12076, 17079),
        ((0, 1.5), 11500, 12426, 17573), ((-3, 3), 11850, 12776, 18068), ((-3, 4.5), 12200, 13126, 18563),
        ((-3, 6), 12549, 13476, 19058), ((-3, 7.5), 12899, 13826, 19552), ((-3, 9), 13249, 14175, 20047)]


def dc1_truth(sheet):
    t = {"drawing_no": ["RL-A2TL-TL-CIV-FND-2312"], "sheet": (sheet, 4), "rev": 0.0}
    if sheet in (1, 3):
        b2, b1 = (4.0, 4.7) if sheet == 1 else (3.2, 3.9)
        t |= dict(D=3.5, H=2.28, G1=0.1, G2=0.35, G3=0.72, dp=50, S=0.7, B2=b2, B1=b1, B3=1.8, H1=1.225, Ext=150, e=59.0)
        t |= dict(zip(("concrete", "lean", "reinf", "excav"), (23.5, 2.2, 2443.6, 136.7) if sheet == 1 else (17.7, 1.5, 1646.5, 91.7)))
        t |= dict(soil_type="DRY FISSURED ROCK", sbc=62.5, upper_density=1700, lower_density=1700, beta=20, alfa=20)
        top = [("A", 16, 4600, 68, 136, 1.58, 988.4), ("B", 16, 4950, 50, 100, 1.58, 782.1)] if sheet == 1 else \
              [("A", 16, 3800, 54, 108, 1.58, 648.4), ("B", 12, 4150, 44, 88, 0.89, 325.0)]
        for i, row in enumerate(top + _COMMON_BBS):
            t |= {f"bbs{i}.{k}": v for k, v in zip(BBS, row)}
    else:
        for i, (ext, plx, mlx, nlx) in enumerate(_EXC):
            t |= {f"exc{i}.ext": ext, f"exc{i}.plx_t": plx, f"exc{i}.plx_l": plx, f"exc{i}.mlx_t": mlx, f"exc{i}.mlx_l": mlx, f"exc{i}.nlx": nlx}
    return t


def score(result, truths):
    """Every answer-key field → correct / wrong (released as ok or resolved, but differs: must never happen) /
    unverified_wrong (released marked "unverified" for the user to check, and differs) / flagged / missing /
    withheld (the drawing has a drawing-level problem, so nothing at all is released)."""
    tally = {"correct": 0, "wrong": 0, "unverified_wrong": 0, "flagged": 0, "missing": 0, "withheld": 0}
    bad = []
    for sheet, want in zip(result["sheets"], truths, strict=True):
        for k, v in want.items():
            if result["problems"]:
                tally["withheld"] += 1
                continue
            if k in ("sheet", "drawing_no"):  # the drawing's agreed number (a sheet may also hold an outvoted misread)
                got = sheet["sheet"] if k == "sheet" else [result["drawing_no"]] if result["drawing_no"] else []
                st = "ok" if got else "missing"
            else:
                cell = sheet["values"].get(k)
                got, st = (cell["value"], cell["status"]) if cell else (None, "missing")
            same = got == v or (isinstance(v, float) and isinstance(got, float) and abs(got - v) < 1e-6)
            r = ("missing" if st == "missing" else "flagged" if st == "flagged" or got is None else "correct" if same
                 else "unverified_wrong" if st == "unverified" else "wrong")
            tally[r] += 1
            if r != "correct":
                bad.append(f"sheet {sheet['page']} {r} {k}: got {got!r} want {v!r} [{st}]")
    return tally, bad
