"""DocHub client and DocHub-backed index against a local fake that behaves like DocHub UAT did on 2026-11-17:
300 s tokens, 401 on a bad token, R1004 "Node not found" (HTTP 500) for a folder its parent lists, a folder id
"downloaded" as success with the text "Error downloading content", and gateway errors."""
import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import dochub, settings, source, store
from app.main import app

SAMPLES = Path(__file__).parent / "samples"
ROOT = "Sterlite Power>Docs>Anantapur"
H = {"X-Service-Key": "k"}
N2009, N2511 = "RL-A2TL-TL-CIV-FND-2009", "RL-A2TL-TL-CIV-FND-2511"


def folder(node_id, name):
    return {"nodeTypeQname": "deep:folder", "nodeId": node_id, "name": name, "version": "0.0", "createdOn": "28/10/2026 02:48:23 PM",
            "deleted": False, "archived": False, "purged": False}


def file(node_id, name, created="28/10/2026 02:52:39 PM"):
    return {**folder(node_id, name), "nodeTypeQname": "deep:file", "createdOn": created}


class Fake:
    def __init__(self):
        self.tree = {
            "root": [folder("la", "Line A"), folder("lb", "Line B")],
            "la": [folder("fd", "Foundation")],
            "fd": [file("n1", "RL-A2TL-TL-CIV-FND-2009-CAT-A.pdf"), file("n2", ".DS_Store"),
                   file("n3", "RL-A2TL-TL-CIV-FND-2009-CAT-A(1).pdf"), file("n4", "RL-A2TL-TL-CIV-FND-2511-R1.pdf")],
        }
        self.content = {"n1": (SAMPLES / "DA -3m to +3m - WFR.pdf").read_bytes(), "n2": b"\x00\x00\x00\x01Bud1",
                        "n3": (SAMPLES / "DA -3m to +3m - WFR.pdf").read_bytes(), "n4": (SAMPLES / "DB2 ULE 1M RC DRY_R1.pdf").read_bytes()}
        self.broken = {"lb"}  # listed by its parent, but listing it says R1004
        self.tokens, self.logins, self.downloads, self.fail_next, self.down = set(), 0, [], 0, False

    def handle(self, method, path, body, token):
        if self.down:
            return 503, "Service Unavailable"
        if self.fail_next:
            self.fail_next -= 1
            return 502, "Bad Gateway"
        if path == "/DeepApp/api/authenticate":
            if body != {"username": "api.user", "password": "secret"}:
                return 401, {"error": True, "errorReason": 1, "errorMessage": "Bad credentials"}
            self.logins += 1
            tok = f"t{self.logins}"
            self.tokens.add(tok)
            return 200, {"token": tok, "refreshToken": "r", "error": False, "tokenExpiry": str(int((time.time() + 300) * 1000))}
        if token not in self.tokens:
            return 401, {"error": True, "errorReason": 4, "errorMessage": "Bad Token"}
        rt = "/DeepApp/deep-rest/api/v1/repo/runtime"
        missing = 500, {"success": False, "errorCode": "R1004", "message": "Node not found"}
        if path == rt + "/find/path":
            return (200, {"success": True, "data": {"nodeId": "root"}}) if body["path"] == ROOT else missing
        if path == rt + "/nodes":
            node = body["parentNodeId"]
            if node in self.broken or node not in self.tree:
                return missing
            return 200, {"success": True, "errorCode": None, "message": None, "data": {"nodeList": self.tree[node]}}
        if path.startswith(rt + "/file/"):
            node = path.rsplit("/", 1)[1]
            self.downloads.append(node)
            if node in self.tree:
                data = b"Error downloading content"
            elif node in self.content:
                data = self.content[node]
            else:
                return missing
            return 200, {"success": True, "errorCode": None, "message": None, "data": {"name": node, "content": base64.b64encode(data).decode()}}
        return 404, "Not Found"


@pytest.fixture
def fake(tmp_path, monkeypatch):
    f = Fake()

    class Handler(BaseHTTPRequestHandler):
        def _go(self, method):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n)) if n else None
            token = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
            status, res = f.handle(method, self.path, body, token)
            raw = json.dumps(res).encode() if isinstance(res, dict) else res.encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self._go("GET")

        def do_POST(self):
            self._go("POST")

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(settings, "DOCHUB_URL", f"http://127.0.0.1:{server.server_port}/DeepApp")
    monkeypatch.setattr(settings, "DOCHUB_USERNAME", "api.user")
    monkeypatch.setattr(settings, "DOCHUB_PASSWORD", "secret")
    monkeypatch.setattr(settings, "DOCHUB_ROOTS", [ROOT])
    monkeypatch.setattr(settings, "DRAWINGS_DIR", "")
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(settings, "SERVICE_KEY", "k")
    monkeypatch.setattr(dochub, "_client", None)
    monkeypatch.setattr(dochub.time, "sleep", lambda s: None)  # retry back-off
    yield f
    server.shutdown()


def test_walk_reports_a_folder_it_cannot_list_and_carries_on(fake):
    files, failed = dochub.DocHub().walk(ROOT)
    assert [(f["nodeId"], f["folder"]) for f in files][:2] == [("n1", f"{ROOT}>Line A>Foundation"), ("n2", f"{ROOT}>Line A>Foundation")]
    assert len(files) == 4 and [x["folder"] for x in failed] == [f"{ROOT}>Line B"] and "R1004" in failed[0]["error"]
    with pytest.raises(dochub.NotFound):
        dochub.DocHub().find_path("Sterlite Power>Nowhere")


def test_download_accepts_only_a_real_pdf(fake):
    dh = dochub.DocHub()
    assert dh.download("n1") == fake.content["n1"]
    with pytest.raises(dochub.DocHubError, match="not a PDF"):
        dh.download("fd")  # a folder id: DocHub says success, the content is an error text
    with pytest.raises(dochub.DocHubError, match="not a PDF"):
        dh.download("n2")
    with pytest.raises(dochub.NotFound):
        dh.download("nope")


def test_token_reuse_relogin_on_401_and_bad_credentials(fake):
    dh = dochub.DocHub()
    dh.children("root"), dh.children("la")
    assert fake.logins == 1  # one login while the token is valid
    fake.tokens.clear()  # token revoked or expired early
    assert dh.children("la")[0]["nodeId"] == "fd" and fake.logins == 2
    dh._expires = time.time()  # stated expiry reached: a fresh login before the call
    dh.children("la")
    assert fake.logins == 3
    with pytest.raises(dochub.DocHubError, match="login failed"):
        dochub.DocHub(password="wrong").children("root")


def test_gateway_errors_are_retried_and_an_outage_is_an_error(fake):
    fake.fail_next = 2
    assert dochub.DocHub().children("la")  # two 502s, third attempt works
    fake.down = True
    with pytest.raises(dochub.DocHubError):
        dochub.DocHub().children("la")


def test_index_from_dochub_end_to_end(fake):
    counts = source.reindex()
    assert {k: counts[k] for k in ("seen", "new", "unchanged", "skipped", "removed", "unreadable")} == \
        {"seen": 3, "new": 3, "unchanged": 0, "skipped": 1, "removed": 0, "unreadable": 0}
    assert [x["folder"] for x in counts["failed_folders"]] == [f"{ROOT}>Line B"]
    assert sorted(fake.downloads) == ["n1", "n3", "n4"]  # .DS_Store never downloaded

    client = TestClient(app)
    lib = client.get("/api/v1/library", headers=H).json()
    rows = {r["dochub_id"]: r for r in lib["drawings"]}
    assert lib["count"] == 3 and rows["n1"]["folder"] == f"{ROOT}>Line A>Foundation"
    assert {rows["n1"]["selection"], rows["n3"]["selection"]} == {"selected", "same_file"}  # the duplicate upload
    assert rows["n1"]["selected_key"] == rows["n3"]["selected_key"] in ("dochub/n1", "dochub/n3")
    a = rows["n1"]
    assert (a["drawing_no"], a["tower_type"], a["ext_from"], a["ext_to"], a["raised_chimney"], a["leg_type"], a["soil_code"],
            a["title_status"]) == (N2009, "DA", -3.0, 3.0, 0.0, "Normal", "WFR", "ok")
    assert rows["n4"]["leg_type"] == "Unequal" and rows["n4"]["rev"] == 1.0

    # unchanged: nothing downloaded again
    fake.downloads.clear()
    assert source.reindex()["unchanged"] == 3 and fake.downloads == []
    # a new version (DocHub keeps the node id, the date changes) is downloaded and read again
    fake.tree["fd"][3] = file("n4", "RL-A2TL-TL-CIV-FND-2511-R1.pdf", created="17/11/2026 10:00:00 AM")
    assert source.reindex()["changed"] == 1 and fake.downloads == ["n4"]
    # gone from DocHub → removed, not deleted, and no longer selected
    del fake.tree["fd"][2]
    assert source.reindex()["removed"] == 1
    rows = {r["dochub_id"]: r for r in client.get("/api/v1/library", headers=H).json()["drawings"]}
    assert rows["n3"]["status"] == "removed" and rows["n3"]["selection"] == "removed" and rows["n1"]["selection"] == "selected"
    assert len(client.get("/api/v1/library", params={"status": "active"}, headers=H).json()["drawings"]) == 2
    # a folder that cannot be listed this time: its files are not taken as removed
    fake.broken.add("fd")
    assert source.reindex()["removed"] == 0
    # DocHub down: the run is logged with the failure, the library keeps what it has
    fake.broken.discard("fd")
    fake.down = True
    counts = source.reindex()
    assert counts["removed"] == 0 and counts["failed_folders"][0]["folder"] == ROOT
    fake.down = False
    runs = client.get("/api/v1/drawings/reindex", headers=H).json()
    assert runs["running"] is False and len(runs["runs"]) == 6 and runs["runs"][0]["failed_folders"][0]["folder"] == ROOT
    assert client.get("/api/v1/drawings", params={"drawing_no": N2009}, headers=H).json()["status"] == "matched"


def test_jobs_and_pdf_from_dochub(fake):
    source.reindex()
    client = TestClient(app)
    pits = {p: {"body_ext": 0, "leg_ext": -3} for p in "ABCD"}
    r = client.post("/api/v1/ocr/jobs", json={"drawing_no": N2009, "pits": pits}, headers=H).json()
    assert r["status"] == "done" and r["problems"] == [] and r["fields"]["pits"]["A"]["pit_length"]["value"] == 4.13
    (f,) = r["files"]
    assert f["dochub_id"] in ("n1", "n3") and f["name"].startswith(N2009) and f["pits"] == list("ABCD")
    # the PDF itself, served from the local copy; downloaded again if that copy is gone
    pdf = client.get("/api/v1/drawings/pdf", params={"key": "dochub/n4"}, headers=H)
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.content == fake.content["n4"]
    for p in (settings.DATA_DIR / "pdfs").glob("*.pdf"):
        p.unlink()
    fake.downloads.clear()
    assert client.get("/api/v1/drawings/pdf", params={"key": "dochub/n4"}, headers=H).content == fake.content["n4"]
    assert fake.downloads == ["n4"]
    assert client.get("/api/v1/drawings/pdf", params={"key": "dochub/zzz"}, headers=H).status_code == 404
    assert client.get("/api/v1/drawings/pdf", params={"key": "dochub/n4"}).status_code == 401


def test_parser_upgrade_rewrites_every_row_from_the_cached_pdfs(fake, monkeypatch):
    from app import extract
    source.reindex()
    fake.downloads.clear()
    monkeypatch.setattr(extract, "PARSER_VERSION", 99)
    monkeypatch.setattr(source, "PARSER_VERSION", 99)
    counts = source.reindex()
    assert (counts["changed"], counts["unchanged"]) == (3, 0) and fake.downloads == []  # the identical copy too
    assert {store.drawing_entry(k)["parser_version"] for k in ("dochub/n1", "dochub/n3", "dochub/n4")} == {99}
    assert source.reindex()["unchanged"] == 3
