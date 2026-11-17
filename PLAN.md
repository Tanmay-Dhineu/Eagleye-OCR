# CR-01 — Foundation drawing extraction (eagleye-ocr)

Final plan, 11 Nov 2026; drawing source changed to DocHub 17 Nov 2026. Scope is §2.1 of
`EagleyeCRs_proposal_v2_technical.pdf`: pick each pit's approved foundation drawing from DocHub, extract the
predefined values, let the user review and confirm them in Eagleye. No LLM anywhere; OCR plus deterministic rules
only. Laravel stays the system of record.

## 1. What we learned from the real drawings

Five distinct sample PDFs (`Eagleye\OCR`), all the Ramboll / Anantapur layout:

| Drawing no. | Rev | Sheets | Text layer | Notes |
|---|---|---|---|---|
| RL-A2TL-TL-CIV-FND-2009 | 0 | 2 | yes | 3 extension rows |
| RL-A2TL-TL-CIV-FND-9112 | 0 | 2 | yes | 5 extension rows, "PER TOWER", MLX TRAN ≠ LONG |
| RL-A2TL-TL-CIV-FND-2212 | 0 | 2 | yes | unequal legs, 9 extension rows |
| RL-A2TL-TL-CIV-FND-2511 | 1 | 4 | yes | sheet 1 uplift legs, sheet 3 compression legs, "PER TWO LEGS", B1/B2 rows swapped |
| RL-A2TL-TL-CIV-FND-2312 | 0 | 4 | **no** | Print-to-PDF: text drawn as curves, page sideways → OCR |

- 4 of 5 need **no OCR**: the values come out of the text layer exactly. Trap: some rows are overprinted
  2–3× ("33661100" for 3610), so the text path must dedupe characters.
- The drawing number is printed in the title block of every sheet and in note 7; D&E confirmed they are
  the same number. Filenames do **not** contain it.
- The drawing's own arithmetic validates nearly every value:
  - NLX = √(MLX_TRAN² + MLX_LONG²)
  - D = H + G3 + G2 + G1 + dp
  - (slab + frustum + step box + chimney + raised chimney) × legs = QUANTITY "CONCRETE"
  - lean concrete = B1² × dp × legs
  - BBS: nos per group = nos/leg × legs; weight = length × nos × unit weight; rows sum to the total
- D&E remarks (ANANDHU sheet): drawing number is the same throughout; B1 is the same on both faces;
  Ext is inside (−150) for undercut and outside for non-undercut; "Lean concrete" replaced "M10".

## 2. Measured accuracy (hands-off pipeline, nothing set by hand)

| Test | Fields | Correct | Wrong | Left for the user |
|---|---|---|---|---|
| 2312 (scanned-type, OCR) | 260 | 260 | 0 | 0 |
| 4 text PDFs rasterised and OCR'd as if scanned, code had never seen them | 516 | 485 (94%) | 0 | 31 |
| Same 4 via the text layer (the normal path) | 516 | all | 0 | 0 |

The CR's proof-of-concept baseline was 85–90%.

**Test suite (58 tests, `tests/`, all passing in Docker with Tesseract).** Every scenario is scored field
by field against an independent answer key: the PDF's own text layer, or DC1's hand-read values.

| Scenario | Filled | Wrong | Flagged for the user |
|---|---|---|---|
| 4 text PDFs (text layer) | 100% | 0 | 0 |
| The same 4 forced through OCR (unseen by the code) | 90–99% | 0 | the rest |
| DC1 (scanned-type) | 100% | 0 | 0 |
| DC1 scanned at 300 / 200 dpi | 100% / 94% | 0 | the rest |
| DC1 blur / speckle + JPEG / skew 0.4° / skew −1.5° | 100% / 98% / 98% / 100% | 0 | the rest |
| DC1 at 200 dpi + 0.8° skew + speckle, combined | 53% | 0 | the rest |
| DC1 with 10% of digit reads deliberately corrupted | 81% | 0 | the rest |
| Single wrong numbers injected into a text PDF (6 cases) | — | 0 | always flagged |

Statuses: `ok` (exact text, or confirmed by arithmetic), `resolved` (readings disagreed, arithmetic
settled it), `unverified` (OCR value no arithmetic can confirm: soil, Ext, PLX; highlighted for the user),
`flagged` (not released). The only unverified values found wrong in testing were two soil-type reads
("ORY FISSURED ROCK"), which match no dropdown option and so reach the form as flagged.
Drawing-level problems (number not confirmed on every sheet, sheets missing, revisions disagreeing)
release nothing at all. "Wrong" stays at 0 because a value is released only when the readings
agree **and** the checks pass; otherwise the field is flagged for the user.

## 3. How it works

```
PDF ──render 400 dpi──► find table cells (ruling lines) ──► fill each cell
                                                              ├─ text layer present → exact text (dedupe)
                                                              └─ no text layer → OSD rotate → Tesseract,
                                                                 two readings per number (1× and 2×)
     ──► Ramboll layout parser (header text locates every table and column)
     ──► checks (the arithmetic above) settle disagreements, flag the rest
     ──► per-pit field mapping for Eagleye forms
```

One parser serves both paths because both produce the same "cells with text". Rotation comes from
Tesseract OSD and is verified by finding the drawing number; if none is found the other orientations
are tried.

### Drawing number
- Read from every sheet's title block and from note 7; all readings must agree (`drawing_no_conflict`
  otherwise). `SHEET n OF N` must be complete and `Rev.` identical on every sheet.
- The DocHub folders are indexed by content (drawing no → DocHub file, version, rev, sheets, title); a file is
  downloaded and read again only when its DocHub version or date changes (see "Drawing source" below).
- Typed Pre-Requisite numbers are canonicalised (upper-case, `-` as the only separator, drop a `REV n`
  suffix, cut a well-formed number out of extra text). Exact match → `matched`. Same project code +
  serial but FDN/FND or missing `TL-` → `probable`, user confirms. Several candidates, or two different
  files with the same number and revision → `ambiguous`. A field naming several drawings ("…2102 &
  …2122") → `multiple`, one match per number. Else `missing`. Nothing is guessed. On the 2,731 typed
  entries of this project in the 30 Sep backup: 93% matched, 5% probable, 1.4% missing, 0.4% multiple.
- If the PDF's own number differs from the requested one, no values are released (wrong drawing).

### Drawing source: DocHub
DocHub UAT API (Service 027 find path, 010 list folder, 009 download base64), checked on 17 Nov 2026:
- Root `Sterlite Power>Inhouse Design Solution>Infra>Documents for Execution>Anantapur-II REZ Transmission Limited`.
  Cuddapah line › Foundation › DA › CAT-A holds 73 PDFs in subfolders by raised chimney (`1MRC`, …) and extension
  (`-3M TO +3`, `6M to 9M`), plus a `.DS_Store`. 2127 is uploaded twice (identical bytes). Listing the Davangere
  line folder returns R1004 "Node not found" (no access or empty): reported in every sync run, not fatal.
- Token lives 300 s (logged in again before expiry, and once more on a 401). Gateway errors and timeouts are
  retried. A folder id "downloads" as success with the text "Error downloading content", so every download must
  start with `%PDF-`. All files show version `0.0`; the change check uses version + created date.
- Every drawing has a text layer; only 2003 (a 17-page design report filed as the drawing) has one scanned page.
  DocHub's 2009 is byte-identical to our sample; its drawing numbers and revisions agree with the file names.
- Sync (`POST /api/v1/drawings/reindex`, twice a day): new/changed files downloaded and read, unchanged skipped,
  failed ones retried next run, vanished ones marked `removed` (kept, never under a folder that failed to list),
  each run logged. First full run 12.5 min on one core (~500 MB RAM); later runs about 25 s (listing only).
- Some PDFs carry pages that are not drawing sheets (2003: a 14-page design report before its 2 sheets; 2123: a
  scanned page after them). Such pages are set aside (`other_pages`); the sheet-completeness check still guards
  against a real sheet that could not be read.

### Drawing library (from each drawing's title)
For Eagleye's per-pit picker, each file's title cell is read into: tower type + angle range, extension from/to,
raised chimney (none = 0), leg type (UNEQUAL LEG → Unequal), soil as printed + code (DFR, WET, SANDY FS, …),
variant (block / anchor). On all 73 CAT-A files every title reads `ok`, every raised-chimney and extension agrees
with the DocHub folder the file sits in, and Tanmay's worked examples come out unchanged (15/1 → 2112 / 2002 /
9114 / 9122; 2/1 → 2102 and 9015 both fit; AP15 → none). Each library row also says whether it is the file a lookup
of its number selects: `selected`, `same_file`, `older_revision`, `conflict`, `not_approved`, `unreadable`, `removed`.

### Field mapping, per pit
The service returns values by meaning (`cement_kg`, `pit_length`, `b2b_AB`, …) in the forms' units. Eagleye binds
each one to its form field **by the field's label in the form's current template**: the keys are not consistent
(all four Stub Setting forms use `…_PitA`, all four PCC forms `…_PITD`, the Frustum forms have no pit suffix).

| Eagleye form | Value |
|---|---|
| Pre-Requisite | drawing number as printed |
| Pit Marking | soil type → the dropdown **label** the app stores ("DFR", "WFR", …); depth = D; back-to-back and diagonals below |
| PCC A–D | cement (kg) = lean concrete ÷ legs covered × 4.4 × 50 (÷2 on "per two legs" sheets) |
| Stub Setting A–D | length = width = B1 − 2 × Ext at an undercut pit, B1 otherwise; depth = D |
| Reinforcement A–D | bar rows: type, length **in metres** (drawing: mm), number per leg, diameter (mm) |
| Frustum / step box / chimney A–D | the volume formulas, verified by the concrete identity |

Back-to-back (outer edge to outer edge) = MLX_i + MLX_j + half_i + half_j, half = B1/2 (− Ext if undercut).
**AB and CD use MLX,LONG; BC and AD use MLX,TRAN.** Diagonal = √(long side² + transverse side²).
These three rules come from the site's own entries, not only the mapping sheet: on the 41 towers of drawings 2009 and
9112 the site entered B1 − 2·Ext as the pit size 31 times out of 40 (plain B1 never), every back-to-back entry that
follows a formula follows this one (12–13 of 34; the rest are measured), and the sheet's NLX × 2 diagonal matched
0 of 34 (it is the centre-to-centre distance). On the unequal-leg drawing 2312 (tower AP54) the same rules give
diagonals of 45.75 against the site's 45.749 / 45.750. They are open with D&E (question 5 below).

Each pit can use a different sheet (uplift vs compression) and a different excavation row, so the output is per
pit. Eagleye pre-selects the row from the tower master's `extention` for equal-leg drawings and asks the user
otherwise; undercut defaults from the drawing's soil type (rock → undercut) and the user can change it per pit.

## 4. What is built

**Service** `eagleye-ocr/` — Python 3.12, FastAPI, run by systemd with uv; the only system package is
`tesseract-ocr` (no Docker, see README). No Redis, no OpenCV, no ORM: a FastAPI background task and SQLite are
enough (text PDFs take ~2–10 s, OCR drawings 30–60 s, every result is cached by file hash + parser version).

```
app/
  settings.py    env config (DocHub or local folder, service key, approved-revision rule, upload limit)
  dochub.py      DocHub client: login/relogin, folder walk, download (PDF check), retries
  cells.py       page → cells (text layer or Tesseract)
  ramboll.py     layout parser: title block, revision table, DIMENSIONS, QUANTITY, soil, BBS, excavation
  title.py       title → library properties (tower type, angle, extension, raised chimney, leg type, soil)
  checks.py      arithmetic checks; ok / resolved / unverified / flagged
  extract.py     whole-drawing extraction: sheet completeness, revision, drawing-number consensus, title
  drawing_no.py  canonicalise and match typed drawing numbers
  mapping.py     per-pit values by meaning, in the forms' units; each pit may use its own drawing
  source.py      DocHub (or a local folder) + content index + sync; manual uploads
  store.py       SQLite jobs, result cache, drawing index and library, sync log
  main.py        API
tests/           ~100 tests incl. a fake DocHub; the 5 sample PDFs; answer keys from the text layer / hand-read values
```

API (all under `X-Service-Key`):
- `GET /health` (no key): up, source, last sync
- `POST /api/v1/drawings/reindex` → sync DocHub in the background; `GET` the same path → log of the last runs
- `GET /api/v1/library[?status=active]` → one flat key-value row per drawing file (DocHub id, name, folder, version,
  drawing no, revision, issued for, title properties, `selection`, `selected_key`)
- `GET /api/v1/drawings/pdf?key=…` → the PDF (for Eagleye to keep its own copy for the site app)
- `GET /api/v1/drawings?drawing_no=…` → `matched | probable | ambiguous | multiple | not_approved | missing` + candidates
- `POST /api/v1/drawings/upload` (body: the PDF) → `key` of a manual upload; uploads never enter the shared index
- `POST /api/v1/ocr/jobs` `{drawing_no?, key?, pits: {A: {body_ext, leg_ext, undercut?, key?, drawing_no?}, …}}` →
  one drawing for the tower or one per pit; `done` with `files` (which file each pit's values come from) and
  per-pit values, `queued` + job id, or the lookup status (and the pits concerned) when a drawing did not resolve
- `GET /api/v1/ocr/jobs/{id}`

"Latest approved revision": the revision table's description ("ISSUED FOR APPROVAL", …) is read; the index takes the
highest revision among approved files. Which descriptions count as approved is `OCR_APPROVED_ISSUES` (empty = all),
pending question 2.

**Eagleye (Laravel)** — Eagleye-CTQ › Transactional Data › **Foundation Drawing Values**:
- tower picker (the Foundation listing's project → package → line → work package → tower dropdowns);
- tower page: the drawing number from the Pre-Requisite form (editable), *Find and read*, or *Upload and read*;
  when no single drawing matches, the candidates are listed and the user picks one (never guessed);
- source drawing, printed number, revision, issued for, PDF text or OCR, extraction version, problems;
- per pit: extension row and undercut;
- the values against the form fields they belong to, with the value on the form today, a status per value
  (OK / Checked / Please check / Not read / Not on form), editable; *Confirm values* stores
  `{form sequence: {field key: value}}` and every change from the drawing;
- `drawing_extractions` table: tower, typed and requested number, drawing used (key, source), revision, issued for,
  extraction version, job, pits, the service's full answer, confirmed values, corrections, errors, who and when.

Permissions: viewing and extracting need `transaction.formsubmission.view`, confirming needs
`transaction.formsubmission.approve`. Config: `OCR_SERVICE_URL`, `OCR_SERVICE_KEY`, `OCR_SERVICE_TIMEOUT`
(`config/microservices.php`); run the `drawing_extractions` migration. Tests:
`tests/Feature/Transaction/DrawingExtractionTest.php` (9 tests, the service faked with its real answers).

**Where the confirmed values go.** Foundation forms are filled in the Android app, which this CR does not change,
and web editing of submissions is not implemented (`formEdit`/`formUpdate`). So the confirmed values are kept per
tower in Eagleye, keyed by the real form field, ready for (a) CR-02's rules to check what the site submits and
(b) an app release that pre-fills them the way it already pre-fills `autofill_*` tower values.

## 5. Order of work
1. Service core: cells, parser, checks, extraction; tests on the 5 samples. ✓
2. Drawing-number matching + content index; measured on the typed numbers in the 30 Sep backup. ✓
3. Per-pit mapping + API. ✓
4. Eagleye review screen, audit table, manual upload. ✓ (stashed while the Eagleye side is re-planned)
5. DocHub source, sync, drawing library from titles, per-pit drawings, no Docker; tested against DocHub UAT. ✓
6. Eagleye: library tables + scheduled sync + per-pit picker + Form 1 / portal (Tanmay); production DocHub
   account and network route; held-out accuracy run on client-approved drawings; UAT.

## 6. Open questions for the client / D&E
1. DocHub: a production API user (only UAT exists), the server's network route to DocHub, access to the Davangere
   line folder (R1004 today), and the folders for other lines / categories than Cuddapah CAT-A.
2. Which revision descriptions count as "approved": "Issued for approval" with the Fichtner Cat-A stamp, or only
   "Released for construction"? (A setting; every sample is "Issued for approval".)
3. Uplift vs compression pits: the mapping uses A, B = uplift sheet and C, D = compression sheet, because on AP54
   (drawing 2312, the only unequal-leg tower with site data) the site entered pit size 4.40 on A, B (uplift B1 4.7
   − 2·Ext) and 3.60 on C, D (compression 3.9 − 2·Ext); with that, our diagonals there are 45.75 against the site's
   45.749 / 45.750. Confirm it holds for every tower.
4. Unequal-leg towers: `leg_a`–`leg_d` are empty in the tower master, so the user picks each pit's row. Should the
   master be filled in?
5. Confirm the rules the site data shows (section 3): pit size B1 − 2·Ext at undercut pits, AB/CD on MLX,LONG and
   BC/AD on MLX,TRAN, diagonal as the outer-corner distance instead of NLX × 2. And for non-undercut pits: no Ext
   adjustment (the sheet) or + Ext (the "outside the footing" remark)? No non-undercut sample has been seen.
6. Are KEC / Sterlite layouts in Release 1? Only Ramboll samples have been received.
7. 10–20 more held-out PDFs, including real scans, for the acceptance run.
