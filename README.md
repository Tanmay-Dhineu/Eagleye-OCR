# eagleye-ocr

Reads the approved foundation drawings (PDF) from **DocHub**, works out what each drawing is for (from its title
block), and extracts the values the Foundation forms need (pit depth, pit size, PCC, cement, concrete volumes,
bar bending schedule …). The **Eagleye web portal** pulls these values twice a day and passes them to the site app,
which pre-fills the forms. OCR (Tesseract) plus fixed rules; no AI/LLM, no external cloud service.
Design notes: [PLAN.md](PLAN.md).

This README is written for the person who deploys it on a server. Follow sections 1–7 in order.

---

## 1. How it fits together

```
  DocHub (Sterlite, private network)
        │  06:00 and 18:00 — cron on the OCR server calls POST /api/v1/drawings/reindex
        ▼
  eagleye-ocr  (this service, port 8090, Python)
        │  06:30 and 18:30 — Eagleye web's scheduler runs `php artisan cr01:sync-drawings`
        │                    (GET /api/v1/library and /api/v1/library/fields)
        ▼
  Eagleye web DB  ──►  Eagleye API  ──►  site app (pre-filled Foundation forms)
```

- The service only **reads** DocHub; it never writes to DocHub or to the Eagleye database.
- It is called only by the Eagleye web server. **Never expose it to the internet.**
- It keeps its own small state in one folder (`OCR_DATA_DIR`): a SQLite file and the downloaded PDFs.

## 2. What the server needs

| Item | Requirement |
|---|---|
| OS | Ubuntu 24.04 LTS recommended (22.04 / Debian 12 also fine). No Docker needed. |
| CPU / RAM | 2+ cores, 1 GB free RAM (a full read uses ~500 MB). |
| Disk | ~1 GB for `OCR_DATA_DIR` (73 drawings = 22 MB of PDFs; grows with more projects). |
| Network out | **Must reach the DocHub host** (it is on Sterlite's private network: office network / VPN / site-to-site). |
| Network in | Port 8090 reachable **only from the Eagleye web server** (or only from localhost if both run on one server). |
| Packages | `tesseract-ocr`, `git`, `curl`. Python 3.12 is installed by `uv` (step 3) if the OS does not have it. |
| From the team | Git access to this repo, the DocHub **API user + password** for the environment (UAT and prod users are different), the DocHub folder path(s) to read, and a new random service key. |

Check DocHub is reachable **before** installing anything:

```sh
curl -sS -o /dev/null -w "%{http_code}\n" https://dochub-uat.sterlitepower.com/DeepApp/
# any HTTP code (200/302/401/404) = reachable;  "Could not resolve host" or a timeout = not reachable, fix the network first
```

## 3. Install

Run as a user with sudo.

```sh
# 3.1 system packages
sudo apt-get update
sudo apt-get install -y tesseract-ocr git curl
tesseract --version                       # must print a version

# 3.2 uv (Python package manager; installs the exact library versions pinned in uv.lock)
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh
uv --version

# 3.3 service user (no login) and data folder
sudo useradd --system --no-create-home --home-dir /var/lib/eagleye-ocr --shell /usr/sbin/nologin eagleye-ocr
sudo install -d -o eagleye-ocr -g eagleye-ocr -m 750 /var/lib/eagleye-ocr

# 3.4 code (use a deploy key or token if the repo is private)
sudo git clone https://github.com/Tanmay-Dhineu/Eagleye-OCR.git /opt/eagleye-ocr
cd /opt/eagleye-ocr

# 3.5 Python + libraries into /opt/eagleye-ocr/.venv
#     UV_PYTHON_INSTALL_DIR keeps any downloaded Python outside /root, so the service user can run it
sudo env UV_PYTHON_INSTALL_DIR=/opt/uv-python uv sync --frozen --no-dev
```

The code stays owned by root; the service can write only to `/var/lib/eagleye-ocr`.

## 4. Configure (`/opt/eagleye-ocr/.env`)

```sh
sudo cp .env.example .env
openssl rand -hex 32                      # use the output as OCR_SERVICE_KEY
sudo nano .env
sudo chown eagleye-ocr:eagleye-ocr .env && sudo chmod 600 .env
```

| Variable | Set it to | Notes |
|---|---|---|
| `OCR_SERVICE_KEY` | the random key from `openssl` | **Required.** Eagleye web sends the same value. Empty = no auth (local dev only, never on a server). |
| `OCR_DATA_DIR` | `/var/lib/eagleye-ocr` | SQLite + PDF cache. |
| `DOCHUB_URL` | UAT: `https://dochub-uat.sterlitepower.com/DeepApp` · prod: the prod DocHub URL | No trailing `/api`. |
| `DOCHUB_USERNAME` | DocHub API user for this environment | The UAT API user does not exist on prod. |
| `DOCHUB_PASSWORD` | exactly as DocHub issued it | It is already an encrypted-looking string; paste it as is, no quotes. |
| `DOCHUB_ROOTS` | DocHub folder path(s), `>` between levels, `;` between several roots | e.g. `Sterlite Power>Inhouse Design Solution>Infra>Documents for Execution>Anantapur-II REZ Transmission Limited`. Everything below each root is read. |
| `OMP_THREAD_LIMIT` | `1` | Keep at 1 (Tesseract is faster this way here). |
| `OCR_THREADS` | number of CPU cores (default 4) | Parallel OCR reads. |
| `OCR_DPI` | `400` | Leave as is. |
| `OCR_DRAWINGS_DIR` | *(empty)* | Dev only: read PDFs from a local folder instead of DocHub. |
| `DOCHUB_TIMEOUT` | *(optional, default 60)* | Seconds per DocHub call. |
| `OCR_APPROVED_ISSUES` | *(optional, empty)* | Comma-separated revision descriptions that count as approved; empty = every issued revision (the "Documents for Execution" folder only holds released drawings). |
| `OCR_MAX_UPLOAD_MB` | *(optional, default 50)* | Limit for the manual PDF upload call. |
| `OCR_DOCS` | *(leave unset)* | `1` opens `/docs` **without a key** — dev only. |
| `HTTPS_PROXY` | *(only if the server reaches DocHub through a proxy)* | e.g. `http://proxy.local:3128`. |

`.env` rules (systemd reads this file): one `NAME=value` per line, no quotes, **comments only on their own line**
(an inline `# …` would become part of the value).

## 5. Run as a service

Create `/etc/systemd/system/eagleye-ocr.service`:

```ini
[Unit]
Description=eagleye-ocr (CR-01 drawing extraction)
After=network-online.target
Wants=network-online.target

[Service]
User=eagleye-ocr
Group=eagleye-ocr
WorkingDirectory=/opt/eagleye-ocr
EnvironmentFile=/opt/eagleye-ocr/.env
ExecStart=/opt/eagleye-ocr/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8090
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- **Web and OCR on the same server:** keep `--host 127.0.0.1`.
- **On different servers:** use `--host 0.0.0.0` and allow port 8090 only from the web server, e.g.
  `sudo ufw allow from <web-server-ip> to any port 8090 proto tcp`. Traffic is plain HTTP inside the private
  network, protected by the service key.
- **Do not add `--workers`.** The service must run as one process (it guards against two DocHub reads at once
  inside the process).

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now eagleye-ocr
sudo systemctl status eagleye-ocr --no-pager
curl -s http://127.0.0.1:8090/health
# {"ok":true,"source":"dochub","last_sync":null}      ← "source" must say "dochub"; "none" = DOCHUB_URL is empty
```

## 6. First read of DocHub, then the twice-a-day schedule

### 6.1 First read (by hand)

```sh
KEY=$(sudo grep '^OCR_SERVICE_KEY=' /opt/eagleye-ocr/.env | cut -d= -f2-)
curl -s -X POST -H "X-Service-Key: $KEY" http://127.0.0.1:8090/api/v1/drawings/reindex
# {"status":"started"}
```

The first run downloads and reads every drawing: about **13 minutes and ~500 MB RAM for 73 PDFs** (Cuddapah CAT-A).
Follow it with `sudo journalctl -u eagleye-ocr -f`, or check the run log:

```sh
curl -s -H "X-Service-Key: $KEY" http://127.0.0.1:8090/api/v1/drawings/reindex
```

The run is done when `"running": false` and the newest run has a `finished_at`. Check it:

- `error` is `null`
- `failed_folders` is `[]` (a folder listed there could not be read from DocHub; its drawings are kept, not removed)
- `counts.seen` is the number of PDFs you expect; `counts.unreadable` should be 0 or only non-drawing files
  (e.g. a design report PDF in the drawing folder)

Then confirm values came out: `curl -s -H "X-Service-Key: $KEY" http://127.0.0.1:8090/api/v1/library/fields | head -c 600`
(`"count"` > 0, and entries with `"problems": []`).

Later runs only list the DocHub folders (about 25 s) and download just new or changed files.

### 6.2 Schedule (cron on the OCR server)

Create `/etc/cron.d/eagleye-ocr` (the file name must have no dot; end the file with a newline):

```cron
# Read DocHub at 06:00 and 18:00 (server time); Eagleye web pulls the results at 06:30 and 18:30
0 6,18 * * * eagleye-ocr curl -fsS -X POST -H "X-Service-Key: $(grep '^OCR_SERVICE_KEY=' /opt/eagleye-ocr/.env | cut -d= -f2-)" http://127.0.0.1:8090/api/v1/drawings/reindex > /dev/null
```

This cron is **required**: nothing else tells the service to read DocHub. If the web server uses a different
timezone, keep this run at least 30 minutes before the web's pull.

## 7. Connect the Eagleye web portal

On the **Eagleye web** server (the Laravel admin portal, the release that contains CR-01):

1. Add to the web `.env`:

   ```ini
   OCR_SERVICE_URL=http://127.0.0.1:8090        # or http://<ocr-server-ip>:8090
   OCR_SERVICE_KEY=<same key as in the OCR .env>
   CR01_LINE_IDS=31                             # Eagleye line ids (comma-separated) whose pits get drawing options
   # OCR_SERVICE_TIMEOUT=60                     # optional, seconds
   ```

2. Apply it and create the drawing tables:

   ```sh
   php artisan migrate --force
   php artisan config:cache          # only if this server caches config
   ```

3. Make sure the Laravel scheduler cron already runs on that server
   (`* * * * * php /path/to/eagleye/artisan schedule:run`). It runs `cr01:sync-drawings` at 06:30 and 18:30.

4. After the first DocHub read (6.1) has finished, pull once by hand and check the output:

   ```sh
   php artisan cr01:sync-drawings
   # Drawings: 73 seen, 73 new, 0 changed, 0 removed; N pit options written
   ```

   The drawings then show in the portal under **Transaction > Foundation drawings**.

## 8. Day-to-day

| Task | Command |
|---|---|
| Is it up? | `curl -s http://127.0.0.1:8090/health` |
| Logs | `sudo journalctl -u eagleye-ocr --since today` |
| Restart | `sudo systemctl restart eagleye-ocr` |
| Last DocHub reads | `curl -s -H "X-Service-Key: $KEY" http://127.0.0.1:8090/api/v1/drawings/reindex` |
| Read DocHub now | `curl -s -X POST -H "X-Service-Key: $KEY" http://127.0.0.1:8090/api/v1/drawings/reindex` |

**Upgrade** (keeps the cache; if the parser changed, drawings are re-read from the cached PDFs without
downloading them again):

```sh
cd /opt/eagleye-ocr
sudo git pull
sudo env UV_PYTHON_INSTALL_DIR=/opt/uv-python uv sync --frozen --no-dev
sudo systemctl restart eagleye-ocr
```

**Reset the cache** (only if asked; the next read downloads everything again, ~13 min):
`sudo systemctl stop eagleye-ocr && sudo rm -rf /var/lib/eagleye-ocr/* && sudo systemctl start eagleye-ocr`, then 6.1.

**Backup:** not needed. `OCR_DATA_DIR` is a cache that can be rebuilt from DocHub; Eagleye keeps its own copy of
the results.

## 9. Troubleshooting

| Symptom | Likely cause → fix |
|---|---|
| `systemctl status` shows the service restarting | `journalctl -u eagleye-ocr -n 50`. Usually a wrong path in the unit file, `.env` not readable by `eagleye-ocr`, or `.venv` missing (run step 3.5). |
| `/health` says `"source":"none"` | `DOCHUB_URL` is empty in `.env`. |
| Reindex run `error` mentions 401 / login / Bad Token | Wrong DocHub user or password, or a UAT user used against prod. |
| Reindex run `error` is a timeout / connection refused / name not resolved | The server cannot reach DocHub: network, VPN, firewall, or set `HTTPS_PROXY`. |
| Reindex error "Node not found" for the root | `DOCHUB_ROOTS` path is wrong or the API user has no permission on that folder. |
| `{"status":"already_running"}` | A read is still going; wait for it. |
| Web: `Drawing sync failed: OCR_SERVICE_URL is not set` | Web `.env` missing the line, or config cache not rebuilt (`php artisan config:cache`). |
| Web: `eagleye-ocr /api/v1/library: HTTP 401` | `OCR_SERVICE_KEY` differs between web and OCR `.env`. |
| Web: connection refused / timeout | OCR service down, bound to `127.0.0.1` while the web is on another server, or firewall. |
| `/library/fields` shows `"problems":["not_read"]` | That drawing has not been read yet (first read still running, or it failed to download); the next run retries it. |
| `TesseractNotFoundError` in the log | `sudo apt-get install -y tesseract-ocr`, then restart. |
| Reads are slow | Set `OCR_THREADS` to the number of cores; keep `OMP_THREAD_LIMIT=1`. |

## 10. API reference

All calls except `/health` need the `X-Service-Key` header.

| Call | What it does |
|---|---|
| `GET /health` | up, source, last DocHub read |
| `POST /api/v1/drawings/reindex` | read DocHub now (background); `GET` the same path for the log of each run |
| `GET /api/v1/library[?status=active]` | one flat row per drawing file: DocHub id, name, folder, drawing no., revision, tower type, angle, extension from/to, raised chimney, leg type, soil, variant, and `selection` (is this the file its drawing number selects) |
| `GET /api/v1/library/fields` | ready-to-fill form values (depth, pit size, PCC, cement, stub clearance, concrete volumes, BBS) per selected drawing and leg group; `not_read` until the drawing is read |
| `GET /api/v1/drawings/pdf?key=…` | the PDF itself |
| `GET /api/v1/drawings?drawing_no=…` | match a typed drawing number (matched / probable / ambiguous / multiple / missing / not_approved) |
| `POST /api/v1/drawings/upload` | manual fallback: PDF as the body; returns a `key` for a job |
| `POST /api/v1/ocr/jobs` | extract values; `done` at once when the drawing was read before, else `queued` + `job_id` |
| `GET /api/v1/ocr/jobs/{id}` | poll a queued job |

A job takes one drawing for the tower, or one per pit:

```json
{"pits": {"A": {"key": "dochub/1564932270017409024", "body_ext": 0, "leg_ext": 3},
          "C": {"drawing_no": "RL-A2TL-TL-CIV-FND-9114", "body_ext": 0, "leg_ext": 3}}}
```

The answer has `files` (which file each pit's values come from) and `fields`: values by meaning (`pit_length`,
`cement_kg`, `b2b_AB`, …) in the forms' units, each with a status: `ok` (exact text, or readings agreed and checks
passed), `resolved` (readings disagreed, the drawing's own arithmetic picked the right one), `unverified` (read by OCR
with nothing to cross-check it; the user checks it) or `flagged` (not released; the user types it). Nothing is
released for a drawing whose number, sheets or revision do not agree.

## 11. Develop (local machine)

```sh
uv sync
uv run --env-file .env uvicorn app.main:app --port 8090   # OCR_DOCS=1 in .env turns on /docs
uv run pytest            # the OCR tests need tesseract on PATH and are skipped without it; run them on Linux
```
