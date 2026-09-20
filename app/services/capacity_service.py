"""
CapacityService - shapes the nightly snapshot into what the dashboard renders.

This used to call QuickBooks Time on page load. It doesn't any more, and
that's the main thing to know about it. A 24-month pull is 68,000 entries
over ~25 chunked requests and takes about thirteen minutes; with ten people
on the dashboard that was ten separate pulls through one shared token, each
showing a spinner indistinguishable from a hang.

Now scripts/build_snapshot.py does that once a night and writes a 1.9 MB
JSON file to Drive. Everything here is arithmetic over that file - the whole
aggregation takes well under a second.

Windows: the snapshot stores per-person hours by month and per-job hours by
day, so any window shorter than the snapshot's own is computed here rather
than re-fetched. Monthly is the finest grain for people (a 24-month daily
breakdown would cost ~26,000 rows for no real gain); daily is kept for jobs,
where "what went in on Tuesday" is a question people actually ask.
"""
from __future__ import annotations

import datetime as dt
import re

import streamlit as st

from app.services.labor_buckets import (
    PROJECT_BUCKETS,
    classify_person,
    split_totals,
)
from app.services.snapshot_reader import load_snapshot, snapshot_age
from app.utils.logger import get_logger

logger = get_logger(__name__)

_JOB_NUM_PREFIX = re.compile(
    r"^\s*\(?(?:DO NOT USE\)?\s*)?#?(\d{3,6})\s*[-,:]\s*", re.IGNORECASE)

# Jobcodes that aren't projects. These dwarf real jobs - Admin alone carries
# ~8,000 hours, more than any site - so they'd otherwise sit at the top of
# every list and default every picker to something useless.
_NON_PROJECT_NAMES = {"admin", "drive", "travel", "shop", "warehouse",
                      "training", "holiday", "vacation", "sick",
                      "lunch break", "pto"}


def job_number_of(jobcode_name: str) -> str | None:
    """
    The leading job number, when punctuation marks it as one -
    "5216 - 28 Sky Terrace_Shade System" -> "5216".

    A bare leading number is NOT one: "3114 Blackhawk Meadow" is a street
    address, and reading it as a job code matched it to an unrelated
    jobcode that happened to share the digits.
    """
    m = _JOB_NUM_PREFIX.match(jobcode_name or "")
    return m.group(1) if m else None


def is_project_job(name: str) -> bool:
    """A real job, as opposed to Admin, Drive, Holiday and similar."""
    n = (name or "").strip().lower()
    return bool(n) and n not in _NON_PROJECT_NAMES


def _months_back(months: int) -> list:
    """The YYYY-MM keys covering the last `months` months, inclusive."""
    today = dt.date.today()
    keys, y, m = [], today.year, today.month
    for _ in range(months):
        keys.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return keys


class CapacityService:
    # Kept as months rather than days: the snapshot's people data is monthly.
    WINDOWS = {
        "This month": 1,
        "Last 3 months": 3,
        "Last 6 months": 6,
        "Last 12 months": 12,
        "Everything": 0,        # 0 means the snapshot's whole window
    }

    def snapshot(self) -> dict:
        return load_snapshot()

    def status(self) -> dict:
        """What the page shows above the numbers: where this came from, how old."""
        snap = load_snapshot()
        if snap.get("error"):
            return {"ok": False, "error": snap["error"], "age": None,
                    "source": None, "window": None}
        return {
            "ok": True,
            "error": None,
            "age": snapshot_age(snap),
            "source": snap.get("_source", "?"),
            "window": snap.get("window", {}),
            "entries": snap.get("entry_count"),
            "generated_at": snap.get("generated_at_utc"),
        }

    def get_capacity(self, months: int = 3) -> dict:
        """
        Buckets, totals and per-person rows for the last `months` months.

        months=0 uses the snapshot's stored totals for its whole window,
        which avoids re-summing 25 months of monthly rows for no reason.
        """
        snap = load_snapshot()
        if snap.get("error"):
            return {"error": snap["error"], "buckets": {},
                    "totals": split_totals({}), "people": [], "office": []}

        cap = snap.get("capacity", {})
        if not months:
            return {
                "buckets": cap.get("buckets", {}),
                "totals": cap.get("totals", split_totals({})),
                "people": cap.get("people", []),
                "office": cap.get("office", []),
                "error": None,
            }

        monthly = cap.get("monthly", {})
        if not monthly:
            # Older snapshot without monthly detail - fall back rather than
            # showing an empty page, and say so.
            return {
                "buckets": cap.get("buckets", {}),
                "totals": cap.get("totals", split_totals({})),
                "people": cap.get("people", []),
                "office": cap.get("office", []),
                "error": None,
                "note": "This snapshot predates monthly detail, so the "
                        "window selector has no effect. Rebuild it to fix.",
            }

        names = cap.get("names", {})
        keys = set(_months_back(months))

        people, office = [], []
        for uid, by_month in monthly.items():
            mix: dict = {}
            for ym, buckets in by_month.items():
                if ym not in keys:
                    continue
                for b, h in buckets.items():
                    mix[b] = mix.get(b, 0.0) + h
            if not mix:
                continue
            totals = split_totals(mix)
            if totals["worked"] < 1:
                continue
            role, confidence = classify_person(mix)
            rec = {
                "user_id": uid,
                "name": names.get(str(uid), f"(user {uid})"),
                "role": role,
                "confidence": confidence,
                "buckets": {k: round(v, 2) for k, v in mix.items()},
                "jobs": 0,   # not derivable from monthly rollups
                **{k: round(v, 1) for k, v in totals.items()},
            }
            (office if role == "office" else people).append(rec)

        office_ids = {r["user_id"] for r in office}
        buckets: dict = {}
        for uid, by_month in monthly.items():
            if uid in office_ids:
                continue
            for ym, bk in by_month.items():
                if ym not in keys:
                    continue
                for b, h in bk.items():
                    buckets[b] = buckets.get(b, 0.0) + h

        people.sort(key=lambda r: (r["role"], -r["worked"]))
        office.sort(key=lambda r: -r["worked"])

        return {
            "buckets": {k: round(v, 2) for k, v in buckets.items()},
            "totals": split_totals(buckets),
            "people": people,
            "office": office,
            "error": None,
        }

    def get_project_hours(self, months: int = 3,
                          projects_only: bool = True) -> dict:
        """
        Per-job totals and daily rows, filtered to the last `months` months.

        Totals are recomputed from the daily rows rather than read from the
        snapshot's stored totals, so they match whatever window is showing.
        Getting that wrong is how a job reads 104 hours in the header and
        57 in the table underneath.
        """
        snap = load_snapshot()
        if snap.get("error"):
            return {"error": snap["error"], "jobs": [], "daily": {},
                    "names": {}, "window": None}

        proj = snap.get("projects", {})
        all_jobs = proj.get("jobs", [])
        all_daily = proj.get("daily", {})

        if months:
            cutoff = str(dt.date.today() - dt.timedelta(days=round(months * 30.44)))
        else:
            cutoff = ""

        jobs, daily = [], {}
        for job in all_jobs:
            if projects_only and not is_project_job(job.get("name", "")):
                continue
            rows = [r for r in all_daily.get(str(job["jobcode_id"]), [])
                    if r["date"] >= cutoff]
            if not rows:
                continue
            # Rows written by older snapshot builds omit a phase key entirely
            # when that phase has no hours, rather than storing a zero. Reading
            # them with [] raises KeyError on the first such row, so every
            # phase is read defensively and a missing/None value counts as 0.
            def _phase(name: str) -> float:
                return sum((r.get(name) or 0) for r in rows)

            rough = _phase("rough")
            trim = _phase("trim")
            final = _phase("final")
            other = _phase("other")
            jobs.append({
                **job,
                "job_number": (job.get("short_code") or "").strip()
                              or job_number_of(job.get("name", "")) or "",
                "rough": round(rough, 2),
                "trim": round(trim, 2),
                "final": round(final, 2),
                "other": round(other, 2),
                "total": round(rough + trim + final + other, 2),
                "install_total": round(rough + trim + final, 2),
                # first/last day WITHIN the window - not the job's start date,
                # which the snapshot's own first_day also isn't once a window
                # is applied.
                "first_day": rows[0]["date"],
                "last_day": rows[-1]["date"],
            })
            daily[job["jobcode_id"]] = rows

        # Busiest installs first, so the picker defaults to a real job rather
        # than whatever has the most admin time against it.
        jobs.sort(key=lambda r: (-r["install_total"], -r["total"]))
        return {"jobs": jobs, "daily": daily, "error": None,
                # Daily rows carry "who" keyed by user id (schema 2+); the
                # name map lives under capacity, so pass it through rather
                # than making every caller dig for it.
                "names": snap.get("capacity", {}).get("names", {}),
                "window": snap.get("window")}

    @staticmethod
    def clear_cache() -> None:
        from app.services import snapshot_reader
        snapshot_reader.clear()

    # Kept so existing callers don't break.
    clear_project_cache = clear_cache
