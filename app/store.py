"""SQLite: extraction results cached by file hash, the drawing index, sync runs and background jobs."""
import json
import sqlite3
import uuid
from datetime import datetime, timezone

from . import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS results (sha256 TEXT, parser_version INT, result TEXT, PRIMARY KEY (sha256, parser_version));
CREATE TABLE IF NOT EXISTS drawings (key TEXT PRIMARY KEY, etag TEXT, sha256 TEXT, drawing_no TEXT, rev REAL, sheets INT,
                                     problems TEXT, indexed_at TEXT, issued_for TEXT);
CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, status TEXT, request TEXT, result TEXT, error TEXT,
                                 created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS sync_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT,
                                      counts TEXT, failed_folders TEXT, error TEXT);
"""
# added after the first release: (column, type); added in place so the extraction cache survives an upgrade
DRAWING_COLUMNS = [("name", "TEXT"), ("folder", "TEXT"), ("source_version", "TEXT"), ("source_created", "TEXT"),
                   ("status", "TEXT DEFAULT 'active'"), ("last_seen", "TEXT"), ("title", "TEXT"), ("parser_version", "INT")]


def _db():
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(settings.DATA_DIR / "ocr.sqlite", timeout=30)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    have = {r["name"] for r in db.execute("PRAGMA table_info(drawings)")}
    for col, typ in DRAWING_COLUMNS:
        if col not in have:
            db.execute(f"ALTER TABLE drawings ADD COLUMN {col} {typ}")
    return db


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_result(sha256, parser_version):
    with _db() as db:
        row = db.execute("SELECT result FROM results WHERE sha256=? AND parser_version=?", (sha256, parser_version)).fetchone()
    return json.loads(row["result"]) if row else None


def put_result(sha256, parser_version, result):
    with _db() as db:
        db.execute("INSERT OR REPLACE INTO results VALUES (?,?,?)", (sha256, parser_version, json.dumps(result)))


def _row(row):
    return {**dict(row), "problems": json.loads(row["problems"] or "[]"), "title": json.loads(row["title"] or "null")}


def drawing_entry(key):
    with _db() as db:
        row = db.execute("SELECT * FROM drawings WHERE key=?", (key,)).fetchone()
    return _row(row) if row else None


def put_drawing(key, etag, sha256, result, meta=None):
    """meta: where the file sits in the source {name, folder, version, created_on}."""
    meta = meta or {}
    with _db() as db:
        db.execute("""INSERT INTO drawings (key, etag, sha256, drawing_no, rev, sheets, problems, indexed_at, issued_for,
                                            name, folder, source_version, source_created, status, last_seen, title, parser_version)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'active',?,?,?)
                      ON CONFLICT(key) DO UPDATE SET etag=excluded.etag, sha256=excluded.sha256, drawing_no=excluded.drawing_no,
                        rev=excluded.rev, sheets=excluded.sheets, problems=excluded.problems, indexed_at=excluded.indexed_at,
                        issued_for=excluded.issued_for, name=excluded.name, folder=excluded.folder,
                        source_version=excluded.source_version, source_created=excluded.source_created, status='active',
                        last_seen=excluded.last_seen, title=excluded.title, parser_version=excluded.parser_version""",
                   (key, etag, sha256, result["drawing_no"], result["rev"], len(result["sheets"]), json.dumps(result["problems"]),
                    _now(), result.get("issued_for"), meta.get("name"), meta.get("folder"), meta.get("version"),
                    meta.get("created_on"), _now(), json.dumps(result.get("title")), result.get("parser_version")))


def touch(key):
    with _db() as db:
        db.execute("UPDATE drawings SET last_seen=? WHERE key=?", (_now(), key))


def mark_removed(seen, failed_folders, dochub):
    """Files of this source that were not listed this run → 'removed' (kept, so old results still say what was used).
    Nothing under a folder that could not be listed counts as removed."""
    def under_failed(folder):
        return any(folder == f or (folder or "").startswith(f + ">") for f in failed_folders)
    with _db() as db:
        rows = db.execute("SELECT key, folder FROM drawings WHERE status='active' AND key NOT LIKE 'upload/%' AND "
                          + ("key LIKE 'dochub/%'" if dochub else "key NOT LIKE 'dochub/%'")).fetchall()
        gone = [r["key"] for r in rows if r["key"] not in seen and not under_failed(r["folder"])]
        db.executemany("UPDATE drawings SET status='removed' WHERE key=?", [(k,) for k in gone])
    return len(gone)


def approved(issued_for):
    return not settings.APPROVED_ISSUES or (issued_for or "").upper() in settings.APPROVED_ISSUES


def drawing_index():
    """{drawing_no: entry} over the active source files whose number was read without conflict. Manual uploads are
    for the tower they were uploaded for only, so they never enter the shared index."""
    with _db() as db:
        rows = db.execute("SELECT * FROM drawings WHERE drawing_no IS NOT NULL AND key NOT LIKE 'upload/%' "
                          "AND status='active'").fetchall()
    # first row per number = latest approved revision (or latest of all when none is approved); key breaks ties
    rows = sorted(rows, key=lambda r: (approved(r["issued_for"]), r["rev"] or 0, r["indexed_at"], r["key"]), reverse=True)
    index = {}
    for r in rows:
        e = index.setdefault(r["drawing_no"], {**_row(r), "others": [], "approved": approved(r["issued_for"])})
        if e["key"] != r["key"]:
            e["others"].append({"key": r["key"], "rev": r["rev"], "sha256": r["sha256"], "name": r["name"]})
    for e in index.values():  # another file, same number and revision, different content: never pick one silently
        e["ambiguous"] = any(o["rev"] == e["rev"] and o["sha256"] != e["sha256"] for o in e["others"])
    return index


def library():
    """Every source file (not manual uploads) with what it is, and whether it is the one a lookup of its drawing
    number selects: selected / same_file (identical copy of the selected one) / older_revision / conflict
    (same number and revision, different content: nothing is selected) / not_approved / unreadable / removed."""
    index = drawing_index()
    with _db() as db:
        rows = [_row(r) for r in db.execute("SELECT * FROM drawings WHERE key NOT LIKE 'upload/%' ORDER BY folder, name, key")]
    for r in rows:
        e = index.get(r["drawing_no"])
        if r["status"] == "removed":
            r["selection"] = "removed"
        elif not r["drawing_no"] or not e:
            r["selection"] = "unreadable"
        elif e["ambiguous"] and r["rev"] == e["rev"]:
            r["selection"] = "conflict"
        elif not approved(r["issued_for"]):
            r["selection"] = "not_approved"
        elif e["key"] == r["key"]:
            r["selection"] = "selected"
        elif r["sha256"] == e["sha256"]:
            r["selection"] = "same_file"
        else:
            r["selection"] = "older_revision"
        r["selected_key"] = e["key"] if e and not e["ambiguous"] else None
    return rows


def start_run():
    with _db() as db:
        return db.execute("INSERT INTO sync_runs (started_at) VALUES (?)", (_now(),)).lastrowid


def finish_run(run_id, counts, failed_folders, error=None):
    with _db() as db:
        db.execute("UPDATE sync_runs SET finished_at=?, counts=?, failed_folders=?, error=? WHERE id=?",
                   (_now(), json.dumps(counts), json.dumps(failed_folders), error, run_id))


def runs(limit=10):
    with _db() as db:
        rows = db.execute("SELECT * FROM sync_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{**dict(r), "counts": json.loads(r["counts"] or "null"), "failed_folders": json.loads(r["failed_folders"] or "[]")}
            for r in rows]


def new_job(request):
    job_id = uuid.uuid4().hex
    with _db() as db:
        db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?)", (job_id, "queued", json.dumps(request), None, None, _now(), _now()))
    return job_id


def update_job(job_id, status, result=None, error=None):
    with _db() as db:
        db.execute("UPDATE jobs SET status=?, result=?, error=?, updated_at=? WHERE id=?",
                   (status, json.dumps(result) if result is not None else None, error, _now(), job_id))


def get_job(job_id):
    with _db() as db:
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        return None
    return {**dict(row), "request": json.loads(row["request"]), "result": json.loads(row["result"]) if row["result"] else None}
