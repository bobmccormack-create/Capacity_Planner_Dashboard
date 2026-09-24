"""
How much of the work people actually do was on the calendar?

    .\\.venv\\Scripts\\python.exe scripts\\schedule_coverage.py
    .\\.venv\\Scripts\\python.exe scripts\\schedule_coverage.py --weeks 12

A forward capacity view reads the Zoho calendar and reports who has free
time. That's only trustworthy if the calendar holds most of the work. If a
tech's service calls are booked somewhere else, they'll look 40% free while
actually being flat out - and the dashboard will tell you to overbook them.

This compares, for recent weeks, hours scheduled on the calendar against
hours logged in QuickBooks Time, per person. A coverage ratio near 100%
means the calendar can be trusted for that person; well below means it
can't, or needs a correction factor.

Two traps it deliberately avoids:

  Calendar reach. Zoho's Events API sorts by last-modified, not by date,
  and the client caps the pull. Events from weeks ago that nobody's edited
  since may simply not come back, which would undercount scheduled hours
  and make coverage look worse than it is. So the comparison is restricted
  to days the calendar pull actually reaches, and the script says how far
  back that is.

  Time off. "OFF", "APPROVED PTO", "Labor Day" and the like are calendar
  events but not work. They're counted separately rather than as scheduled
  hours - otherwise a week of vacation reads as a fully booked week.

The calendar view's tech exclusion list is NOT applied here. Finding the
people who never appear on the schedule is part of the point.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.api.tsheets_client import tsheets_client, TSheetsAPIError  # noqa: E402
from app.api.zoho_projects import zoho_client, ZohoAPIError  # noqa: E402

SECONDS_PER_HOUR = 3600.0
WORKDAY_HOURS = 8.0      # 7:00-3:30 with an unpaid 30-minute lunch
LUNCH_HOURS = 0.5
MAX_DAY_HOURS = 11.0     # a scheduled "day" longer than this is a data error

# Titles that mean someone is unavailable, not working. Matched as words.
TIME_OFF = re.compile(
    r"\b(off|pto|vacation|holiday|sick|unavailable|out of office|ooo|"
    r"labor day|memorial day|thanksgiving|christmas|new year|july 4|"
    r"approved)\b", re.IGNORECASE)


def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def parse_dt(value: str):
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def tech_of(event: dict) -> str:
    owner = event.get("Owner")
    if isinstance(owner, dict):
        return owner.get("name") or owner.get("email") or "Unassigned"
    return "Unassigned"


def participants_of(event: dict) -> list:
    """Names in the event's Participants list - the crew under the lead."""
    out = []
    for p in event.get("Participants") or []:
        if isinstance(p, dict):
            name = p.get("name") or p.get("Full_Name") or p.get("email")
            if name:
                out.append(name)
    return out


def crew_of(event: dict, include_participants: bool) -> list:
    """
    Everyone the event books.

    Each job is scheduled as a lead tech (the Owner) plus participants. The
    calendar view only reads Owner, which credits the lead with the whole
    crew's day and gives the crew nothing - leads showed 110-130% coverage
    while their crews showed under 10%. Counting participants too gives each
    person the hours they were actually booked for.
    """
    people = [tech_of(event)]
    if include_participants:
        people += participants_of(event)
    # De-dup, keeping order - the lead is sometimes listed as a participant.
    seen, out = set(), []
    for n in people:
        k = n.strip().lower()
        if n and n != "Unassigned" and k not in seen:
            seen.add(k)
            out.append(n)
    return out


def name_key(name: str) -> frozenset:
    """
    Names are spelled differently between systems: "Jorge Flores Jr." in
    Zoho, "Jorge Jr Flores" in QuickBooks Time; "Brad Diemont" against
    "Brad Waller Diemont". Compare as sets of words instead.
    """
    words = re.sub(r"[^a-z ]", " ", (name or "").lower()).split()
    return frozenset(w for w in words if len(w) > 1)


_GENERATION = {"jr", "sr", "ii", "iii", "iv"}


def names_match(a: frozenset, b: frozenset, raw_a: str = "",
                raw_b: str = "") -> bool:
    if not a or not b:
        return False
    # Jorge Flores Sr. and Jorge Jr Flores share two of three words and are
    # father and son. If both names carry a generational suffix and they
    # differ, they are different people - checked before anything looser.
    ga, gb = a & _GENERATION, b & _GENERATION
    if ga and gb and ga != gb:
        return False
    if a <= b or b <= a:
        return True
    if len(a & b) >= 2:
        return True
    # Nicknames: "Tim Fay" against "Timothy Fay". Same surname, and one
    # first name a prefix of the other. Deliberately not a looser rule -
    # "Blair Stotler" and "Blake Stotler" share a surname and a three-letter
    # prefix and are two different people.
    wa = re.sub(r"[^a-z ]", " ", raw_a.lower()).split()
    wb = re.sub(r"[^a-z ]", " ", raw_b.lower()).split()
    if len(wa) >= 2 and len(wb) >= 2 and wa[-1] == wb[-1]:
        fa, fb = wa[0], wb[0]
        short, long_ = sorted((fa, fb), key=len)
        if len(short) >= 3 and long_.startswith(short):
            return True
        # A single typo: "Johnathon" in one system, "Johnathan" in the
        # other. Blair/Blake differ by two letters, so they stay apart.
        if min(len(fa), len(fb)) >= 4 and _edit_distance(fa, fb) <= 1:
            return True
    return False


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def weekdays(start: dt.date, end: dt.date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += dt.timedelta(days=1)


def scheduled_hours(event: dict, start, end) -> dict:
    """{date: hours} for one calendar event."""
    s_date, e_date = start.date(), (end or start).date()

    # All-day, or spanning several days: a full workday on each weekday.
    # A 3-day install doesn't carry per-day times worth trusting.
    if event.get("All_day") or e_date > s_date:
        return {d: WORKDAY_HOURS for d in weekdays(s_date, e_date)}

    if not end:
        return {}
    hours = (end - start).total_seconds() / SECONDS_PER_HOUR
    if hours <= 0 or hours > MAX_DAY_HOURS:
        return {}
    # A full-day booking (7:00-3:30) includes lunch; logged time doesn't.
    if hours >= 6:
        hours -= LUNCH_HOURS
    return {s_date: hours}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=8,
                    help="how many recent weeks to compare (default 8)")
    ap.add_argument("--owner-only", action="store_true",
                    help="credit only the event Owner, the way the calendar "
                         "view does. Default credits participants too.")
    ap.add_argument("--max-pages", type=int, default=10,
                    help="calendar pages to pull, 200 events each. Zoho "
                         "caps this at 10 without a page token (default 10)")
    args = ap.parse_args()

    today = dt.date.today()
    # Compare up to yesterday - today's timesheets aren't finished yet.
    window_end = today - dt.timedelta(days=1)
    window_start = window_end - dt.timedelta(weeks=args.weeks) + dt.timedelta(days=1)

    # ---- calendar -------------------------------------------------------
    log(f"Calendar events (up to {args.max_pages * 200:,})...")
    try:
        raw = zoho_client.get_calendar_events(max_pages=args.max_pages)
    except ZohoAPIError as exc:
        log(f"Zoho refused: {exc}")
        return 1
    log(f"  {len(raw):,} events returned")

    sched = defaultdict(float)        # (tech, date) -> hours
    off_days = defaultdict(set)       # tech -> {dates}
    dated = []
    for ev in raw:
        start = parse_dt(ev.get("Start_DateTime"))
        if start is None:
            continue
        end = parse_dt(ev.get("End_DateTime")) or start
        dated.append(start.date())
        crew = crew_of(ev, not args.owner_only)
        title = ev.get("Event_Title") or ""

        if TIME_OFF.search(title):
            for person in crew:
                for d in weekdays(start.date(), end.date()):
                    off_days[person].add(d)
            continue

        for d, h in scheduled_hours(ev, start, end).items():
            for person in crew:
                sched[(person, d)] += h

    if not dated:
        log("No dated events came back.")
        return 1

    earliest = min(dated)
    log(f"  events span {earliest} to {max(dated)}")

    # Only compare days the calendar pull actually reaches.
    effective_start = max(window_start, earliest)
    if effective_start > window_start:
        log(f"  NOTE: calendar only reaches back to {earliest}. Comparing "
            f"{effective_start} to {window_end} instead of the full "
            f"{args.weeks} weeks - try a larger --max-pages to go further.")
    days = [d for d in weekdays(effective_start, window_end)]
    if not days:
        log("The calendar doesn't reach into the comparison window at all.")
        return 1

    # ---- timesheets -----------------------------------------------------
    log(f"Timesheets {effective_start} to {window_end}...")
    try:
        users = tsheets_client.get_users(active_only=False)
        jobcodes = tsheets_client._get_all_pages(
            "jobcodes", "jobcodes", params={"type": "all"})
        sheets = tsheets_client.get_timesheets(str(effective_start),
                                               str(window_end))
    except TSheetsAPIError as exc:
        log(f"QuickBooks Time refused: {exc}")
        return 1
    log(f"  {len(sheets):,} entries")

    not_work = {j["id"] for j in jobcodes
                if j.get("type") in ("pto", "unpaid_time_off", "unpaid_break")}
    user_name = {u["id"]: f"{u.get('first_name','')} {u.get('last_name','')}".strip()
                 for u in users}

    logged = defaultdict(float)       # (name, date) -> hours
    for t in sheets:
        if t.get("jobcode_id") in not_work:
            continue
        d = dt.date.fromisoformat(t["date"])
        if d not in days:
            continue
        nm = user_name.get(t.get("user_id"), "")
        if nm:
            logged[(nm, d)] += (t.get("duration") or 0) / SECONDS_PER_HOUR

    # ---- match people across the two systems ----------------------------
    cal_people = {tech for (tech, _) in sched} | set(off_days)
    cal_people.discard("Unassigned")
    qbt_people = {nm for (nm, _) in logged}

    pairs, unmatched_cal = {}, []
    for tech in sorted(cal_people):
        k = name_key(tech)
        hit = next((q for q in qbt_people
                    if names_match(k, name_key(q), tech, q)), None)
        if hit:
            pairs[hit] = tech
        else:
            unmatched_cal.append(tech)

    # ---- report ---------------------------------------------------------
    rows = []
    for person in sorted(qbt_people):
        tech = pairs.get(person)
        lg = sum(logged[(person, d)] for d in days)
        sc = sum(sched[(tech, d)] for d in days) if tech else 0.0
        off = len([d for d in days if tech and d in off_days[tech]])
        if lg < 1 and sc < 1:
            continue
        rows.append((person, tech, sc, lg, off))

    rows.sort(key=lambda r: -r[3])

    print()
    print(f"Comparing {len(days)} weekdays, {days[0]} to {days[-1]}")
    print(f"Crediting: {'event owner only' if args.owner_only else 'owner and participants'}")
    print()
    print(f"  {'person':<24}{'scheduled':>11}{'logged':>9}{'coverage':>10}"
          f"{'off days':>10}  note")
    print("  " + "-" * 82)

    total_s = total_l = 0.0
    trusted = weak = absent = 0
    for person, tech, sc, lg, off in rows:
        cov = (sc / lg * 100) if lg else 0.0
        if not tech:
            note = "never on the calendar"
            absent += 1
        elif cov >= 80:
            note = ""
            trusted += 1
        elif cov >= 50:
            note = "partly scheduled"
            weak += 1
        else:
            note = "mostly unscheduled"
            weak += 1
        if cov > 130:
            note = "booked more than logged"
        total_s += sc
        total_l += lg
        cov_s = f"{cov:>8.0f}%" if lg else "      -  "
        print(f"  {person[:24]:<24}{sc:>10.1f}h{lg:>8.1f}h{cov_s}"
              f"{off:>10}  {note}")

    print("  " + "-" * 82)
    overall = (total_s / total_l * 100) if total_l else 0
    print(f"  {'ALL':<24}{total_s:>10.1f}h{total_l:>8.1f}h{overall:>8.0f}%")

    print()
    print(f"  {trusted} people the calendar can be trusted for (80%+)")
    print(f"  {weak} partly or mostly unscheduled")
    print(f"  {absent} who log time but never appear on the calendar")

    if unmatched_cal:
        print()
        print("  On the calendar but no matching QuickBooks Time name:")
        for t in unmatched_cal:
            print(f"    {t}")
        print("  (a spelling difference the matcher couldn't resolve, or an "
              "office user who schedules but doesn't clock in)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
