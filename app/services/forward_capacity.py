"""
Forward capacity - who's booked, who's free, and who's double-booked, over
the next several weeks.

Everything else in the app looks backward at where hours went. This looks
forward, using the Zoho calendar as the plan.

It's only trustworthy because of a check run first (scripts/
schedule_coverage.py): counting both the lead tech and the participants on
each event, the calendar held 95-115% of what field techs actually logged
over the previous eight weeks. Counting the lead alone, crews showed under
10% - the calendar view had been crediting the whole crew's day to the lead.
So this module always counts participants.

Three rules, each learned from that check:

  Lead and crew. Every event books its Owner and its Participants. Only
  participants of type "user" count - Zoho lets clients and subcontractors
  be added as participants too, and they'd otherwise appear as phantom
  staff with booked hours.

  Time off reduces capacity, it isn't booked work. An "OFF" or "APPROVED
  PTO" event lowers what that person has available rather than adding to
  what they're booked for, or a week of vacation would read as fully busy.

  Overlaps are conflicts, not overtime. Someone on two jobs the same day
  isn't booked for 16 hours; they're double-booked. Booked hours are
  capped at a workday and the overlap is reported separately, because a
  double-booking you can see ahead of time is one you can fix.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict

import streamlit as st

from app.api.zoho_projects import zoho_client, ZohoAPIError
from app.utils.logger import get_logger

logger = get_logger(__name__)

WORKDAY_HOURS = 8.0       # 7:00-3:30 with an unpaid 30-minute lunch
LUNCH_HOURS = 0.5
MAX_EVENT_DAY = 11.0      # a single-day event longer than this is bad data
CONFLICT_OVER = 9.0       # booked beyond this in a day = double-booked
CACHE_TTL_SECONDS = 1800

TIME_OFF = re.compile(
    r"\b(off|pto|vacation|holiday|sick|unavailable|out of office|ooo|"
    r"labor day|memorial day|thanksgiving|christmas|new year|july 4|"
    r"approved)\b", re.IGNORECASE)

# Participant types that are staff. Anything else - a contact, a lead - is
# a client or a sub who shouldn't show up as someone with capacity.
_STAFF_TYPES = {"user", "users"}


def _parse(value):
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def _owner(event: dict) -> str:
    o = event.get("Owner")
    if isinstance(o, dict):
        return (o.get("name") or o.get("email") or "").strip()
    return ""


def _staff_participants(event: dict) -> list:
    out = []
    for p in event.get("Participants") or []:
        if not isinstance(p, dict):
            continue
        ptype = (p.get("type") or "").strip().lower()
        # If Zoho didn't say, include rather than silently drop crew. The
        # type is the reliable signal when present.
        if ptype and ptype not in _STAFF_TYPES:
            continue
        name = (p.get("name") or p.get("Full_Name") or "").strip()
        if name:
            out.append(name)
    return out


def crew(event: dict) -> list:
    """The lead plus staff participants, de-duplicated, lead first."""
    seen, out = set(), []
    for n in [_owner(event)] + _staff_participants(event):
        k = n.lower()
        if n and k not in seen:
            seen.add(k)
            out.append(n)
    return out


def _weekdays(start: dt.date, end: dt.date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += dt.timedelta(days=1)


def _event_hours(event: dict, start, end) -> dict:
    """{date: hours} this event books, per weekday it covers."""
    s, e = start.date(), (end or start).date()
    if event.get("All_day") or e > s:
        return {d: WORKDAY_HOURS for d in _weekdays(s, e)}
    if not end:
        return {}
    h = (end - start).total_seconds() / 3600
    if h <= 0 or h > MAX_EVENT_DAY:
        return {}
    if h >= 6:
        h -= LUNCH_HOURS
    return {s: h} if s.weekday() < 5 else {}


def _monday(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Reading the calendar...")
def _fetch_events() -> dict:
    try:
        # 10 pages is Zoho's ceiling without a page token (2,000 events).
        # Upcoming work is recently edited, so this reaches it comfortably.
        return {"events": zoho_client.get_calendar_events(max_pages=10),
                "error": None}
    except ZohoAPIError as exc:
        logger.warning("Forward capacity calendar fetch failed: %s", exc)
        return {"events": [], "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Forward capacity calendar unavailable: %s", exc)
        return {"events": [], "error": str(exc)}


def forward_capacity(weeks: int = 8, exclude: set | None = None) -> dict:
    """
    Returns:
      {
        "weeks": [monday, ...],
        "people": [ {name, weeks: {monday: {booked, available, pct,
                     conflict_days}}, free_next_2w, booked_total,
                     available_total} ],
        "conflicts": [ {person, date, hours, jobs: [titles]} ],
        "error": str | None,
      }

    exclude: lower-cased names to leave out entirely - office staff who
    appear on the calendar but aren't field capacity.
    """
    exclude = {n.lower() for n in (exclude or set())}
    fetched = _fetch_events()
    if fetched["error"] and not fetched["events"]:
        return {"weeks": [], "people": [], "conflicts": [],
                "error": fetched["error"]}

    today = dt.date.today()
    start = _monday(today)
    end = start + dt.timedelta(weeks=weeks) - dt.timedelta(days=1)
    mondays = [start + dt.timedelta(weeks=i) for i in range(weeks)]

    booked = defaultdict(float)             # (person, date) -> hours
    jobs_on = defaultdict(list)             # (person, date) -> [titles]
    off = defaultdict(set)                  # person -> {dates}
    people = set()

    for ev in fetched["events"]:
        s = _parse(ev.get("Start_DateTime"))
        if s is None:
            continue
        e = _parse(ev.get("End_DateTime")) or s
        if e.date() < start or s.date() > end:
            continue
        who = [p for p in crew(ev) if p.lower() not in exclude]
        if not who:
            continue
        title = ev.get("Event_Title") or "(untitled)"
        lead = _owner(ev)
        if ev.get("All_day") or e.date() > s.date():
            when = "All day"
        else:
            # %-I drops the leading zero on Linux but raises on Windows, so
            # strip it by hand to run the same in both places.
            when = f"{s:%I:%M %p}".lstrip("0") + " - " + f"{e:%I:%M %p}".lstrip("0")

        if TIME_OFF.search(title):
            for p in who:
                people.add(p)
                for d in _weekdays(max(s.date(), start), min(e.date(), end)):
                    off[p].add(d)
            continue

        for d, h in _event_hours(ev, s, e).items():
            if not (start <= d <= end):
                continue
            for p in who:
                people.add(p)
                booked[(p, d)] += h
                jobs_on[(p, d)].append({
                    "title": title, "hours": round(h, 1), "when": when,
                    "role": "Lead" if p.lower() == lead.lower() else "Crew",
                })

    rows, conflicts = [], []
    two_weeks = start + dt.timedelta(weeks=2)
    for p in sorted(people):
        by_week = {}
        days = {}
        free_2w = 0.0
        b_total = a_total = 0.0
        for m in mondays:
            wk_booked = wk_avail = 0.0
            conflict_days = 0
            for d in _weekdays(m, m + dt.timedelta(days=4)):
                if d in off[p]:
                    days[d] = {"off": True, "booked": 0.0, "jobs": []}
                    continue                    # time off: no capacity at all
                avail = WORKDAY_HOURS
                raw = booked.get((p, d), 0.0)
                days[d] = {"off": False, "booked": round(raw, 1),
                           "jobs": jobs_on.get((p, d), []),
                           "conflict": raw > CONFLICT_OVER}
                if raw > CONFLICT_OVER:
                    conflict_days += 1
                    conflicts.append({"person": p, "date": d,
                                      "hours": round(raw, 1),
                                      "jobs": [j["title"] for j in jobs_on[(p, d)]]})
                used = min(raw, avail)
                wk_booked += used
                wk_avail += avail
                if d < two_weeks:
                    free_2w += avail - used
            by_week[m] = {
                "booked": round(wk_booked, 1),
                "available": round(wk_avail, 1),
                "pct": round(wk_booked / wk_avail * 100) if wk_avail else None,
                "conflict_days": conflict_days,
            }
            b_total += wk_booked
            a_total += wk_avail
        # Nothing booked and no time off anywhere in the window: a name that
        # appeared on an event but carries no weekday hours (a weekend job,
        # a malformed entry). A row of zeros is noise, not capacity.
        if b_total == 0 and not any(v.get("off") for v in days.values()):
            continue
        rows.append({"name": p, "weeks": by_week, "days": days,
                     "free_next_2w": round(free_2w, 1),
                     "booked_total": round(b_total, 1),
                     "available_total": round(a_total, 1)})

    conflicts.sort(key=lambda c: (c["date"], c["person"]))
    return {"weeks": mondays, "people": rows, "conflicts": conflicts,
            "error": fetched["error"]}


def clear_cache() -> None:
    _fetch_events.clear()
