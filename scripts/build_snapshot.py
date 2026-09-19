"""
Build the nightly snapshot and write it to Google Drive.

    .\\.venv\\Scripts\\python.exe scripts\\build_snapshot.py
    .\\.venv\\Scripts\\python.exe scripts\\build_snapshot.py --months 24
    .\\.venv\\Scripts\\python.exe scripts\\build_snapshot.py --dry-run
    .\\.venv\\Scripts\\python.exe scripts\\build_snapshot.py --local-only

Why this exists
---------------
The dashboard used to call QuickBooks Time on page load. That works for one
person on a 4-week window and falls apart otherwise: a 12-month pull is
roughly 190 sequential API calls, every viewer triggers their own, they all
share one token, and Streamlit shows a spinner that looks identical whether
it's working or hung.

So the fetch moves here. This runs once a night (GitHub Actions), writes a
JSON snapshot to Drive, and the app just reads it. Everyone gets an instant
page and nobody touches the API.

Fetching is chunked by month rather than one giant request, so progress is
visible and a failure part-way through doesn't throw away what already came
back.
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.api.tsheets_client import tsheets_client, TSheetsAPIError  # noqa: E402
from app.services.labor_buckets import (  # noqa: E402
    OVERHEAD_BUCKETS,
    PROJECT_BUCKETS,
    UNAVAILABLE_BUCKETS,
    bucket_of,
    classify_person,
    detect_phase_field,
    hours,
    split_totals,
)

SNAPSHOT_NAME = "capacity_snapshot.json"
LOCAL_COPY = ROOT / ".cache" / SNAPSHOT_NAME

# Mirrors capacity_service.install_phase_of - the three phases a budget is
# written against. "Shading Install" is field work but is budgeted
# separately, so it is not part of rough/trim/final.
_PHASE_TO_INSTALL = {
    "prewire": "field_rough",
    "trim": "field_trim",
    "finish": "field_final",
}


def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def month_starts(start: dt.date, end: dt.date):
    """Yield (chunk_start, chunk_end) covering start..end, one per month."""
    cur = start
    while cur <= end:
        if cur.month == 12:
            nxt = dt.date(cur.year + 1, 1, 1)
        else:
            nxt = dt.date(cur.year, cur.month + 1, 1)
        yield cur, min(nxt - dt.timedelta(days=1), end)
        cur = nxt


def fetch_timesheets(start: dt.date, end: dt.date) -> list:
    """
    Pull month by month so progress is visible and a mid-way failure keeps
    what already arrived. One 24-month request would be ~380 pages with no
    feedback until it finished or died.
    """
    chunks = list(month_starts(start, end))
    out = []
    for i, (c_start, c_end) in enumerate(chunks, start=1):
        for attempt in (1, 2, 3):
            try:
                batch = tsheets_client.get_timesheets(str(c_start), str(c_end))
                out.extend(batch)
                log(f"  {i}/{len(chunks)}  {c_start:%Y-%m}  "
                    f"{len(batch):>6,} entries  (total {len(out):,})")
                break
            except TSheetsAPIError as exc:
                if attempt == 3:
                    raise
                log(f"  {c_start:%Y-%m} failed ({exc}) - retry {attempt}")
                time.sleep(5 * attempt)
    return out


def build_payload(months: int) -> dict:
    end = dt.date.today()
    start = end - dt.timedelta(days=round(months * 30.44))

    log(f"Window {start} to {end} ({months} months)")

    log("Users...")
    users = tsheets_client.get_users(active_only=False)
    log(f"  {len(users):,}")

    log("Jobcodes (type=all, so PTO and breaks are included)...")
    jobcodes = tsheets_client._get_all_pages(
        "jobcodes", "jobcodes", params={"type": "all"})
    log(f"  {len(jobcodes):,}")

    log("Timesheets...")
    timesheets = fetch_timesheets(start, end)
    log(f"  {len(timesheets):,} entries total")

    if not timesheets:
        raise SystemExit("No timesheet entries came back - refusing to write "
                         "an empty snapshot over a good one.")

    jc = {j["id"]: j for j in jobcodes}
    jc_type = {j["id"]: j.get("type", "regular") for j in jobcodes}
    name_of = {
        u["id"]: (f"{u.get('first_name','')} {u.get('last_name','')}".strip()
                  or f"(user {u['id']})")
        for u in users
    }
    phase_field = detect_phase_field(timesheets)
    log(f"Phase customfield: {phase_field}")

    # ---- people / capacity ---------------------------------------------
    log("Aggregating people...")
    by_user: dict = {}
    user_jobs: dict = {}
    for t in timesheets:
        uid = t.get("user_id")
        phase = (t.get("customfields") or {}).get(phase_field) or ""
        b = bucket_of(phase, jc_type.get(t.get("jobcode_id"), "regular"))
        by_user.setdefault(uid, {})
        by_user[uid][b] = by_user[uid].get(b, 0.0) + hours(t.get("duration"))
        if b in PROJECT_BUCKETS:
            user_jobs.setdefault(uid, set()).add(t.get("jobcode_id"))

    # Per-person, per-month buckets. The dashboard needs to answer "last 4
    # weeks" as well as "last 2 years", and a single 24-month total can't be
    # sliced. Monthly granularity keeps this at ~1,300 rows instead of the
    # ~26,000 daily would cost.
    by_user_month: dict = {}
    for t in timesheets:
        uid = t.get("user_id")
        day = t.get("date") or ""
        if len(day) < 7:
            continue
        ym = day[:7]
        phase = (t.get("customfields") or {}).get(phase_field) or ""
        b = bucket_of(phase, jc_type.get(t.get("jobcode_id"), "regular"))
        by_user_month.setdefault(str(uid), {}).setdefault(ym, {})
        by_user_month[str(uid)][ym][b] = (
            by_user_month[str(uid)][ym].get(b, 0.0) + hours(t.get("duration")))
    for uid in by_user_month:
        for ym in by_user_month[uid]:
            by_user_month[uid][ym] = {
                k: round(v, 2) for k, v in by_user_month[uid][ym].items()}

    people, office = [], []
    for uid, mix in by_user.items():
        totals = split_totals(mix)
        if totals["worked"] < 1:
            continue
        role, confidence = classify_person(mix)
        rec = {
            "user_id": uid,
            "name": name_of.get(uid, f"(user {uid})"),
            "role": role,
            "confidence": confidence,
            "jobs": len(user_jobs.get(uid, ())),
            "buckets": {k: round(v, 2) for k, v in mix.items()},
            **{k: round(v, 1) for k, v in totals.items()},
        }
        (office if role == "office" else people).append(rec)

    office_ids = {r["user_id"] for r in office}
    buckets: dict = {}
    for uid, mix in by_user.items():
        if uid in office_ids:
            continue
        for b, h in mix.items():
            buckets[b] = buckets.get(b, 0.0) + h

    people.sort(key=lambda r: (r["role"], -r["worked"]))
    office.sort(key=lambda r: -r["worked"])

    # ---- projects -------------------------------------------------------
    log("Aggregating projects...")
    daily: dict = {}
    totals_by_job: dict = {}
    job_people: dict = {}

    for t in timesheets:
        jid = t.get("jobcode_id")
        if jc_type.get(jid) in ("pto", "unpaid_time_off", "unpaid_break"):
            continue
        h = hours(t.get("duration"))
        if h <= 0:
            continue
        phase = ((t.get("customfields") or {}).get(phase_field) or "").strip().lower()
        slot = _PHASE_TO_INSTALL.get(phase, "other")
        day = t.get("date")

        daily.setdefault(jid, {}).setdefault(day, {})
        daily[jid][day][slot] = daily[jid][day].get(slot, 0.0) + h
        totals_by_job.setdefault(jid, {})
        totals_by_job[jid][slot] = totals_by_job[jid].get(slot, 0.0) + h
        job_people.setdefault(jid, set()).add(name_of.get(t.get("user_id"), "?"))

    jobs = []
    for jid, mix in totals_by_job.items():
        job = jc.get(jid) or {}
        days_seen = sorted(daily.get(jid, {}))
        jobs.append({
            "jobcode_id": jid,
            "name": job.get("name", f"(jobcode {jid})"),
            "short_code": (job.get("short_code") or "").strip(),
            "active": bool(job.get("active")),
            "rough": round(mix.get("field_rough", 0.0), 2),
            "trim": round(mix.get("field_trim", 0.0), 2),
            "final": round(mix.get("field_final", 0.0), 2),
            "other": round(mix.get("other", 0.0), 2),
            "total": round(sum(mix.values()), 2),
            "first_day": days_seen[0] if days_seen else None,
            "last_day": days_seen[-1] if days_seen else None,
            "people": sorted(job_people.get(jid, ())),
        })
    jobs.sort(key=lambda r: -r["total"])

    daily_out = {}
    for jid, by_day in daily.items():
        daily_out[str(jid)] = [
            {
                "date": day,
                "rough": round(by_day[day].get("field_rough", 0.0), 2),
                "trim": round(by_day[day].get("field_trim", 0.0), 2),
                "final": round(by_day[day].get("field_final", 0.0), 2),
                "other": round(by_day[day].get("other", 0.0), 2),
                "total": round(sum(by_day[day].values()), 2),
            }
            for day in sorted(by_day)
        ]

    payload = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "window": {"start": str(start), "end": str(end), "months": months},
        "source": "quickbooks_time",
        "phase_field": phase_field,
        "entry_count": len(timesheets),
        "capacity": {
            "buckets": {k: round(v, 2) for k, v in buckets.items()},
            "totals": {k: round(v, 2) for k, v in split_totals(buckets).items()},
            "people": people,
            "office": office,
            "monthly": by_user_month,
            "names": {str(uid): nm for uid, nm in name_of.items()},
        },
        "projects": {"jobs": jobs, "daily": daily_out},
    }

    log(f"Built: {len(people)} capacity staff, {len(office)} office, "
        f"{len(jobs):,} jobcodes with hours")
    return payload


# ---------------------------------------------------------------------------
# Drive
# ---------------------------------------------------------------------------

def write_to_drive(payload: dict) -> str:
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseUpload

    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    folder_id = os.environ.get("DRIVE_FOLDER_ID", "").strip()
    if not raw or not folder_id:
        raise SystemExit(
            "GOOGLE_SERVICE_ACCOUNT_JSON and DRIVE_FOLDER_ID must both be set. "
            "See SETUP_DRIVE_EXPORT.md. Use --local-only to skip Drive."
        )
    info = json.loads(raw) if raw.startswith("{") else json.load(open(raw))

    creds = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive"])
    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    log(f"Snapshot is {len(body)/1_000_000:.1f} MB")
    media = MediaIoBaseUpload(io.BytesIO(body), mimetype="application/json",
                              resumable=False)

    # Shared drives are a separate corpus in the Drive API. Without these
    # flags every call is scoped to My Drive, and a shared-drive folder comes
    # back as "File not found" even when the account can plainly see it.
    shared_drive_args = {
        "supportsAllDrives": True,
        "includeItemsFromAllDrives": True,
    }

    q = (f"name = '{SNAPSHOT_NAME}' and '{folder_id}' in parents "
         f"and trashed = false")
    found = service.files().list(
        q=q, spaces="drive", fields="files(id)", pageSize=1,
        corpora="allDrives", **shared_drive_args).execute().get("files", [])

    if found:
        fid = service.files().update(
            fileId=found[0]["id"], media_body=media, fields="id",
            supportsAllDrives=True).execute()["id"]
        log("Updated the existing snapshot in place (file id unchanged)")
    else:
        fid = service.files().create(
            body={"name": SNAPSHOT_NAME, "parents": [folder_id]},
            media_body=media, fields="id",
            supportsAllDrives=True).execute()["id"]
        log("Created a new snapshot file")
    return fid


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=24,
                    help="how much history to pull (default 24)")
    ap.add_argument("--dry-run", action="store_true",
                    help="build it and report, write nothing")
    ap.add_argument("--local-only", action="store_true",
                    help="write .cache/ only, skip Drive")
    ap.add_argument("--skip-fetch", action="store_true",
                    help="upload the existing .cache snapshot without "
                         "re-pulling from QuickBooks Time")
    args = ap.parse_args()

    started = time.time()

    if args.skip_fetch:
        if not LOCAL_COPY.exists():
            raise SystemExit(f"--skip-fetch needs {LOCAL_COPY} to exist.")
        log(f"Reusing {LOCAL_COPY.name} - no QuickBooks Time fetch")
        payload = json.loads(LOCAL_COPY.read_text(encoding="utf-8"))
    else:
        try:
            payload = build_payload(args.months)
        except TSheetsAPIError as exc:
            log(f"QuickBooks Time refused the request: {exc}")
            return 1

    if not args.skip_fetch:
        LOCAL_COPY.parent.mkdir(exist_ok=True)
        LOCAL_COPY.write_text(json.dumps(payload, separators=(",", ":")),
                              encoding="utf-8")
        log(f"Wrote {LOCAL_COPY.relative_to(ROOT)} "
            f"({LOCAL_COPY.stat().st_size/1_000_000:.1f} MB)")

    if args.dry_run:
        log("Dry run - nothing sent to Drive.")
    elif args.local_only:
        log("Local only - nothing sent to Drive.")
    else:
        fid = write_to_drive(payload)
        log(f"Drive file id {fid}")

    log(f"Done in {time.time()-started:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
