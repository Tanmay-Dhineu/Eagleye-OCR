"""The drawing's own arithmetic decides what is released.

Statuses:
  ok         text layer (exact), or OCR readings agreed and an arithmetic check confirms the value
  resolved   OCR readings disagreed and exactly one combination passes the arithmetic
  unverified OCR value no arithmetic can confirm (soil, Ext, PLX, …): readings agreed and it is plausible,
             but two readings of the same noisy image are not independent evidence. The user must check it.
  flagged    not released; the user types it"""
import itertools
import math
import re


def pick(fields, check=lambda v: True):
    """Accept a combination of candidate readings only if the check holds and it is the only one that does."""
    names = list(fields)
    good = {combo for combo in itertools.product(*[(fields[n] or {}).get("cands", []) for n in names])
            if check(dict(zip(names, combo)))}
    if len(good) != 1:
        return {n: None for n in names}, "flagged"
    return dict(zip(names, good.pop())), "ok" if all(fields[n]["agree"] for n in names) else "resolved"


# unchecked numbers: (min, max, max decimals) as printed on these drawings
PLAUSIBLE = {"sbc": (1, 500, 1), "upper_density": (500, 3000, 0), "lower_density": (500, 3000, 0),
             "beta": (0, 60, 1), "alfa": (0, 60, 1), "excav": (0.1, 5000, 1), "Ext": (0, 1000, 0), "e": (0, 1000, 1),
             "rev": (0, 30, 0), "plx_t": (1000, 50000, 0), "plx_l": (1000, 50000, 0)}


def plausible(name):
    lo, hi, dec = PLAUSIBLE[name]
    return lambda v: lo <= v[name] <= hi and abs(round(v[name], dec) - v[name]) < 1e-9


def concrete_per_leg(d):
    """Slab + frustum + step box + chimney + raised chimney (m³) for one leg."""
    return (d["B1"] ** 2 * d["G1"]
            + d["G2"] / 3 * (d["B2"] ** 2 + d["B1"] ** 2 + d["B2"] * d["B1"])
            + d["G3"] / 3 * (d["S"] ** 2 + d["B3"] ** 2 + d["S"] * d["B3"])
            + d["S"] ** 2 * (d["H"] + 0.225)
            + d["S"] ** 2 * (d["H1"] - 0.225))


def resolve(p, ocr=False):
    """parse() output → (values, status) with flat keys: D, concrete, bbs0.length, exc3.nlx, …"""
    out, status, legs = {}, {}, p.get("legs", 4)

    def put(vals, st):
        for k, v in vals.items():
            out[k], status[k] = v, st

    def put_unchecked(vals, st):  # nothing but agreement and plausibility behind it
        put(vals, "unverified" if ocr and st != "flagged" else st)

    if p.get("dims"):
        def check(v):
            return (abs(v["H"] + v["G3"] + v["G2"] + v["G1"] + v["dp"] / 1000 - v["D"]) < 0.006
                    and abs(concrete_per_leg(v) * legs - v["concrete"]) < 0.06
                    and abs(v["B1"] ** 2 * v["dp"] / 1000 * legs - v["lean"]) < 0.06)

        tried = []
        for dims in p["dims"]:  # one option per possible label order; usually just the one that was read
            joint = {k: dims.get(k) for k in ("D", "H", "G1", "G2", "G3", "dp", "S", "B2", "B1", "B3", "H1")}
            joint |= {"concrete": p.get("concrete"), "lean": p.get("lean")}
            tried.append((dims, *pick(joint, check)))
        passed = [t for t in tried if t[2] != "flagged"]
        if len({tuple(sorted(t[1].items())) for t in passed}) == 1:
            dims, vals, st = passed[0]
        else:  # none, or two label orders giving different values
            dims, vals, st = tried[0][0], {k: None for k in tried[0][1]}, "flagged"
        put(vals, st)
        for k in ("Ext", "e"):
            put_unchecked(*pick({k: dims.get(k)}, plausible(k)))
    for i, row in enumerate(p.get("bbs", [])):
        fields = {k: row[k] for k in ("dia", "length", "nos_leg", "nos_legs", "unit", "total")}
        vals, st = pick(fields, lambda v: v["nos_legs"] == v["nos_leg"] * legs
                        and abs(v["length"] / 1000 * v["nos_legs"] * v["unit"] - v["total"]) < 0.15)
        mark_ok = re.fullmatch(r"[A-Z]\d?", row["mark"])
        put({f"bbs{i}.{k}": v for k, v in vals.items()}, st)
        put_unchecked({f"bbs{i}.mark": row["mark"] if mark_ok else None}, "ok" if mark_ok else "flagged")
    if p.get("bbs") and p.get("reinf"):
        totals = [out.get(f"bbs{i}.total") for i in range(len(p["bbs"]))]
        put(*pick({"reinf": p["reinf"]}, lambda v: None not in totals and abs(sum(totals) - v["reinf"]) < 0.3))
    rows = p.get("exc", [])
    for i, row in enumerate(rows):
        vals, st = pick({k: row[k] for k in ("mlx_t", "mlx_l", "nlx")}, lambda v: abs(math.hypot(v["mlx_t"], v["mlx_l"]) - v["nlx"]) < 1.5)
        put({f"exc{i}.{k}": v for k, v in vals.items()}, st)
        for k in ("plx_t", "plx_l"):
            v, st = pick({k: row[k]}, plausible(k))
            put_unchecked({f"exc{i}.{k}": v[k]}, st)
    # extension labels: rows are printed in increasing total extension, and MLX grows with it
    totals = [sum(r["ext"]) if r["ext"] else None for r in rows]
    mlx = [out.get(f"exc{i}.mlx_t") for i in range(len(rows))]
    ordered = None not in totals and None not in mlx and all(a < b for a, b in zip(totals, totals[1:]))         and all(a < b for a, b in zip(mlx, mlx[1:]))
    for i, row in enumerate(rows):
        put({f"exc{i}.ext": row["ext"]}, "flagged" if not row["ext"] else "ok" if ordered or not ocr else "unverified")
    # revision: text layer → the title block is exact. OCR → the title value must also be listed in the sheet's
    # revision table (OCR can turn a 0 into a 1). Unreadable title → the table's latest entry stands in.
    rev, st = pick({"rev": p["rev"]}, plausible("rev")) if p.get("rev") else ({"rev": None}, "flagged")
    table = p.get("rev_table") or []
    if rev["rev"] is None and table:
        rev, st = {"rev": max(table)}, "ok"
    elif table and (max(table) > rev["rev"] or (ocr and rev["rev"] not in table)):
        rev, st = {"rev": None}, "flagged"
    put_unchecked(rev, st)
    for k in ("excav", "sbc", "upper_density", "lower_density", "beta", "alfa"):  # no arithmetic: agree + plausible
        if k in p:
            put_unchecked(*pick({k: p[k]}, plausible(k)))
    if "soil_type" in p:
        put_unchecked({"soil_type": p["soil_type"]}, "ok")
    return out, status
