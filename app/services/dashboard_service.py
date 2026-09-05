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
        calendar entry ever created.
        """
        return _fetch_upcoming_events_cached(days_ahead)

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
