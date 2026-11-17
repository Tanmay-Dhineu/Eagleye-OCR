"""Per-drawing field values for the site app (CR-01 mobile autofill): one set per leg group, in the forms' units.

FND-2002 (Cuddapah CAT-A, DA 0-2°, -3 to +9 m, dry fissured rock) is the reference: the expected values are the ones
site users entered on line 31 (B1 - 0.3 on rock, cement = M10 x 220, clearance 200)."""
import copy
from functools import lru_cache
from pathlib import Path

from app import extract, mapping, ramboll

SAMPLES = Path(__file__).parent / "samples"


@lru_cache
def _extract(name):
    return extract.extract(SAMPLES / name)


def ex(name):
    return copy.deepcopy(_extract(name))


def test_2002_fields_match_site_validated_values():
    f = mapping.drawing_fields(ex("FND-2002.pdf"))["all"]
    v = {k: x["value"] for k, x in f.items()}
    assert v["pit_depth"] == 3.5
    assert v["pit_length"] == v["pit_width"] == 3.05          # DFR: 3.35 - 2 * 0.150
    assert v["pcc_m10"] == 0.55 and v["cement_kg"] == 121.0   # lean 2.2 / 4 legs; x 220
    assert v["stub_clearance_mm"] == 200.0                    # cover 150 + dp 50
    assert v["frustum_volume"] == 3.448 and v["stepbox_volume"] == 0.926
    assert v["chimney_volume"] == 0.866 and v["raised_chimney_volume"] == 0.0
    assert all(x["status"] == "ok" for x in f.values())


def test_non_rock_pit_is_b1_plus_two_ext():
    e = ex("DB2 ULE 1M RC DRY_R1.pdf")
    fields = mapping.drawing_fields(e)
    group = next(iter(fields))
    d = next(s for s in e["sheets"] if s["kind"] == "detail" and s["leg_group"] == group)["values"]
    b1, ext = d["B1"]["value"], d["Ext"]["value"]
    assert fields[group]["pit_length"]["value"] == round(b1 + 2 * ext / 1000, 3)


def test_missing_input_flags_only_dependent_fields():
    e = ex("FND-2002.pdf")
    for s in e["sheets"]:
        s["values"].pop("Ext", None)
    f = mapping.drawing_fields(e)["all"]
    assert f["pit_length"] == {"value": None, "status": "flagged"} and f["pit_width"]["status"] == "flagged"
    assert f["pit_depth"] == {"value": 3.5, "status": "ok"}


def test_stub_cover_note_is_read():
    assert ramboll.stub_cover("6. STUB BELOW GROUND LEVEL = 3300 mm & CLEAR COVER TO STUB IS 150mm ABOVE THE") == 150.0
    assert ramboll.stub_cover("CLEAR COVER TO THE MAIN REINFORCEMENT BARS SHALL BE 50mm.") is None


def test_drawing_without_stub_note_flags_clearance_only():
    e = ex("FND-2002.pdf")
    for s in e["sheets"]:
        s["values"].pop("stub_cover", None)
    f = mapping.drawing_fields(e)["all"]
    assert f["stub_clearance_mm"]["status"] == "flagged" and f["cement_kg"]["status"] == "ok"
