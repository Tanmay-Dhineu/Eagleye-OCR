"""eagleye-ocr API. Laravel calls these with X-Service-Key; nothing here writes to Eagleye or to DocHub."""
import hashlib
import logging
import threading
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import dochub, drawing_no, mapping, settings, source, store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("eagleye-ocr")
app = FastAPI(title="eagleye-ocr", version="0.2.0", **({} if settings.DOCS else {"docs_url": None, "redoc_url": None, "openapi_url": None}))
_reindex_lock = threading.Lock()


def auth(x_service_key: str = Header(default="")):
    if settings.SERVICE_KEY and x_service_key != settings.SERVICE_KEY:
        raise HTTPException(401, "bad service key")


@app.get("/health")
def health():
    last = next(iter(store.runs(1)), None)
    return {"ok": True, "source": "local" if settings.DRAWINGS_DIR else "dochub" if settings.DOCHUB_URL else "none",
            "last_sync": last and {"finished_at": last["finished_at"], "ok": last["finished_at"] is not None and not last["error"]}}


@app.get("/api/v1/drawings", dependencies=[Depends(auth)])
def find_drawing(drawing_no: str):
    m = drawing_no_match(drawing_no)
    fields = ("key", "name", "folder", "drawing_no", "rev", "issued_for", "approved", "sheets", "problems", "others")
    for part in [m, *m.get("parts", [])]:
        part["candidates"] = [{k: c.get(k) for k in fields} for c in part["candidates"]]
    return m


def drawing_no_match(typed):
    return drawing_no.match(typed, store.drawing_index())


@app.post("/api/v1/drawings/reindex", dependencies=[Depends(auth)])
def reindex(background: BackgroundTasks):
    """Read DocHub again: new and changed drawings are downloaded and read, vanished ones marked removed.
    Run it on a schedule (twice a day); the outcome of each run is at GET /api/v1/drawings/reindex."""
    if not _reindex_lock.acquire(blocking=False):
        return {"status": "already_running"}

    def run():
        try:
            log.info("reindex done: %s", source.reindex())
        except Exception:
            log.exception("reindex failed")
        finally:
            _reindex_lock.release()

    background.add_task(run)
    return {"status": "started"}


@app.get("/api/v1/drawings/reindex", dependencies=[Depends(auth)])
def reindex_runs(limit: int = 10):
    return {"running": _reindex_lock.locked(), "runs": store.runs(max(1, min(limit, 100)))}


TITLE_FIELDS = ("tower_type", "angle_from", "angle_to", "ext_from", "ext_to", "raised_chimney", "leg_type", "soil",
                "soil_code", "variant")


@app.get("/api/v1/library", dependencies=[Depends(auth)])
def library(status: Literal["active", "removed"] | None = None):
    """One flat key-value row per drawing file in the source: where it is, what it is (from its title), and whether
    it is the file a lookup of its drawing number selects. Eagleye's drawing library is filled from this."""
    rows = []
    for r in store.library():
        if status and r["status"] != status:
            continue
        t = r["title"] or {}
        rows.append({
            "key": r["key"], "dochub_id": r["key"].split("/", 1)[1] if r["key"].startswith("dochub/") else None,
            "name": r["name"], "folder": r["folder"], "source_version": r["source_version"], "source_created": r["source_created"],
            "status": r["status"], "last_seen": r["last_seen"], "sha256": r["sha256"],
            "drawing_no": r["drawing_no"], "rev": r["rev"], "issued_for": r["issued_for"], "approved": store.approved(r["issued_for"]),
            "sheets": r["sheets"], "problems": r["problems"], "selection": r["selection"], "selected_key": r["selected_key"],
            **{k: t.get(k) for k in TITLE_FIELDS}, "title_status": t.get("status", "missing"), "title_text": t.get("text"),
        })
    return {"count": len(rows), "drawings": rows}


@app.get("/api/v1/drawings/pdf", dependencies=[Depends(auth)])
def drawing_pdf(key: str):
    """The PDF itself (for Eagleye to keep its own copy, so the site app never has to reach DocHub)."""
    entry = store.drawing_entry(key)
    if not entry:
        raise HTTPException(404, "unknown drawing key")
    try:
        path = source.fetch(key, entry["etag"])
    except dochub.DocHubError as e:
        raise HTTPException(502, f"DocHub: {e}")
    if not Path(path).exists():
        raise HTTPException(404, "file no longer available")
    return FileResponse(path, media_type="application/pdf", filename=entry["name"] or Path(key).name)


@app.post("/api/v1/drawings/upload", dependencies=[Depends(auth)])
async def upload(request: Request):
    """Manual fallback when the drawing is not in DocHub: the PDF itself as the request body (application/pdf).
    Returns the `key` to pass to /ocr/jobs. Stored by content hash; never added to the shared index."""
    body = await request.body()
    if len(body) > settings.MAX_UPLOAD_MB << 20:
        raise HTTPException(413, f"PDF larger than {settings.MAX_UPLOAD_MB} MB")
    if not body.startswith(b"%PDF-"):
        raise HTTPException(415, "not a PDF")
    digest = hashlib.sha256(body).hexdigest()
    key = f"upload/{digest}.pdf"
    path = settings.DATA_DIR / key
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    if not store.drawing_entry(key):
        store.put_drawing(key, digest, digest, {"drawing_no": None, "rev": None, "sheets": [], "problems": ["not_read_yet"]})
    return {"key": key, "sha256": digest, "bytes": len(body)}


class Pit(BaseModel):
    body_ext: float
    leg_ext: float
    undercut: bool | None = None
    key: str | None = None         # this pit's own drawing (a library key), when the pits of a tower differ
    drawing_no: str | None = None  # or its typed number


class JobRequest(BaseModel):
    drawing_no: str = ""
    key: str | None = None
    pits: dict[Literal["A", "B", "C", "D"], Pit] = {}


@app.post("/api/v1/ocr/jobs", dependencies=[Depends(auth)], status_code=202)
def create_job(body: JobRequest, background: BackgroundTasks):
    """One drawing for the whole tower ({drawing_no, key?, pits}) or one per pit (key / drawing_no on each pit, as
    Eagleye's picker gives them). `key` = a library row, or a probable match the user confirmed. Nothing is read
    unless every drawing resolves to exactly one file."""
    refs = {}
    for pit, p in body.pits.items():
        ref = (p.key, p.drawing_no or "") if (p.key or p.drawing_no) else (body.key, body.drawing_no)
        refs.setdefault(ref, []).append(pit)
    if not refs:
        refs[(body.key, body.drawing_no)] = []
    drawings, pits = {}, {}
    for (key, typed), names in refs.items():
        entry, failure = _resolve(key, typed)
        if failure:
            return {**failure, "drawing_no": typed, "pits": names}
        requested = None  # picked by key with no typed number: nothing to check the drawing against
        if typed:
            # a confirmed candidate (same project code + serial as typed) makes its printed number the requested one;
            # the wrong-drawing guard in _finish then catches a stale index or a key for some other drawing
            same = entry["drawing_no"] and drawing_no.loose_key(entry["drawing_no"]) == drawing_no.loose_key(typed)
            requested = entry["drawing_no"] if same else typed
        drawings.setdefault(entry["key"], {"etag": entry["etag"], "drawing_no": requested, "typed": typed})
        for n in names:
            pits[n] = {**body.pits[n].model_dump(exclude={"key", "drawing_no"}), "key": entry["key"]}
    first = next(iter(drawings))
    request = {"drawing_no": drawings[first]["drawing_no"], "typed_drawing_no": body.drawing_no, "key": first,
               "etag": drawings[first]["etag"], "drawings": drawings, "pits": pits}
    job_id = store.new_job(request)
    cached = {}
    for key in drawings:
        entry = store.drawing_entry(key)
        result = store.get_result(entry["sha256"], source.PARSER_VERSION) if entry["sha256"] else None
        if result is None:
            break
        cached[key] = result
    else:
        store.update_job(job_id, "done", _finish(cached, request))
        return {"status": "done", "job_id": job_id, "key": first, **store.get_job(job_id)["result"]}
    background.add_task(_run, job_id, request)
    return {"status": "queued", "job_id": job_id, "key": first}


def _resolve(key, typed):
    """(entry, None), or (None, why not) when the typed number does not give exactly one file."""
    if key:
        entry = store.drawing_entry(key)
        if not entry:
            raise HTTPException(404, "unknown drawing key")
        return entry, None
    m = drawing_no_match(typed)
    if m["status"] != "matched":
        return None, {"status": m["status"], "candidates": [c["key"] for c in m["candidates"]]}
    return m["candidates"][0], None


@app.get("/api/v1/ocr/jobs/{job_id}", dependencies=[Depends(auth)])
def get_job(job_id: str):
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "no such job")
    return {k: job[k] for k in ("id", "status", "request", "result", "error", "created_at", "updated_at")}


def _finish(results, request):
    """results: {key: extraction}. Nothing is released if any drawing's number, sheets or revision do not hold together.
    `files` says which file each pit's values come from."""
    multi = len(results) > 1
    problems = []
    for key, result in results.items():
        label = f"{result['drawing_no'] or key}: " if multi else ""
        if mapping.wrong_drawing(result, request["drawings"][key]["drawing_no"]):
            problems.append(label + "drawing_no_mismatch")
        problems += [label + p for p in result["problems"]]
    out = {"files": _files(results, request), "fields": None, "problems": problems}
    out.update({"extractions": results} if multi else {"extraction": next(iter(results.values()))})
    if problems:
        return out
    mapped = mapping.map_fields({p: results[v["key"]] for p, v in request["pits"].items()}, request["pits"])
    return {**out, "fields": mapped["fields"], "problems": mapped["problems"]}


def _files(results, request):
    out = []
    for key, result in results.items():
        e = store.drawing_entry(key) or {}
        out.append({"key": key, "dochub_id": key.split("/", 1)[1] if key.startswith("dochub/") else None,
                    "name": e.get("name"), "folder": e.get("folder"), "source_version": e.get("source_version"),
                    "source_created": e.get("source_created"), "status": e.get("status"), "sha256": e.get("sha256"),
                    "drawing_no": result["drawing_no"], "rev": result["rev"], "issued_for": result.get("issued_for"),
                    "pits": [p for p, v in request["pits"].items() if v["key"] == key]})
    return out


def _run(job_id, request):
    store.update_job(job_id, "running")
    try:
        results = {}
        for key, d in request["drawings"].items():
            digest, results[key] = source.extract_cached(source.fetch(key, d["etag"]))
            if key.startswith("upload/"):  # record what the uploaded drawing turned out to be
                store.put_drawing(key, d["etag"], digest, results[key])
        store.update_job(job_id, "done", _finish(results, request))
    except Exception as e:
        log.exception("job %s failed", job_id)
        store.update_job(job_id, "failed", error=str(e))
