"""
DashboardService - fetches live counts from Zoho and shapes them into the
dict the dashboard page renders. This is the class Capacity_planner.py was
already importing but that didn't exist yet.

Design choice: if Zoho is unreachable or unconfigured, we don't crash the
page - we fall back to the last cached snapshot (or zeros) and flag it, so
the dashboard degrades gracefully instead of throwing a stack trace.

Tasks count: earlier versions called the tasks/ endpoint once per
project to count them, which at ~350 projects blows straight through
Zoho's real, confirmed limit on that endpoint - "Cannot execute more
than 100 requests per API in 2 minutes" (a 9-minute lockout once
tripped). There's no need to pay that cost at all: each project record
from get_active_projects() already carries a task_count {open, closed}
field, so the Tasks KPI is just a sum over data we're already fetching
for the Projects count - zero extra API calls, and immune to that
throttle entirely.

Caching: still wrapped in st.cache_data so a Zoho fetch (Projects/Cases/
Users) doesn't re-run on every single rerun - including things like the
password screen or just reloading the tab. _fetch_kpis_cached is shared
across the whole app (all viewers, not just one session) and only
actually re-hits Zoho once every CACHE_TTL_SECONDS.

Upcoming schedule: get_upcoming_events() layers on top of
zoho_client.get_calendar_events() to answer "what's on the calendar in
the next N days", rather than the raw event dump that method returns.
Unlike the KPI numbers, a calendar widget failing to load isn't worth
treating as seriously - it degrades to "couldn't load the schedule right
now" instead of falling back to a stale cached snapshot.
"""
import datetime as dt

import streamlit as st

from app.api.zoho_projects import zoho_client, ZohoAPIError
from app.config.settings import settings
from app.database.database import get_session, init_db
from app.database.models import KpiSnapshot
from app.utils.logger import get_logger

logger = get_logger(__name__)


class DashboardService:
    def __init__(self):
        init_db()

    def get_kpis(self) -> dict:
        """
        Returns: {"projects": int, "tasks": int, "cases": int, "users": int,
                   "source": "zoho" | "cache", "error": str | None}
        """
        return _fetch_kpis_cached()

    def get_upcoming_events(self, days_ahead: int = 14) -> dict:
        """
        Returns: {"events": [ {..raw Zoho event fields.., "start": datetime,
                   "end": datetime} ], "error": str | None}

        "events" is sorted soonest-first and filtered to events that
        haven't fully ended yet and start within `days_ahead` days from
        now - i.e. "what's coming up", not the full history of every
        calendar entry ever created. Superseded by get_calendar_range()
        for the dashboard's grid view (which needs whole months, including
        days already past), but left in place in case an agenda-style
        list is useful again later.
        """
        return _fetch_upcoming_events_cached(days_ahead)

    def get_calendar_range(self, start_date: dt.date, end_date: dt.date) -> dict:
        """
        Returns: {"events": [ {..raw Zoho event fields.., "start": datetime,
                   "end": datetime, "tech_name": str, "is_all_day": bool,
                   "participant_names": [str, ...]} ], "error": str | None}

        Unlike get_upcoming_events, this is date-range based rather than
        "from now forward" - it includes events already in the past, which
        a month-grid view needs (e.g. showing Sept 1-4 even when today is
        Sept 5). An event is included if any part of it falls on or
        between start_date and end_date (inclusive) - callers that key
        events by a single date should place a returned event on every day
        from its start date to its end date, not just its start date, or
        a multi-day job (all-day or otherwise) will only ever show up on
        the first day of its span.

        tech_name: the Zoho Events "Owner" field - confirmed by the
        business to be the assigned tech, not just whoever created the
        entry. Techs on the standing exclusion list (_EXCLUDED_TECH_NAMES)
        are dropped entirely before this function returns.
        """
        return _fetch_calendar_range_cached(start_date, end_date)

    @staticmethod
    def _save_snapshot(kpis: dict) -> None:
        with get_session() as session:
            session.add(
                KpiSnapshot(
                    projects=kpis["projects"],
                    tasks=kpis["tasks"],
                    cases=kpis["cases"],
                    users=kpis["users"],
                    source="zoho",
                )
            )

    @staticmethod
    def _fallback_kpis(error: str) -> dict:
        # Read last.projects/tasks/etc. *inside* the session block, not
        # after it - get_session() commits and closes on exit, and
        # SQLAlchemy expires an object's attributes on commit, so touching
        # them once the session has closed raises DetachedInstanceError
        # instead of gracefully falling back.
        with get_session() as session:
            last = (
                session.query(KpiSnapshot)
                .order_by(KpiSnapshot.captured_at.desc())
                .first()
            )
            if last:
                return {
                    "projects": last.projects,
                    "tasks": last.tasks,
                    "cases": last.cases,
                    "users": last.users,
                    "source": "cache",
                    "error": error,
                }
        return {
            "projects": 0, "tasks": 0, "cases": 0, "users": 0,
            "source": "cache", "error": error,
        }


@st.cache_data(ttl=settings.CACHE_TTL_SECONDS, show_spinner="Fetching latest data from Zoho...")
def _fetch_kpis_cached() -> dict:
    try:
        # Fetch the active-project list once. Each project already carries
        # a task_count {open, closed} field, so the Tasks KPI is a sum over
        # that - no separate per-project tasks/ call, no rate-limit risk.
        active_projects = zoho_client.get_active_projects()
        open_tasks = sum(
            int((p.get("task_count") or {}).get("open") or 0) for p in active_projects
        )
        cases = zoho_client.get_cases()
        users = zoho_client.get_crm_users()

        kpis = {
            "projects": len(active_projects),
            "tasks": open_tasks,
            "cases": len(cases),
            "users": len(users),
            "source": "zoho",
            "error": None,
        }
        DashboardService._save_snapshot(kpis)
        return kpis

    except ZohoAPIError as exc:
        logger.warning("Live Zoho fetch failed, falling back to cache: %s", exc)
        return DashboardService._fallback_kpis(error=str(exc))


def _parse_zoho_datetime(value: str):
    """
    Zoho returns datetimes like "2026-09-10T09:00:00-07:00". datetime.
    fromisoformat handles that offset form directly (no external date
    library needed) - returns None instead of raising if a record has a
    missing/malformed value, so one bad record can't take down the whole
    widget.
    """
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        logger.warning("Could not parse calendar event datetime: %r", value)
        return None


@st.cache_data(ttl=settings.CACHE_TTL_SECONDS, show_spinner="Fetching calendar...")
def _fetch_upcoming_events_cached(days_ahead: int) -> dict:
    try:
        raw_events = zoho_client.get_calendar_events()
    except ZohoAPIError as exc:
        logger.warning("Calendar fetch failed: %s", exc)
        return {"events": [], "error": str(exc)}

    now = dt.datetime.now(dt.timezone.utc)
    window_end = now + dt.timedelta(days=days_ahead)

    upcoming = []
    for event in raw_events:
        start = _parse_zoho_datetime(event.get("Start_DateTime"))
        end = _parse_zoho_datetime(event.get("End_DateTime")) or start
        if start is None:
            continue
        # Keep anything still in progress or starting before the window
        # closes - drop events that already fully ended, and ones too far
        # out to be "upcoming" yet.
        if end is not None and end < now:
            continue
        if start > window_end:
            continue
        upcoming.append({**event, "start": start, "end": end})

    upcoming.sort(key=lambda e: e["start"])
    return {"events": upcoming, "error": None}


# Techs/owners whose calendar entries should never show up on the
# schedule (overview grid, day-by-day list, or the tech-color legend) -
# former employees, subcontractors who've moved on, etc. Requested
# 2026-09-06. Matched case-insensitively and trimmed, since Zoho's Owner
# name formatting isn't guaranteed byte-for-byte consistent.
_EXCLUDED_TECH_NAMES = {
    name.strip().lower()
    for name in [
        "Johnathon Crowley",
        "Izaac Kines",
        "Jacob Thomas",
        "Jennifer McKenzie",
        "Tim Fay",
        "Andrew Pinedo",
        "Rich Pereira",
        "Jeremy McKenzie",
        "Christian Van Horn",
        "Casey Webster",
        "Steven Gin",
    ]
}


def _is_excluded_tech(tech_name: str) -> bool:
    return tech_name.strip().lower() in _EXCLUDED_TECH_NAMES


def _extract_tech_name(event: dict) -> str:
    """
    Best guess at "who's assigned" - the Zoho Events "Owner" field, a
    {id, name, email} reference to a CRM user. Unconfirmed whether this
    is actually the tech doing the job vs. whoever in the office created
    the entry - see get_calendar_range()'s docstring.
    """
    owner = event.get("Owner")
    if isinstance(owner, dict):
        return owner.get("name") or owner.get("email") or "Unassigned"
    return "Unassigned"


def _extract_participant_names(event: dict) -> list:
    """
    Zoho Events "Participants" is a list of {type: 'user'|'contact'|...,
    name/Full_Name, email, ...} objects - defensive about the exact shape
    since it hasn't been checked against a real event yet.
    """
    participants = event.get("Participants")
    names = []
    if isinstance(participants, list):
        for p in participants:
            if not isinstance(p, dict):
                continue
            name = p.get("name") or p.get("Full_Name") or p.get("email")
            if name:
                names.append(name)
    return names


@st.cache_data(ttl=settings.CACHE_TTL_SECONDS, show_spinner="Fetching calendar...")
def _fetch_calendar_range_cached(start_date: dt.date, end_date: dt.date) -> dict:
    try:
        raw_events = zoho_client.get_calendar_events()
    except ZohoAPIError as exc:
        logger.warning("Calendar fetch failed: %s", exc)
        return {"events": [], "error": str(exc)}

    in_range = []
    for event in raw_events:
        start = _parse_zoho_datetime(event.get("Start_DateTime"))
        end = _parse_zoho_datetime(event.get("End_DateTime")) or start
        if start is None:
            continue
        # Compare using each event's own local date (its Zoho offset is
        # assumed to already be this company's local time zone) rather
        # than converting to UTC - simpler, and correct for a single-
        # location business where every event uses the same offset.
        event_start_date = start.date()
        event_end_date = (end or start).date()
        if event_end_date < start_date or event_start_date > end_date:
            continue

        tech_name = _extract_tech_name(event)
        if _is_excluded_tech(tech_name):
            continue

        in_range.append({
            **event,
            "start": start,
            "end": end,
            "tech_name": tech_name,
            # Zoho's own "All_day" flag on the Events module - an all-day
            # job spanning several days (a 3-day install, say) has a real
            # Start_DateTime/End_DateTime, but showing a literal time like
            # "12:00 AM" for it is misleading, so callers use this to
            # render "All day" instead.
            "is_all_day": bool(event.get("All_day")),
            "participant_names": _extract_participant_names(event),
        })

    # .replace(tzinfo=None) rather than comparing "start" directly: an
    # all-day event's Start_DateTime can come back from Zoho as a bare
    # date ("2026-09-05", no offset) while a normal timed event carries a
    # UTC offset - Python raises TypeError comparing an offset-naive and
    # an offset-aware datetime, which would crash this sort (and silently
    # take the whole calendar down) the moment a single all-day event
    # showed up alongside a timed one. Stripping tzinfo for the sort key
    # only (never for display) keeps relative order correct for a single-
    # location business where every timed event already shares one offset.
    in_range.sort(key=lambda e: e["start"].replace(tzinfo=None))
    return {"events": in_range, "error": None}
