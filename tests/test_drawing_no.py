"""Typed drawing numbers, as site engineers really entered them (Pre-Requisite, 30 Sep backup)."""
import pytest

from app.drawing_no import canonical, consensus, match

N = "RL-A2TL-TL-CIV-FND-2002"
INDEX = {d: {"key": f"{d}.pdf", "drawing_no": d, "rev": 0.0, "sha256": d, "others": []}
         for d in (N, "RL-A2TL-TL-CIV-FND-2112", "RL-A2TL-TL-CIV-FND-2102", "RL-A2TL-TL-CIV-FND-2122", "RL-A2TL-TL-CIV-FND-2202")}


@pytest.mark.parametrize("typed", [
    "RL-A2TL-TL-CIV-FND-2002", "RL- A2TL - TL- CIV- FND - 2002", "RL-A2TL-TL- CIV- FND -2002", "RL_A2TL_TL_CIV_FND_2002",
    "rl-a2tl-tl-civ-fnd-2002", "RL-A2TL-TL-CIV-FND-2002 Rev -1", "RL-A2TL-TL-CIV-FND-2002 Rev1", "RL-A2TL--TL-CIV-FND-2002",
    "RL A2TL TL CIV FND 2002", " RL-A2TL-TL-CIV-FND-2002 ", "RL-A2TL-TL-CIV-FND-2002_DA 6m-WFR-Normal RC", "RL-RL-A2TL-TL-CIV-FND-2002",
])
def test_spellings_of_the_same_number_match_exactly(typed):
    assert canonical(typed) == N
    m = match(typed, INDEX)
    assert m["status"] == "matched" and m["drawing_no"] == N


@pytest.mark.parametrize("typed", ["RL-A2TL-CIV-FND-2002", "RL-A2TL-TL-CIV-FDN-2002", "RL-A2TL-TL-FND-2002", "RL -a2tl-civ-fdn-2002"])
def test_dropped_or_swapped_parts_are_only_probable(typed):
    m = match(typed, INDEX)
    assert m["status"] == "probable" and m["drawing_no"] is None and m["candidates"][0]["drawing_no"] == N


@pytest.mark.parametrize("typed", ["RL-A2TL-TL-CIV-FND-0002", "TL-A2TL-CIV-FND-2002", "RL-A2TL-TL-CIV-FND-20021", "", "NA"])
def test_unknown_or_mangled_numbers_are_missing(typed):
    assert match(typed, INDEX)["status"] == "missing"


def test_a_field_naming_several_drawings_is_never_collapsed_to_one():
    m = match("RL-A2TL-TL-CIV-FND-2102 & RL-A2TL-TL-CIV-FND-2122", INDEX)
    assert m["status"] == "multiple" and [p["drawing_no"] for p in m["parts"]] == ["RL-A2TL-TL-CIV-FND-2102", "RL-A2TL-TL-CIV-FND-2122"]
    m = match("RL-A2TL-TL-CIV-FND-2002, RL-A2TL-CIV-FND-2112", INDEX)
    assert [p["status"] for p in m["parts"]] == ["matched", "probable"]
    m = match("RL-A2TL-TL-CIV-FND-2202, RL-A2TL-TL-CIV-FND-2232, RL-A2TL-TL-CIV-FND-2262", INDEX)
    assert [p["status"] for p in m["parts"]] == ["matched", "missing", "missing"]


def test_two_different_files_with_the_same_number_and_rev_are_ambiguous():
    idx = {N: {**INDEX[N], "ambiguous": True, "others": [{"key": "copy.pdf", "rev": 0.0, "sha256": "other"}]}}
    m = match(N, idx)
    assert m["status"] == "ambiguous" and [c["key"] for c in m["candidates"]] == [f"{N}.pdf", "copy.pdf"]


def test_consensus_is_the_one_number_every_sheet_confirms():
    O = "RL-A2TL-TL-CIV-FND-2003"
    assert consensus([[N], [N], [N]]) == (N, "ok")
    assert consensus([[N, "RL-AZTL-TL-CIV-FND-2002"], [N], [N]]) == (N, "ok")  # one sheet's misread is outvoted
    assert consensus([[N], [O]]) == (None, "drawing_no_conflict")
    assert consensus([[N, O], [N, O]]) == (None, "drawing_no_conflict")      # two candidates everywhere: no guess
    assert consensus([[N], []]) == (None, "drawing_no_incomplete")
    assert consensus([[], []]) == (None, "drawing_no_missing")
