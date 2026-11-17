# eagleye-ocr

CR-01 foundation drawing extraction for Eagleye. Reads the approved foundation drawings (PDF) from DocHub,
says what each drawing is for (from its title), and extracts the predefined values as key-value data per pit.
OCR (Tesseract) plus deterministic rules; no LLM. See [PLAN.md](PLAN.md).

## Server setup (Ubuntu/Debian, no Docker)

The only non-Python dependency is Tesseract (scanned drawings). The server must reach DocHub (internal network / VPN).

```sh
sudo apt-get install -y tesseract-ocr curl
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh
sudo useradd --system --home /opt/eagleye-ocr eagleye-ocr
sudo git clone <repo> /opt/eagleye-ocr && cd /opt/eagleye-ocr
sudo uv sync --frozen --no-dev                             # creates /opt/eagleye-ocr/.venv
sudo cp .env.example .env && sudo nano .env                # service key, DocHub user/password, roots
sudo install -d -o eagleye-ocr /var/lib/eagleye-ocr        # OCR_DATA_DIR: sqlite + PDF cache
sudo chown eagleye-ocr .env && sudo chmod 600 .env
```

`/etc/systemd/system/eagleye-ocr.service`:

```ini
[Unit]
Description=eagleye-ocr (CR-01 drawing extraction)
After=network-online.target

[Service]
User=eagleye-ocr
WorkingDirectory=/opt/eagleye-ocr
EnvironmentFile=/opt/eagleye-ocr/.env
ExecStart=/opt/eagleye-ocr/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8090
Restart=always

[Install]
WantedBy=multi-user.target
```

```sh
sudo systemctl daemon-reload && sudo systemctl enable --now eagleye-ocr
curl -s localhost:8090/health
```

Read DocHub twice a day (cron of the service user, or Laravel's scheduler calling the same endpoint):

```cron
0 6,18 * * * curl -fsS -X POST -H "X-Service-Key: <key>" http://127.0.0.1:8090/api/v1/drawings/reindex
```

The first run downloads and reads every drawing (Cuddapah CAT-A: 73 PDFs, 22 MB, about 13 minutes on one core and
~500 MB RAM); later runs only list the folders (about 25 s) and download just new or changed files. Upgrade: `git pull && uv sync --frozen --no-dev && systemctl restart eagleye-ocr`; the cache
in `OCR_DATA_DIR` is kept (a parser upgrade re-reads from the cached PDFs, without downloading them again).

## API

All calls except `/health` need the `X-Service-Key` header. Everything is read-only towards DocHub and Eagleye.

| Call | What it does |
|---|---|
| `GET /health` | up, source, last DocHub sync |
| `POST /api/v1/drawings/reindex` | read DocHub now (background); `GET` the same path for the log of each run |
| `GET /api/v1/library[?status=active]` | one flat key-value row per drawing file: DocHub id, name, folder, drawing no., revision, tower type, angle, extension from/to, raised chimney, leg type, soil, variant, and `selection` (is this the file its drawing number selects) |
| `GET /api/v1/drawings/pdf?key=…` | the PDF itself |
| `GET /api/v1/drawings?drawing_no=…` | match a typed drawing number (matched / probable / ambiguous / multiple / missing / not_approved) |
| `POST /api/v1/drawings/upload` | manual fallback: PDF as the body; returns a `key` for a job |
| `POST /api/v1/ocr/jobs` | extract values; `done` at once when the drawing was read before, else `queued` + `job_id` |
| `GET /api/v1/ocr/jobs/{id}` | poll a queued job |

A job takes one drawing for the tower, or one per pit (as Eagleye's picker gives them):

```json
{"pits": {"A": {"key": "dochub/1564932270017409024", "body_ext": 0, "leg_ext": 3},
          "C": {"drawing_no": "RL-A2TL-TL-CIV-FND-9114", "body_ext": 0, "leg_ext": 3}}}
```

The answer has `files` (which file each pit's values come from: DocHub id, name, folder, drawing no., revision)
and `fields`: values by meaning (`pit_length`, `cement_kg`, `b2b_AB`, …) in the forms' units, each with a status:
`ok` (exact text, or readings agreed and checks passed), `resolved` (readings disagreed, the drawing's own arithmetic
picked the right one), `unverified` (read by OCR with nothing to cross-check it; the user checks it) or `flagged`
(not released; the user types it). Nothing is released for a drawing whose number, sheets or revision do not agree.

## Develop

```sh
uv sync
uv run --env-file .env uvicorn app.main:app --port 8090   # OCR_DOCS=1 in .env turns on /docs
uv run pytest            # the OCR tests need tesseract on PATH and are skipped without it; run them on Linux
```
