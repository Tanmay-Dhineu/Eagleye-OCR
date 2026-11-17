"""Where the drawings live: DocHub (read-only), or a local folder for dev and tests. Plus the content index."""
import hashlib
import logging
from pathlib import Path

from . import dochub, settings, store
from .extract import PARSER_VERSION, extract, sha256

log = logging.getLogger("eagleye-ocr.source")


def list_files():
    """([{key, etag, name, folder, version, created_on}], failed folders). Every file, PDF or not."""
    if settings.DRAWINGS_DIR:
        root = Path(settings.DRAWINGS_DIR)
        return [{"key": p.name, "etag": f"{p.stat().st_mtime_ns}-{p.stat().st_size}", "name": p.name, "folder": "",
                 "version": None, "created_on": None} for p in sorted(root.iterdir()) if p.is_file()], []
    if not (settings.DOCHUB_URL and settings.DOCHUB_ROOTS):
        raise RuntimeError("no drawing source: set DOCHUB_URL and DOCHUB_ROOTS (or OCR_DRAWINGS_DIR for dev)")
    files, failed = [], []
    for root in settings.DOCHUB_ROOTS:
        try:
            found, bad = dochub.client().walk(root)
        except dochub.DocHubError as e:  # root itself not reachable: nothing under it is taken as removed
            log.error("cannot read %s: %s", root, e)
            failed.append({"folder": root, "error": str(e)})
            continue
        failed += bad
        # a new version of a file keeps its node id; the version and date tell the change
        files += [{"key": f"dochub/{n['nodeId']}", "etag": f"{n.get('version')}|{n.get('createdOn')}", "name": n["name"],
                   "folder": n["folder"], "version": n.get("version"), "created_on": n.get("createdOn")} for n in found]
    return files, failed


def fetch(key, etag):
    """Local path of the PDF, downloaded once per etag."""
    if key.startswith("upload/"):  # manual upload, stored by content hash
        return settings.DATA_DIR / key
    if not key.startswith("dochub/"):
        return Path(settings.DRAWINGS_DIR) / key
    local = settings.DATA_DIR / "pdfs" / (hashlib.sha1(f"{key}@{etag}".encode()).hexdigest() + ".pdf")
    if not local.exists():
        local.parent.mkdir(parents=True, exist_ok=True)
        part = local.with_suffix(".part")
        part.write_bytes(dochub.client().download(key.split("/", 1)[1]))
        part.replace(local)  # never leave a half-written PDF under the final name
    return local


def extract_cached(path):
    digest = sha256(path)
    result = store.get_result(digest, PARSER_VERSION)
    if result is None:
        result = extract(path)
        store.put_result(digest, PARSER_VERSION, result)
    return digest, result


def reindex():
    """Bring the index up to date with the source: read new and changed PDFs (and retry ones that failed before),
    mark files that disappeared as removed. One bad file never stops the run; every run is logged."""
    counts = {"seen": 0, "new": 0, "changed": 0, "unchanged": 0, "unreadable": 0, "removed": 0, "skipped": 0}
    failed, run_id = [], store.start_run()
    try:
        files, failed = list_files()
        seen = set()
        for f in files:
            if not f["name"].lower().endswith(".pdf"):  # e.g. .DS_Store uploaded along with the drawings
                counts["skipped"] += 1
                continue
            counts["seen"] += 1
            seen.add(f["key"])
            entry = store.drawing_entry(f["key"])
            # unchanged = same file, read without error, and its row written by the current parser (an upgrade
            # re-reads from the cached PDF; an identical copy then comes straight from the result cache)
            if entry and entry["etag"] == f["etag"] and entry["sha256"] and entry["status"] == "active" \
                    and entry["parser_version"] == PARSER_VERSION:
                store.touch(f["key"])
                counts["unchanged"] += 1
                continue
            try:
                digest, result = extract_cached(fetch(f["key"], f["etag"]))
            except Exception as e:  # recorded with no hash, so the next run tries again
                log.warning("cannot read %s (%s): %s", f["name"], f["key"], e)
                result, digest = {"drawing_no": None, "rev": None, "sheets": [], "problems": [f"error: {e}"]}, None
                counts["unreadable"] += 1
            counts["changed" if entry else "new"] += 1
            store.put_drawing(f["key"], f["etag"], digest, result, f)
        counts["removed"] = store.mark_removed(seen, [x["folder"] for x in failed], dochub=not settings.DRAWINGS_DIR)
        store.finish_run(run_id, counts, failed)
    except Exception as e:
        store.finish_run(run_id, counts, failed, error=str(e))
        raise
    return {**counts, "failed_folders": failed}
