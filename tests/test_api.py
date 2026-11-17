import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import settings, source, store
from app.main import app

SAMPLES = Path(__file__).parent / "samples"
H = {"X-Service-Key": "k"}
DA, DB2 = "DA -3m to +3m - WFR.pdf", "DB2 ULE 1M RC DRY_R1.pdf"
N2009, N2511 = "RL-A2TL-TL-CIV-FND-2009", "RL-A2TL-TL-CIV-FND-2511"


@pytest.fixture
def drawings(tmp_path, monkeypatch):
    """A drawings folder with two text PDFs and an identical copy of one (as in Eagleye\\OCR)."""
    d = tmp_path / "drawings"
    d.mkdir()
    for n in (DA, DB2):
        shutil.copy(SAMPLES / n, d / n)
    shutil.copy(SAMPLES / DA, d / "DA -3m to +3m - WFR (1) 1.pdf")
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(settings, "DRAWINGS_DIR", str(d))
    monkeypatch.setattr(settings, "SERVICE_KEY", "k")
    return d


@pytest.fixture
def client(drawings):
    source.reindex()
    return TestClient(app)


def find(client, typed):
    return client.get("/api/v1/drawings", params={"drawing_no": typed}, headers=H).json()


def job(client, body):
    return client.post("/api/v1/ocr/jobs", json=body, headers=H).json()


def test_auth_and_health(client):
    assert client.get("/health").json()["ok"] is True
    assert client.get("/api/v1/drawings", params={"drawing_no": "x"}).status_code == 401
    assert client.get("/api/v1/drawings", params={"drawing_no": "x"}, headers={"X-Service-Key": "wrong"}).status_code == 401
    assert client.post("/api/v1/drawings/reindex").status_code == 401
    assert client.get("/api/v1/ocr/jobs/abc").status_code == 401


def test_index_is_incremental_and_identical_copies_are_not_ambiguous(drawings, client):
    counts = source.reindex()
    assert (counts["seen"], counts["unchanged"], counts["new"], counts["unreadable"]) == (3, 3, 0, 0)
    m = find(client, " rl-a2tl-tl-civ-fnd_2009 REV 0")
    assert m["status"] == "matched" and m["candidates"][0]["key"] in (DA, "DA -3m to +3m - WFR (1) 1.pdf")
    assert find(client, N2511)["candidates"][0]["rev"] == 1.0


def test_same_number_and_rev_with_different_content_is_ambiguous(drawings, client):
    (drawings / "2009 reissued.pdf").write_bytes((drawings / DA).read_bytes() + b"\n%changed\n")  # same drawing, other bytes
    assert source.reindex()["new"] == 1
    m = find(client, N2009)
    assert m["status"] == "ambiguous" and len(m["candidates"]) == 3
    assert job(client, {"drawing_no": N2009, "pits": {}})["status"] == "ambiguous"  # never picks one by itself


def test_corrupt_pdf_is_recorded_not_fatal(drawings, client):
    (drawings / "broken.pdf").write_bytes(b"%PDF-1.4 this is not a pdf")
    counts = source.reindex()
    assert counts["unreadable"] == 1 and counts["new"] == 1
    assert find(client, N2009)["status"] == "matched"


def test_match_tiers_and_job_statuses(client):
    assert find(client, "RL-A2TL-TL-CIV-FDN-2009")["status"] == "probable"
    assert find(client, "RL-A2TL-TL-CIV-FND-0000")["status"] == "missing"
    m = find(client, f"{N2009} & {N2511}")
    assert m["status"] == "multiple" and [p["status"] for p in m["parts"]] == ["matched", "matched"]
    assert job(client, {"drawing_no": "RL-A2TL-TL-CIV-FDN-2009", "pits": {}})["status"] == "probable"  # needs the user's confirmation
    assert client.get("/api/v1/ocr/jobs/nope", headers=H).status_code == 404
    assert client.post("/api/v1/ocr/jobs", json={"drawing_no": N2009, "key": "nope.pdf"}, headers=H).status_code == 404


def test_cached_job_maps_fields(client):
    pits = {p: {"body_ext": 0, "leg_ext": -3} for p in "ABCD"}
    r = job(client, {"drawing_no": N2009, "pits": pits})
    assert r["status"] == "done" and r["problems"] == [] and r["key"] in (DA, "DA -3m to +3m - WFR (1) 1.pdf")
    a = r["fields"]["pits"]["A"]
    assert a["source"]["undercut"] is True and a["source"]["undercut_from"] == "soil type"  # wet fissured rock
    assert a["pit_length"] == {"value": 4.13, "status": "ok"}  # B1 4.43 − 2 × Ext 0.15 at an undercut pit
    assert a["cement_kg"]["value"] == round(3.9 / 4 * 4.4 * 50, 1)  # lean 3.9 m³ per tower
    assert a["chimney_volume"]["value"] == round(0.57 ** 2 * (2.44 + 0.225), 3)
    row = a["bbs"][0]
    assert {k: c["value"] for k, c in row.items()} == {"type": "A", "dia": 10.0, "length": 4.33, "bars": 84.0}  # metres, as the form asks
    pm = r["fields"]["pit_marking"]
    assert pm["depth_A"]["value"] == 3.5 and pm["soil_type"] == {"value": "WFR", "status": "ok", "printed": "WET FISSURED ROCK"}
    ab, bc = 2 * 7.994 + 4.43 - 0.3, 2 * 9.403 + 4.43 - 0.3  # -3 m row: MLX,LONG 7994 and MLX,TRAN 9403, Ext 0.15 per pit
    assert pm["b2b_AB"]["value"] == round(ab, 3) and pm["b2b_CD"]["value"] == round(ab, 3)
    assert pm["b2b_BC"]["value"] == round(bc, 3) and pm["b2b_AD"]["value"] == round(bc, 3)
    assert pm["diag_AC"]["value"] == round((ab ** 2 + bc ** 2) ** 0.5, 3)  # outer corner to outer corner
    pits = {p: {"body_ext": 0, "leg_ext": -3, "undercut": p == "A"} for p in "ABCD"}  # the user's choice wins
    pm = job(client, {"drawing_no": N2009, "pits": pits})["fields"]["pit_marking"]
    assert pm["b2b_AB"]["value"] == round(2 * 7.994 + 4.43 - 0.15, 3)  # only pit A's side moves in by Ext
    assert pm["b2b_CD"]["value"] == round(2 * 7.994 + 4.43, 3)


def test_unequal_legs_use_each_pits_own_sheet_and_row(client):
    """DB2: sheet 1 = uplift legs (B1 4.3), sheet 3 = compression legs (B1 3.75), quantities per two legs."""
    pits = {"A": {"body_ext": -3, "leg_ext": 3}, "B": {"body_ext": -3, "leg_ext": 3}, "C": {"body_ext": 0, "leg_ext": 1.5}, "D": {"body_ext": 0, "leg_ext": 1.5}}
    r = job(client, {"drawing_no": N2511, "pits": pits})
    assert r["status"] == "done" and r["problems"] == []
    p = r["fields"]["pits"]
    assert p["A"]["source"]["leg_group"] == "uplift" and p["C"]["source"]["leg_group"] == "compression"  # as the site does on AP54
    assert p["A"]["pit_length"]["value"] == 4.3 and p["C"]["pit_length"]["value"] == 3.75
    assert p["A"]["cement_kg"]["value"] == round(1.8 / 2 * 4.4 * 50, 1)  # uplift lean 1.8 m³ ÷2: per two legs
    assert p["C"]["cement_kg"]["value"] == round(1.4 / 2 * 4.4 * 50, 1)  # compression lean 1.4 m³
    pm = r["fields"]["pit_marking"]
    assert p["C"]["source"]["undercut"] is False  # dry soil: not undercut unless the user says so
    assert pm["b2b_CD"]["value"] == round(2 * 11.802 + 3.75, 3)  # C, D: compression row 0/+1.5, MLX 11802 (TRAN = LONG here)
    bc = pm["b2b_BC"]["value"]  # B uplift + C compression: half of each pit's own B1
    mlx_b = (bc - 11.802 - (4.3 + 3.75) / 2)
    assert abs(pm["b2b_AB"]["value"] - (2 * mlx_b + 4.3)) < 0.002


def test_missing_extension_row_is_reported(client):
    r = job(client, {"drawing_no": N2009, "pits": {"A": {"body_ext": 0, "leg_ext": 7.5}}})
    assert "pit A: no excavation row for extension 0/7.5" in r["problems"]


def test_queued_job_runs_and_a_vanished_file_fails_cleanly(drawings, client):
    with store._db() as db:
        db.execute("DELETE FROM results")  # force the slow path
    r = job(client, {"drawing_no": N2009, "pits": {"A": {"body_ext": 0, "leg_ext": 0}}})
    assert r["status"] == "queued"
    done = client.get(f"/api/v1/ocr/jobs/{r['job_id']}", headers=H).json()  # TestClient ran the background task
    assert done["status"] == "done" and done["result"]["fields"]["pits"]["A"]["source"]["extension_row"]["value"] == [0.0, 0.0]
    with store._db() as db:
        db.execute("DELETE FROM results")
    (drawings / DA).unlink()
    (drawings / "DA -3m to +3m - WFR (1) 1.pdf").unlink()
    r = job(client, {"drawing_no": N2009, "pits": {}})
    failed = client.get(f"/api/v1/ocr/jobs/{r['job_id']}", headers=H).json()
    assert failed["status"] == "failed" and failed["error"]


def test_a_key_for_another_drawing_releases_nothing(client):
    r = job(client, {"drawing_no": N2511, "key": DA, "pits": {"A": {"body_ext": 0, "leg_ext": -3}}})
    assert r["fields"] is None and "drawing_no_mismatch" in r["problems"]
    r = job(client, {"drawing_no": "RL-A2TL-TL-CIV-FDN-2009", "key": DA, "pits": {}})  # user confirmed the probable match
    assert r["status"] == "done" and r["problems"] == []


def test_a_drawing_level_problem_releases_nothing(drawings):
    from app.main import _finish
    for problem in ("drawing_no_conflict", "drawing_no_incomplete", "sheets_incomplete", "rev_inconsistent"):
        result = {"drawing_no": N2009, "rev": 0.0, "problems": [problem], "sheets": []}
        r = _finish({DA: result}, {"drawings": {DA: {"drawing_no": N2009}}, "pits": {}})
        assert r["fields"] is None and r["problems"] == [problem]


def test_manual_upload_is_a_fallback_for_that_request_only(drawings, client, monkeypatch):
    pdf = (SAMPLES / "DB1 ULE 1M RC DFR.pdf").read_bytes()
    up = client.post("/api/v1/drawings/upload", content=pdf, headers={**H, "Content-Type": "application/pdf"}).json()
    assert up["key"].startswith("upload/") and up["bytes"] == len(pdf)
    assert find(client, "RL-A2TL-TL-CIV-FND-2212")["status"] == "missing"  # uploads never enter the shared index
    pits = {"A": {"body_ext": 0, "leg_ext": 0}}
    r = job(client, {"drawing_no": "RL-A2TL-TL-CIV-FND-2212", "key": up["key"], "pits": pits})
    assert r["status"] == "queued"
    done = client.get(f"/api/v1/ocr/jobs/{r['job_id']}", headers=H).json()
    assert done["status"] == "done" and done["result"]["problems"] == [] and done["result"]["fields"]["pits"]["A"]
    r = job(client, {"drawing_no": N2009, "key": up["key"], "pits": pits})  # uploaded the wrong drawing for this tower
    assert r["status"] == "done" and r["fields"] is None and "drawing_no_mismatch" in r["problems"]
    assert client.post("/api/v1/drawings/upload", content=b"hello", headers=H).status_code == 415
    monkeypatch.setattr(settings, "MAX_UPLOAD_MB", 0)
    assert client.post("/api/v1/drawings/upload", content=pdf, headers=H).status_code == 413
    assert client.post("/api/v1/drawings/upload", content=pdf).status_code == 401


def test_issued_for_is_read_and_the_approved_rule_is_a_setting(drawings, client, monkeypatch):
    m = find(client, N2009)
    assert m["candidates"][0]["issued_for"] == "ISSUED FOR APPROVAL" and m["candidates"][0]["approved"] is True
    monkeypatch.setattr(settings, "APPROVED_ISSUES", ["RELEASED FOR CONSTRUCTION"])
    m = find(client, N2009)
    assert m["status"] == "not_approved"  # only issued for approval: flagged for the user, never picked
    assert job(client, {"drawing_no": N2009, "pits": {}})["status"] == "not_approved"


def test_library_says_which_file_is_selected_and_what_it_is_for(drawings, client):
    rows = {r["key"]: r for r in client.get("/api/v1/library", headers=H).json()["drawings"]}
    copies = [rows[DA], rows["DA -3m to +3m - WFR (1) 1.pdf"]]
    assert sorted(r["selection"] for r in copies) == ["same_file", "selected"] and copies[0]["selected_key"] == copies[1]["selected_key"]
    assert (copies[0]["tower_type"], copies[0]["ext_from"], copies[0]["ext_to"], copies[0]["soil_code"]) == ("DA", -3.0, 3.0, "WFR")
    db2 = rows[DB2]
    assert (db2["selection"], db2["tower_type"], db2["leg_type"], db2["raised_chimney"], db2["soil_code"]) == ("selected", "DB2", "Unequal", 1.0, "DRY")
    (drawings / "2009 reissued.pdf").write_bytes((drawings / DA).read_bytes() + b"\n%changed\n")
    source.reindex()
    rows = client.get("/api/v1/library", headers=H).json()["drawings"]
    assert {r["selection"] for r in rows if r["drawing_no"] == N2009} == {"conflict"}  # nothing selected silently
    assert all(r["selected_key"] is None for r in rows if r["drawing_no"] == N2009)
    assert client.get("/api/v1/library").status_code == 401


def test_each_pit_can_have_its_own_drawing_and_says_which_file(client):
    lib = client.get("/api/v1/library", headers=H).json()["drawings"]
    key_2009 = next(r["key"] for r in lib if r["drawing_no"] == N2009 and r["selection"] == "selected")
    pits = {"A": {"body_ext": 0, "leg_ext": -3, "key": key_2009}, "B": {"body_ext": 0, "leg_ext": -3, "key": key_2009},
            "C": {"body_ext": 0, "leg_ext": 1.5, "drawing_no": N2511}, "D": {"body_ext": 0, "leg_ext": 1.5, "drawing_no": N2511}}
    r = job(client, {"pits": pits})
    assert r["status"] == "done" and r["problems"] == [] and "extraction" not in r and len(r["extractions"]) == 2
    assert [(f["key"], f["drawing_no"], f["pits"]) for f in r["files"]] == [(key_2009, N2009, ["A", "B"]), (DB2, N2511, ["C", "D"])]
    p, pm = r["fields"]["pits"], r["fields"]["pit_marking"]
    assert (p["A"]["source"]["key"], p["A"]["source"]["drawing_no"], p["C"]["source"]["drawing_no"]) == (key_2009, N2009, N2511)
    assert p["A"]["pit_length"]["value"] == 4.13 and p["C"]["pit_length"]["value"] == 3.75  # each from its own drawing
    assert r["fields"]["drawing_no"] is None and pm["b2b_BC"]["status"] == "ok"
    assert pm["soil_type"]["status"] == "flagged" and pm["soil_type"]["printed"] == ["DRY SOIL", "WET FISSURED ROCK"]  # pits disagree
    # picked by key with no typed number: nothing to check it against, and it is released
    one = job(client, {"key": key_2009, "pits": {"A": {"body_ext": 0, "leg_ext": -3}}})
    assert one["status"] == "done" and one["problems"] == [] and one["extraction"]["drawing_no"] == N2009
    bad = job(client, {"pits": {"A": {"body_ext": 0, "leg_ext": -3, "key": key_2009}, "C": {"body_ext": 0, "leg_ext": 0, "drawing_no": "RL-A2TL-TL-CIV-FND-0000"}}})
    assert (bad["status"], bad["pits"], bad["drawing_no"]) == ("missing", ["C"], "RL-A2TL-TL-CIV-FND-0000")


def test_removed_and_non_pdf_files(drawings, client):
    (drawings / ".DS_Store").write_bytes(b"\0\0\0\1Bud1")
    (drawings / DB2).unlink()
    counts = source.reindex()
    assert (counts["skipped"], counts["removed"], counts["seen"]) == (1, 1, 2)
    assert find(client, N2511)["status"] == "missing"  # a removed drawing is never selected
    row = next(r for r in client.get("/api/v1/library", headers=H).json()["drawings"] if r["key"] == DB2)
    assert row["status"] == "removed" and row["drawing_no"] == N2511  # kept: old results still say what was used
    shutil.copy(SAMPLES / DB2, drawings / DB2)
    assert source.reindex()["changed"] == 1 and find(client, N2511)["status"] == "matched"


def test_bad_job_requests_are_rejected(client):
    assert client.post("/api/v1/ocr/jobs", json={"drawing_no": N2009, "pits": {"E": {"body_ext": 0, "leg_ext": 0}}}, headers=H).status_code == 422
    assert client.post("/api/v1/ocr/jobs", json={"drawing_no": N2009, "pits": {"A": {"body_ext": 0}}}, headers=H).status_code == 422
    assert client.post("/api/v1/ocr/jobs", json={"drawing_no": N2009, "pits": {"A": {"body_ext": "x", "leg_ext": 0}}}, headers=H).status_code == 422


def test_sync_log_and_health(client):
    runs = client.get("/api/v1/drawings/reindex", headers=H).json()
    assert runs["running"] is False and runs["runs"][0]["counts"]["seen"] == 3 and runs["runs"][0]["error"] is None
    h = client.get("/health").json()
    assert h["source"] == "local" and h["last_sync"]["ok"] is True
    assert client.get("/docs").status_code == 404 and client.get("/openapi.json").status_code == 404  # no key needed there: off
