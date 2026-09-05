"""
DashboardService - fetches live counts from Zoho and shapes them into the
dict the dashboard page renders. This is the class Capacity_planner.py was
already importing but that didn't exist yet.

Design choice: if Zoho is unreachable or unconfigured, we don't crash the
page - we fall back to the last cached snapshot (or zeros) and flag it, so
the dashboard degrades gracefully instead of throwing a stack trace.

Caching: fetching "tasks" means one Zoho API call per project when no
single ZOHO_PROJECTS_PROJECT_ID is configured (this app intentionally
aggregates across every project), so a full page load can mean dozens or
hundreds of API calls. Without caching, that refetch happened on every
single rerun - including things like the password screen or just
reloading the tab - which risks tripping Zoho's rate limits and silently
zeroing out counts (failed per-project calls are skipped, not raised).
_fetch_kpis_cached is shared across the whole app (all viewers, not just
one session) and only actually re-hits Zoho once every
CACHE_TTL_SECONDS.
"""
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
        # Fetch the active-project list once and reuse it for both the
        # Projects count and the task aggregation, instead of paying for
        # the (paginated, now ~350-project) projects call twice. Completed/
        # archived projects are excluded from both so the two numbers stay
        # consistent with each other.
        active_projects = zoho_client.get_active_projects()
        projects = active_projects
        tasks = zoho_client.get_tasks_for_projects(active_projects)
        cases = zoho_client.get_cases()
        users = zoho_client.get_crm_users()

        kpis = {
            "projects": len(projects),
            "tasks": len(tasks),
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
