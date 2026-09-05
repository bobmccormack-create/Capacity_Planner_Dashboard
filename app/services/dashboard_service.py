"""
DashboardService - fetches live counts from Zoho and shapes them into the
dict the dashboard page renders. This is the class Capacity_planner.py was
already importing but that didn't exist yet.

Design choice: if Zoho is unreachable or unconfigured, we don't crash the
page - we fall back to the last cached snapshot (or zeros) and flag it, so
the dashboard degrades gracefully instead of throwing a stack trace.
"""
from app.api.zoho_projects import zoho_client, ZohoAPIError
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
        try:
            projects = zoho_client.get_projects()
            tasks = zoho_client.get_tasks()
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
            self._save_snapshot(kpis)
            return kpis

        except ZohoAPIError as exc:
            logger.warning("Live Zoho fetch failed, falling back to cache: %s", exc)
            return self._fallback_kpis(error=str(exc))

    def _save_snapshot(self, kpis: dict) -> None:
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

    def _fallback_kpis(self, error: str) -> dict:
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
