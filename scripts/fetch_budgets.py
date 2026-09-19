"""
Read the quoted hours out of every Project Tools workbook in Drive.

    .\\.venv\\Scripts\\python.exe scripts\\fetch_budgets.py
    .\\.venv\\Scripts\\python.exe scripts\\fetch_budgets.py --limit 5 --verbose

Writes .cache/budgets.json, which build_snapshot.py folds into the snapshot
if it's there. Deliberately a separate script: if a workbook is renamed,
restructured or unshared, the budget side fails on its own and the hours
snapshot still builds.

Two things it can't do on its own:

  Access - the service account has to be given read access to the folder
  holding the Project Tools sheets. It's a separate identity from you; being
  able to open them yourself doesn't help it.

  Mapping - about half the sheet titles carry the job number
  ("3591 - 120 Country Club_AV Tech Project Tools"), which matches a
  jobcode's short_code exactly. The rest ("28 Sky", "740 Sanchez",
  "901 Seabury") don't, so those fall back to matching the site address out
  of the sheet's own header block. Anything still unresolved is written to
  budget_mapping_review.csv for you to fix by hand, and that file is read
  back on the next run.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.budget_parser import (  # noqa: E402
    budget_for,
    parse_budget,
)

CACHE = ROOT / ".cache"
OUT_JSON = CACHE / "budgets.json"
REVIEW_CSV = ROOT / "budget_mapping_review.csv"

SHEET_MIME = "application/vnd.google-apps.spreadsheet"
XLSX_MIME = ("application/vnd.openxmlformats-officedocument."
             "spreadsheetml.sheet")
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Words that identify nothing when comparing a sheet title to a jobcode name.
_STOP = {
    "project", "tools", "av", "tech", "net", "network", "system", "systems",
    "shades", "shade", "lighting", "update", "updates", "upgrade", "install",
    "installation", "the", "and", "svc", "service", "services", "remodel",
    "do", "not", "use", "phase", "main", "house", "residence", "restoration",
}

_JOB_NUM_IN_TITLE = re.compile(r"^\s*#?(\d{3,6})\s*[-_ ]")
_JOB_NUM_PREFIX = re.compile(
    r"^\s*\(?(?:DO NOT USE\)?\s*)?#?(\d{3,6})\s*[-,:]\s*", re.IGNORECASE)


def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def tokens(text: str) -> tuple:
    cleaned = re.sub(r"[^a-z0-9]+", " ", (text or "").lower())
    parts = cleaned.split()
    nums = {p for p in parts if p.isdigit()}
    words = {p for p in parts if not p.isdigit() and len(p) > 2
             and p not in _STOP}
    return nums, words


def drive_service():
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    raw = (os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or "").strip()
    if not raw:
        raise SystemExit(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not set. The service account also "
            "needs read access to the folder holding the Project Tools sheets."
        )
    info = json.loads(raw) if raw.startswith("{") else json.load(open(raw))
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def list_workbooks(service) -> list:
    """Every Project Tools spreadsheet the service account can see."""
    files, token = [], None
    while True:
        resp = service.files().list(
            q=(f"name contains 'Project Tools' and mimeType = '{SHEET_MIME}' "
               f"and trashed = false"),
            spaces="drive",
            fields="nextPageToken, files(id, name, modifiedTime)",
            pageSize=100, pageToken=token,
            # The workbooks live in the Clients shared drive, which the API
            # won't search without these.
            corpora="allDrives", supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        files.extend(resp.get("files", []))
        token = resp.get("nextPageToken")
        if not token:
            break
    return files


def grid_text(service, file_id: str) -> str:
    """
    Export the workbook as xlsx and flatten it to the pipe-delimited form the
    parser expects. Exporting as CSV would only give the first tab, and the
    budget block isn't always on it.
    """
    import openpyxl

    data = service.files().export_media(
        fileId=file_id, mimeType=XLSX_MIME).execute()  # export needs no drive flag
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    out = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            cells = ["" if v is None else str(v) for v in row]
            if any(c.strip() for c in cells):
                out.append("| " + " | ".join(cells) + " |")
    wb.close()
    return "\n".join(out)


def site_from_sheet(text: str) -> str:
    """The SITE ADDRESS value out of the header block, for sheets with no job number."""
    for line in text.split("\n")[:40]:
        if "SITE ADDRESS:" in line.upper():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            for i, c in enumerate(cells):
                if "SITE ADDRESS" in c.upper() and i + 1 < len(cells):
                    return cells[i + 1]
    return ""


def load_overrides() -> dict:
    """sheet_id -> jobcode_id, from the review CSV if it's been filled in."""
    if not REVIEW_CSV.exists():
        return {}
    out = {}
    with REVIEW_CSV.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            jid = (row.get("jobcode_id") or "").strip()
            key = (row.get("sheet_id") or "").strip()
            if jid and key:
                out[key] = jid
    return out


def match_jobcodes(title: str, site: str, jobcodes: list) -> list:
    """
    Candidate jobcodes for a workbook, best first.

    Job number in the title beats everything - it's the same identifier the
    jobcode carries as short_code. Otherwise fall back to the site address,
    anchored on a shared street number AND a shared word, because the number
    alone matches unrelated sites ("2700 Redwolf" vs "2700 Pierce").
    """
    m = _JOB_NUM_IN_TITLE.match(title or "")
    if m:
        num = m.group(1)
        hits = [j for j in jobcodes if (j.get("short_code") or "").strip() == num]
        if hits:
            return sorted(hits, key=lambda j: -j.get("total", 0))

    t_nums, t_words = tokens(site or title)
    if not t_nums or not t_words:
        return []
    hits = []
    for j in jobcodes:
        name = _JOB_NUM_PREFIX.sub("", j.get("name", ""))
        j_nums, j_words = tokens(name)
        if (t_nums & j_nums) and (t_words & j_words):
            hits.append(j)
    return sorted(hits, key=lambda j: -j.get("total", 0))


def slice_for_jobcode(parsed: dict, jobcode_name: str) -> dict:
    """
    Which rows of the sheet belong to this jobcode.

    A sheet often covers a whole site while jobcodes split it by vertical and
    phase - 901 Seabury holds Phase 1 contracts and a PHASE 2 row in one
    block, against jobcodes 5740 and 6159.
    """
    name = (jobcode_name or "").lower()
    m = re.search(r"\bphase\s*([0-9]+|[ivx]+)\b", name)
    phase = None
    if m:
        val = m.group(1).upper()
        phase = {"I": "1", "II": "2", "III": "3"}.get(val, val)

    if "shade" in name:
        return budget_for(parsed, shades=True)
    if phase:
        got = budget_for(parsed, phase=phase)
        if any(got.get(k) for k in ("rough", "trim", "final")):
            return got
    return budget_for(parsed, phase=None, shades=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="only process this many workbooks (for testing)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    snap_path = CACHE / "capacity_snapshot.json"
    if not snap_path.exists():
        raise SystemExit(
            f"{snap_path} not found - run build_snapshot.py first. Budgets "
            "are matched against the jobcodes it contains."
        )
    jobcodes = json.loads(snap_path.read_text(encoding="utf-8")) \
        .get("projects", {}).get("jobs", [])
    log(f"{len(jobcodes):,} jobcodes with hours, from the snapshot")

    service = drive_service()
    books = list_workbooks(service)
    log(f"{len(books)} Project Tools workbooks visible to the service account")
    if not books:
        raise SystemExit(
            "None found. The service account needs read access to the folder "
            "these live in - sharing it with the account's email address is "
            "what grants that."
        )
    if args.limit:
        books = books[:args.limit]

    overrides = load_overrides()
    if overrides:
        log(f"{len(overrides)} manual mappings loaded from "
            f"{REVIEW_CSV.name}")

    budgets, review = {}, []
    failed = 0
    for i, book in enumerate(books, start=1):
        try:
            text = grid_text(service, book["id"])
        except Exception as exc:  # noqa: BLE001
            log(f"  {i}/{len(books)}  {book['name'][:45]} - export failed: {exc}")
            failed += 1
            continue

        parsed = parse_budget(text)
        if not parsed:
            if args.verbose:
                log(f"  {i}/{len(books)}  {book['name'][:45]} - no budget block")
            review.append({"sheet_id": book["id"], "sheet_name": book["name"],
                           "site": "", "jobcode_id": "", "jobcode_name": "",
                           "status": "no budget block found"})
            continue

        site = site_from_sheet(text)
        forced = overrides.get(book["id"])
        if forced:
            hits = [j for j in jobcodes if str(j["jobcode_id"]) == str(forced)]
            how = "manual override"
        else:
            hits = match_jobcodes(book["name"], site, jobcodes)
            how = "job number" if _JOB_NUM_IN_TITLE.match(book["name"]) \
                else "site address"

        if not hits:
            review.append({"sheet_id": book["id"], "sheet_name": book["name"],
                           "site": site, "jobcode_id": "", "jobcode_name": "",
                           "status": "no jobcode matched"})
            if args.verbose:
                log(f"  {i}/{len(books)}  {book['name'][:45]} - no match "
                    f"(site {site!r})")
            continue

        for job in hits:
            sl = slice_for_jobcode(parsed, job.get("name", ""))
            if not any(sl.get(k) for k in ("rough", "trim", "final")):
                continue
            budgets[str(job["jobcode_id"])] = {
                "jobcode_name": job.get("name"),
                "sheet_id": book["id"],
                "sheet_name": book["name"],
                "site": site,
                "matched_by": how,
                "rough": sl["rough"],
                "trim": sl["trim"],
                "final": sl["final"],
                "contracts": sl.get("contracts", []),
                "all_columns": sl.get("total", {}),
            }
        review.append({
            "sheet_id": book["id"], "sheet_name": book["name"], "site": site,
            "jobcode_id": hits[0]["jobcode_id"],
            "jobcode_name": hits[0].get("name", ""),
            "status": f"matched by {how}"
                      + (f"; {len(hits)} candidates" if len(hits) > 1 else ""),
        })
        if args.verbose:
            log(f"  {i}/{len(books)}  {book['name'][:45]} -> "
                f"{hits[0].get('name','')[:40]}  ({how})")

    CACHE.mkdir(exist_ok=True)
    OUT_JSON.write_text(json.dumps({
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "workbooks_seen": len(books),
        "budgets": budgets,
    }, separators=(",", ":")), encoding="utf-8")

    with REVIEW_CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["sheet_id", "sheet_name", "site",
                                           "jobcode_id", "jobcode_name",
                                           "status"])
        w.writeheader()
        w.writerows(review)

    matched = sum(1 for r in review if r["status"].startswith("matched"))
    log(f"Budgets for {len(budgets)} jobcodes, from {matched}/{len(books)} "
        f"workbooks ({failed} export failures)")
    log(f"Wrote {OUT_JSON.relative_to(ROOT)} and {REVIEW_CSV.name}")
    log("Fix the jobcode_id column in the review file for anything wrong - "
        "it's read back on the next run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
