import os
from pathlib import Path

SERVICE_KEY = os.environ.get("OCR_SERVICE_KEY", "")           # empty = auth off (local dev only)
DATA_DIR = Path(os.environ.get("OCR_DATA_DIR", "data"))        # sqlite db + downloaded PDFs
DRAWINGS_DIR = os.environ.get("OCR_DRAWINGS_DIR", "")          # local folder instead of DocHub (dev, tests)
DOCHUB_URL = os.environ.get("DOCHUB_URL", "")                  # e.g. https://dochub-uat.sterlitepower.com/DeepApp
DOCHUB_USERNAME = os.environ.get("DOCHUB_USERNAME", "")
DOCHUB_PASSWORD = os.environ.get("DOCHUB_PASSWORD", "")        # as DocHub issued it (already an encrypted string)
# folders to read, DocHub path syntax ("A>B>C"), several separated by ";"; everything below each is read
DOCHUB_ROOTS = [s.strip() for s in os.environ.get("DOCHUB_ROOTS", "").split(";") if s.strip()]
DOCHUB_TIMEOUT = int(os.environ.get("DOCHUB_TIMEOUT", "60"))
DPI = int(os.environ.get("OCR_DPI", "400"))
OCR_THREADS = int(os.environ.get("OCR_THREADS", "4"))
# which revision descriptions count as "approved"; empty = every issued revision counts (DocHub's
# "Documents for Execution" folder only holds drawings released for execution)
APPROVED_ISSUES = [s.strip().upper() for s in os.environ.get("OCR_APPROVED_ISSUES", "").split(",") if s.strip()]
MAX_UPLOAD_MB = int(os.environ.get("OCR_MAX_UPLOAD_MB", "50"))
DOCS = os.environ.get("OCR_DOCS", "") == "1"                   # /docs and /openapi.json, which need no key: dev only
