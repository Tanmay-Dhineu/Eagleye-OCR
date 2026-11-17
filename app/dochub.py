"""DocHub (Sterlite's document store) client, read-only: log in, walk folders, download files.

Behaviour seen on UAT that this code relies on:
- the access token lives 300 s; an expired or bad token gets HTTP 401;
- "Node not found" (errorCode R1004, HTTP 500) also comes back for some folders that their parent lists;
- downloading something that is not a file (a folder id) still says success, with the text
  "Error downloading content" as the content, so every download is checked to be a PDF.
Credentials and tokens are never logged."""
import base64
import binascii
import json
import logging
import threading
import time
import urllib.error
import urllib.request

from . import settings

log = logging.getLogger("eagleye-ocr.dochub")
RUNTIME = "/deep-rest/api/v1/repo/runtime"
RETRY_STATUS = {502, 503, 504}


class DocHubError(Exception):
    pass


class NotFound(DocHubError):
    pass


class DocHub:
    def __init__(self, url=None, username=None, password=None, timeout=None):
        self.url = (url or settings.DOCHUB_URL).rstrip("/")
        self.username = username or settings.DOCHUB_USERNAME
        self.password = password or settings.DOCHUB_PASSWORD
        self.timeout = timeout or settings.DOCHUB_TIMEOUT
        self._token, self._expires = None, 0.0
        self._lock = threading.Lock()

    def find_path(self, path):
        """'A>B>C' (DocHub's own separator) → node id."""
        return self._call("POST", "/find/path", {"path": path})["nodeId"]

    def children(self, node_id):
        return self._call("POST", "/nodes", {"parentNodeId": node_id}).get("nodeList") or []

    def walk(self, root_path):
        """Every file under root_path, each with its `folder` path. A folder whose listing fails is reported in
        `failed` and skipped, so one bad folder does not hide the rest (and its files are not taken as removed)."""
        files, failed, queue = [], [], [(self.find_path(root_path), root_path)]
        while queue:
            node_id, path = queue.pop(0)
            try:
                nodes = self.children(node_id)
            except DocHubError as e:
                log.warning("cannot list %s: %s", path, e)
                failed.append({"folder": path, "error": str(e)})
                continue
            for n in nodes:
                if n.get("deleted") or n.get("archived") or n.get("purged"):
                    continue
                if n.get("nodeTypeQname") == "deep:folder":
                    queue.append((n["nodeId"], f"{path}>{n['name']}"))
                else:
                    files.append({**n, "folder": path})
        return files, failed

    def download(self, node_id):
        """The file's bytes; only a real PDF is accepted."""
        data = self._call("GET", f"/file/{node_id}")
        try:
            content = base64.b64decode(data.get("content") or "", validate=True)
        except binascii.Error as e:
            raise DocHubError(f"file {node_id}: content is not base64") from e
        if not content.startswith(b"%PDF-"):
            raise DocHubError(f"file {node_id}: not a PDF ({content[:40]!r})")
        return content

    def _call(self, method, path, body=None):
        for attempt in (1, 2):
            status, res = self._http(method, RUNTIME + path, body, self._bearer())
            if status == 401 and attempt == 1:  # expired before its stated expiry: log in again, once
                with self._lock:
                    self._token = None
                continue
            if isinstance(res, dict) and res.get("success") and status == 200:
                return res.get("data") or {}
            code, msg = (res.get("errorCode"), res.get("message") or res.get("errorMessage")) if isinstance(res, dict) else (None, res)
            raise (NotFound if code == "R1004" else DocHubError)(f"{method} {path}: HTTP {status} {code or ''} {msg or ''}".strip())

    def _bearer(self):
        with self._lock:
            if not self._token or time.time() > self._expires - 30:
                if not (self.username and self.password):
                    raise DocHubError("DocHub username/password not set")
                status, res = self._http("POST", "/api/authenticate", {"username": self.username, "password": self.password})
                if status != 200 or not isinstance(res, dict) or res.get("error") or not res.get("token"):
                    raise DocHubError(f"DocHub login failed: HTTP {status}")
                self._token = res["token"]
                self._expires = int(res.get("tokenExpiry") or 0) / 1000 or time.time() + 240
            return self._token

    def _http(self, method, path, body=None, token=None):
        """(status, parsed JSON or text). Network errors and gateway errors are retried; others are returned."""
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(3):
            if attempt:
                time.sleep(2 ** attempt)
            req = urllib.request.Request(self.url + path, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    status, raw = r.status, r.read()
            except urllib.error.HTTPError as e:
                status, raw = e.code, e.read()
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                log.warning("DocHub %s %s: %s (attempt %d)", method, path, e, attempt + 1)
                if attempt == 2:
                    raise DocHubError(f"DocHub unreachable: {e}") from e
                continue
            if status in RETRY_STATUS and attempt < 2:
                continue
            try:
                return status, json.loads(raw)
            except ValueError:
                return status, raw[:200].decode(errors="replace")


_client = None


def client():
    global _client
    if _client is None:
        _client = DocHub()
    return _client
