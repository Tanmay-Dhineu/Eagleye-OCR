"""Extracted values → the values the Eagleye Foundation forms ask for, per pit (D&E-approved ANANDHU sheet).

A derived field inherits the worst
status of its inputs: if any input was flagged, the field is None and flagged too."""
import math

from .drawing_no import canonical

PITS = "ABCD"
# Which pits use the uplift sheet and which the compression sheet, on drawings that have both. From the site's own
# entries on AP54 (drawing 2312): pit size 4.40 on A and B = uplift B1 4.7 − 2·Ext, 3.60 on C and D = compression
# B1 3.9 − 2·Ext. One tower: open question 3 for D&E.
PIT_GROUP = {"A": "uplift", "B": "uplift", "C": "compression", "D": "compression"}
RANK = {"ok": 0, "resolved": 1, "unverified": 2, "flagged": 3}
# soil type as printed on the drawing → the Pit Marking dropdown option *label*, which is what the app stores in
# submitted_data ("DFR" 5,889 submissions, "WFR" 1,169, …; the option values "dfr"/"wfr" never appear).
# ponytail: only phrases seen on drawings or unambiguous; anything else is flagged for the user to pick.
SOIL_OPTIONS = {"DRY FISSURED ROCK": "DFR", "WET FISSURED ROCK": "WFR", "SUBMERGED FISSURED ROCK": "SFR",
                "HARD ROCK": "Hard Rock", "DRY SOIL": "DRY", "WET SOIL": "WET", "DRY BLACK COTTON": "DBC",
                "WET BLACK COTTON": "WBC", "SUBMERGED BLACK COTTON": "SBC", "NORMAL DRY SOIL": "NDS"}
FLAGGED = {"value": None, "status": "flagged"}


def _get(values, k):
    """A value as {value, status}; absent counts as flagged so nothing downstream is computed from it."""
    return values.get(k) or FLAGGED


def _field(values, keys, fn):
    """Apply fn to the named values; status = worst input status."""
    vals = [_get(values, k) for k in keys]
    status = max((v["status"] for v in vals), key=RANK.get, default="ok")
    if any(v["value"] is None for v in vals):
        return FLAGGED
    return {"value": fn(*[v["value"] for v in vals]), "status": status}


def _round(fn, nd=3):
    return lambda *a: round(fn(*a), nd)


def _pit_size(v, undercut):
    """Pit length = width (m). Undercut (rock): Ext is inside the footing, B1 − 2·Ext. Otherwise Ext is outside the
    footing (D&E remark), B1 + 2·Ext: on the Cuddapah line (CAT-A, Ext 150) the site entered B1 − 0.3 on DFR/WFR and
    B1 + 0.3 on every other soil in 97–98% of 861 pits."""
    sign = -1 if undercut else 1
    return _field({"b1": _get(v, "B1"), "ext": _get(v, "Ext")}, ["b1", "ext"], _round(lambda b1, e: b1 + sign * 2 * e / 1000))


# per-pit concrete volumes (m³), one leg, from the DIMENSIONS table; the concrete identity in checks.py verifies them
VOLUMES = {
    "frustum_volume": (["B1", "G1", "B2", "G2"], lambda b1, g1, b2, g2: b1 ** 2 * g1 + g2 / 3 * (b1 ** 2 + b2 ** 2 + b1 * b2)),
    "stepbox_volume": (["S", "B3", "G3"], lambda s, b3, g3: g3 / 3 * (s ** 2 + b3 ** 2 + s * b3)),
    "chimney_volume": (["S", "H"], lambda s, h: s ** 2 * (h + 0.225)),
    "raised_chimney_volume": (["S", "H1"], lambda s, h1: s ** 2 * (h1 - 0.225)),
}
CEMENT_KG_PER_CUM = 4.4 * 50  # PCC M10: 4.4 bags of 50 kg per m³


def _volumes(v):
    return {k: _field(v, keys, _round(fn)) for k, (keys, fn) in VOLUMES.items()}


def drawing_fields(result):
    """{leg_group: {value key: {value, status}}} for one drawing, independent of the tower: what Eagleye sends to the
    site app so it can fill the Foundation forms offline once the pit's soil (hence its drawing) is known.
    Undercut comes from the drawing's own soil (rock → undercut); an unread soil flags the pit size."""
    out = {}
    for s in result["sheets"]:
        if s["kind"] != "detail" or s["leg_group"] in out:
            continue
        v, legs = s["values"], s["legs"] or 4
        soil = _get(v, "soil_type")
        size = _pit_size(v, "ROCK" in str(soil["value"]).upper()) if soil["value"] else FLAGGED
        out[s["leg_group"]] = {
            "pit_depth": _get(v, "D"), "pit_length": size, "pit_width": size,
            "pcc_m10": _field(v, ["lean"], _round(lambda lean: lean / legs)),
            "cement_kg": _field(v, ["lean"], _round(lambda lean: lean / legs * CEMENT_KG_PER_CUM, 1)),
            "stub_clearance_mm": _field(v, ["stub_cover", "dp"], _round(lambda c, dp: c + dp, 0)),  # cover above the PCC
            **_volumes(v),
            "bbs": _bbs_field(v),
        }
    return out


def _detail_sheet(result, pit):
    sheets = [s for s in result["sheets"] if s["kind"] == "detail"]
    mine = [s for s in sheets if s["leg_group"] == PIT_GROUP[pit]] or [s for s in sheets if s["leg_group"] == "all"]
    return mine[0] if mine else None


def _exc_row(result, detail, ext):
    """The excavation row for this pit's (body ext, leg ext); prefer the excavation sheet after its detail sheet."""
    sheets = [s for s in result["sheets"] if s["kind"] == "excavation"]
    sheets.sort(key=lambda s: (s["page"] != detail["page"] + 1, s["page"]))
    for s in sheets:
        i = 0
        while f"exc{i}.ext" in s["values"]:
            row_ext = s["values"][f"exc{i}.ext"]["value"]  # tuple when fresh, list after the JSON cache
            if row_ext is not None and tuple(row_ext) == tuple(ext):
                return {k[len(f"exc{i}."):]: v for k, v in s["values"].items() if k.startswith(f"exc{i}.")}
            i += 1
    return None


def map_fields(results, pits):
    """results: {pit: extraction result of that pit's drawing}; pits of one tower can use different drawings.
    pits: {"A": {"body_ext": -3.0, "leg_ext": 3.0, "undercut": false, "key": "dochub/1564932317589204992"}, ...}

    Returns values by meaning, not by Form.io key: the keys differ per form and per template version (all four
    Stub Setting forms use `…_PitA`, all PCC forms `…_PITD`), so Eagleye binds each meaning to its form field by the
    field's label in the current template. Units are the forms' units: metres, m³, kg; bar diameter in mm."""
    problems = []
    numbers = {r["drawing_no"] for r in results.values()}
    out = {"drawing_no": numbers.pop() if len(numbers) == 1 else None, "pit_marking": {}, "pits": {}}
    rows, soils = {}, {}
    for pit in PITS:
        if pit not in pits:
            continue
        req, result = pits[pit], results[pit]
        d = _detail_sheet(result, pit)
        if not d:
            problems.append(f"pit {pit}: no detail sheet")
            continue
        v = d["values"]
        row = _exc_row(result, d, (float(req["body_ext"]), float(req["leg_ext"])))
        if not row:
            problems.append(f"pit {pit}: no excavation row for extension {float(req['body_ext']):g}/{float(req['leg_ext']):g}")
            row = {}
        # Undercut: Ext is inside the footing (D&E), so the pit is B1 − 2·Ext and each pit side moves in by Ext.
        # Not given → rock foundations count as undercut: on the 41 towers of drawings 2009/9112 (all fissured rock)
        # the site entered B1 − 2·Ext as the pit size 31 times out of 40, and plain B1 never.
        undercut = req.get("undercut")
        undercut_from = "request"
        if undercut is None:
            undercut, undercut_from = "ROCK" in str(_get(v, "soil_type")["value"]).upper(), "soil type"
        rows[pit] = (v, row, bool(undercut))
        size = _pit_size(v, undercut)
        legs = d["legs"] or 4
        out["pits"][pit] = {
            "source": {"key": req.get("key"), "drawing_no": result["drawing_no"], "rev": result["rev"],
                       "sheet": d["sheet"], "leg_group": d["leg_group"], "legs_per_quantity": legs, "extension_row": row.get("ext"),
                       "undercut": bool(undercut), "undercut_from": undercut_from},
            "cement_kg": _field(v, ["lean"], _round(lambda lean: lean / legs * CEMENT_KG_PER_CUM, 1)),  # PCC: lean m³ per pit
            "pit_length": size, "pit_width": size, "pit_depth": _get(v, "D"),  # B1 is the same on both faces
            "bbs": _bbs(v),
            **_volumes(v),
        }
        out["pit_marking"][f"depth_{pit}"] = _get(v, "D")
        soils[pit] = _get(v, "soil_type")
    # one soil field on the Pit Marking form: released only when every pit's drawing prints the same soil
    printed = {" ".join(str(s["value"]).split()) for s in soils.values()}
    if soils:
        soil = max(soils.values(), key=lambda s: RANK[s["status"]])
        option = SOIL_OPTIONS.get(next(iter(printed))) if len(printed) == 1 else None
        out["pit_marking"]["soil_type"] = ({"value": option, "status": soil["status"], "printed": soil["value"]} if option else
                                           {**FLAGGED, "printed": soil["value"] if len(printed) == 1 else sorted(printed)})
    # Back to back (m), outer edge to outer edge of the two pits = MLX_i + MLX_j + half_i + half_j, where a pit's half
    # is half the pit size (B1/2 ∓ Ext, see _pit_size). AB and CD run along MLX,LONG, BC and AD along MLX,TRAN: on drawings
    # 2009/9112 every site entry that follows a formula follows this one (12-13 of 34 towers; the rest are measured).
    half = {pit: _field({"size": _pit_size(v, undercut)}, ["size"], lambda size: size / 2) for pit, (v, _, undercut) in rows.items()}
    for a, b, axis in (("A", "B", "mlx_l"), ("B", "C", "mlx_t"), ("C", "D", "mlx_l"), ("A", "D", "mlx_t")):
        if a in rows and b in rows:
            inputs = {"ma": rows[a][1].get(axis), "mb": rows[b][1].get(axis), "ha": half[a], "hb": half[b]}
            out["pit_marking"][f"b2b_{a}{b}"] = _field(inputs, ["ma", "mb", "ha", "hb"],
                                                      _round(lambda ma, mb, ha, hb: (ma + mb) / 1000 + ha + hb))
    # Diagonal back to back: outer corner to outer corner = √(long side² + transverse side²). The mapping sheet's
    # NLX × 2 is the centre-to-centre diagonal and matched 0 of 34 site entries; this matched every one that follows
    # a formula (13). Open with D&E.
    for a, b in (("A", "C"), ("B", "D")):
        if a in rows and b in rows:
            (_, ra, _), (_, rb, _) = rows[a], rows[b]
            inputs = {"la": ra.get("mlx_l"), "lb": rb.get("mlx_l"), "ta": ra.get("mlx_t"), "tb": rb.get("mlx_t"), "ha": half[a], "hb": half[b]}
            out["pit_marking"][f"diag_{a}{b}"] = _field(inputs, ["la", "lb", "ta", "tb", "ha", "hb"], _round(
                lambda la, lb, ta, tb, ha, hb: math.hypot((la + lb) / 1000 + ha + hb, (ta + tb) / 1000 + ha + hb)))
    return {"fields": out, "problems": problems}


def _bbs(v):
    """Bar schedule rows in the Reinforcement form's units: length in metres (drawing: mm), diameter in mm."""
    rows, i = [], 0
    while f"bbs{i}.mark" in v:
        length = _get(v, f"bbs{i}.length")
        rows.append({"type": _get(v, f"bbs{i}.mark"), "length": _field({"l": length}, ["l"], _round(lambda l: l / 1000)),
                     "bars": _get(v, f"bbs{i}.nos_leg"), "dia": _get(v, f"bbs{i}.dia")})
        i += 1
    return rows


def _bbs_field(v):
    """The whole bar schedule for the Reinforcement "Steel Road details" grid, or flagged: a bar list with one row
    missing or misread is worse than none, so it is released only when every cell was read for sure and the rows add
    up to the drawing's reinforcement total (checks.py)."""
    rows = _bbs(v)
    cells = [c for r in rows for c in r.values()]
    if not rows or _get(v, "reinf")["status"] != "ok" or any(c["value"] is None or c["status"] != "ok" for c in cells):
        return FLAGGED
    return {"value": [{k: c["value"] for k, c in r.items()} for r in rows], "status": "ok"}


def wrong_drawing(result, requested):
    """requested None = picked by key from the library, with no typed number to check against."""
    return requested is not None and result["drawing_no"] is not None and result["drawing_no"] != canonical(requested)
