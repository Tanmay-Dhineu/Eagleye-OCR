"""Title → library properties, on real DocHub (Cuddapah CAT-A) titles. Expected values agree with the DocHub folder
each file sits in (1MRC, -3M TO +3, …) and with the worked examples in CR01_Drawing_Selection_Method.pdf."""
import pytest

from app import title

HEAD = "TITLE: FOUNDATION DRAWING & PIT MARKING FOR 765KV D/C TRANSMISSION LINE WZ-2. FOR "
CASES = [  # tail of the title, (tower, angles, ext from-to, raised chimney, leg type, soil code, variant)
    ("DA(0° - 2°) SUSPENSION TOWER DA-3M TO +9M EXTENSION FOR DRY FISSURED ROCK 1M RAISED CHIMNEY",  # 2112
     ("DA", 0, 2, -3, 9, 1.0, "Normal", "DFR", None)),
    ("DA(0° - 2°) SUSPENSION TOWER DA-3M TO +9M EXTENSION FOR WET SOIL 1.5M RAISED CHIMNEY",  # 9114
     ("DA", 0, 2, -3, 9, 1.5, "Normal", "WET", None)),
    ("DA(0° - 2°) SUSPENSION TOWER DA-3M TO +9M EXTENSION FOR SANDY FS SOIL 1M RAISED CHIMNEY",  # 9012
     ("DA", 0, 2, -3, 9, 1.0, "Normal", "SANDY FS", None)),
    ("DA(0° - 2°) SUSPENSION TOWER DA-3M TO +9M EXTENSION FOR FULLY SUBMERGED SOIL 1M RAISED CHIMNEY",  # 2116
     ("DA", 0, 2, -3, 9, 1.0, "Normal", "FS", None)),
    ("DA(0° - 2°) SUSPENSION TOWER DA+6M & +9M EXTENSION FOR DRY FISSURED ROCK",  # 2102: no RC = 0
     ("DA", 0, 2, 6, 9, 0.0, "Normal", "DFR", None)),
    ("DA(0° - 2°) SUSPENSION TOWER DA+6M & +9M EXTENSION FOR PARTIALLY SUBMERGED SOIL",  # 2105
     ("DA", 0, 2, 6, 9, 0.0, "Normal", "PS", None)),
    ("DA(0° - 2°) SUSPENSION TOWER DA+9M EXTENSION FOR DRY FISSURED ROCK",  # 9015: one extension only
     ("DA", 0, 2, 9, 9, 0.0, "Normal", "DFR", None)),
    ("DA(0° - 2°) SUSPENSION TOWER DA+0M EXTENSION FOR DRY FISSURED ROCK",  # 9016
     ("DA", 0, 2, 0, 0, 0.0, "Normal", "DFR", None)),
    ("DA(0° - 2°) SUSPENSION TOWER DA-3M & +3M EXTENSION FOR SANDY DRY SOIL",  # 2003, filed under "-3M TO +3"
     ("DA", 0, 2, -3, 3, 0.0, "Normal", "SANDY DRY", None)),
    ("DA(0° - 2°) SUSPENSION TOWER DA-3M TO +3M EXTENSION FOR SANDY WET SOIL",  # 6001
     ("DA", 0, 2, -3, 3, 0.0, "Normal", "SANDY WET", None)),
    ("DA(0° - 2°) SUSPENSION TOWER DA-3M TO +9M EXTENSION FOR DFR (SELF DRILL ANCHOR_PRECAST BLOCK)",  # 9002
     ("DA", 0, 2, -3, 9, 0.0, "Normal", "DFR", "block")),
    ("DA(0° - 2°) SUSPENSION TOWER DA-3M TO +9M EXTENSION FOR DFR (ANCHOR TYPE)",  # 9100
     ("DA", 0, 2, -3, 9, 0.0, "Normal", "DFR", "anchor")),
    ("DB1(0° - 7°) (UNEQUAL LEG EXTENSION)(TENSION TOWER) FOR DRY FISSURED ROCK - 1.5M RAISED CHIMNEY",  # 9212
     ("DB1", 0, 7, None, None, 1.5, "Unequal", "DFR", None)),
    ("DA(0° - 2°) SUSPENSION TOWER DA-3M TO +3M EXTENSION FOR WET FISSURED ROCK -",  # 2009 (the old sample)
     ("DA", 0, 2, -3, 3, 0.0, "Normal", "WFR", None)),
]
KEYS = ("tower_type", "angle_from", "angle_to", "ext_from", "ext_to", "raised_chimney", "leg_type", "soil_code", "variant")


@pytest.mark.parametrize("tail,expected", CASES)
def test_real_titles(tail, expected):
    t = title.parse(HEAD + tail)
    assert tuple(t[k] for k in KEYS) == expected and t["status"] == "ok"


def test_ocr_noise_in_the_degree_signs_still_reads():
    t = title.parse(HEAD + "DA(0 - 2) SUSPENSION TOWER DA-3M TO +9M EXTENSION FOR DRY FISSURED ROCK 1M RAISED CHIMNEY")
    assert (t["tower_type"], t["angle_from"], t["angle_to"], t["status"]) == ("DA", 0, 2, "ok")


def test_unknown_wording_is_partial_not_guessed():
    t = title.parse(HEAD + "DA(0° - 2°) SUSPENSION TOWER DA-3M TO +9M EXTENSION FOR CREEK BED")
    assert t["soil"] == "CREEK BED" and t["soil_code"] is None and t["status"] == "partial"
    assert title.parse("TITLE: FOUNDATION DRAWING & PIT MARKING")["status"] == "partial"


def test_sheets_must_agree():
    uplift = HEAD + "DB2(7° - 15°) (UNEQUAL LEG EXTENSION)(TENSION TOWER)-UPLIFT LEGS FOR DRY SOIL - 1M RAISED CHIMNEY"
    comp = uplift.replace("UPLIFT", "COMPRESSION")
    assert title.read([uplift, comp, uplift])["status"] == "ok"  # leg group differs, the properties do not
    other = HEAD + "DB2(7° - 15°) (UNEQUAL LEG EXTENSION)(TENSION TOWER) FOR WET SOIL - 1M RAISED CHIMNEY"
    assert title.read([uplift, other])["status"] == "conflict"
    assert title.read([None, None])["status"] == "missing"
