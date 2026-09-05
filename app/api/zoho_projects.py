"""
Thin client for the Zoho Projects and Zoho CRM REST APIs.

Only the read endpoints needed for the dashboard KPIs are implemented.
Each method returns plain Python data (lists/dicts) so callers don't need
to know anything about Zoho's response envelope.
"""
from typing import Any, Dict, List

import requests

from app.api.zoho_auth import zoho_auth, ZohoAuthError
from app.config.settings import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ZohoAPIError(Exception):
    """Raised when a Zoho API call fails."""


class ZohoClient:
    def __init__(self):
        self._session = requests.Session()

    def _get(self, url: str, params: dict = None) -> Dict[str, Any]:
        try:
            headers = zoho_auth.auth_header()
        except ZohoAuthError as exc:
            raise ZohoAPIError(str(exc)) from exc

        try:
            resp = self._session.get(
                url, headers=headers, params=params or {},
                timeout=settings.REQUEST_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            logger.error("Zoho API request to %s failed: %s", url, exc)
            raise ZohoAPIError(f"Request to {url} failed: {exc}") from exc

    # ---------------- Zoho Projects ----------------

    def get_projects(self) -> List[dict]:
        """All projects in the configured portal."""
        url = f"{settings.zoho_projects_base()}/restapi/portal/{settings.ZOHO_PORTAL_ID}/projects/"
        data = self._get(url)
        return data.get("projects", [])

    def get_tasks(self, project_id: str = None) -> List[dict]:
        """
        All tasks for a project. If project_id isn't given, uses
        ZOHO_PROJECTS_PROJECT_ID from settings, or falls back to
        aggregating tasks across every project (slower - one call each).
        """
        project_id = project_id or settings.ZOHO_PROJECTS_PROJECT_ID
        base = settings.zoho_projects_base()
        portal = settings.ZOHO_PORTAL_ID

        if project_id:
            url = f"{base}/restapi/portal/{portal}/projects/{project_id}/tasks/"
            data = self._get(url)
            return data.get("tasks", [])

        # No single project configured: aggregate across all projects.
        all_tasks: List[dict] = []
        for project in self.get_projects():
            pid = project.get("id") or project.get("id_string")
            if not pid:
                continue
            url = f"{base}/restapi/portal/{portal}/projects/{pid}/tasks/"
            try:
                data = self._get(url)
                all_tasks.extend(data.get("tasks", []))
            except ZohoAPIError as exc:
                logger.warning("Skipping tasks for project %s: %s", pid, exc)
        return all_tasks

    # ---------------- Zoho CRM ----------------

    def get_cases(self) -> List[dict]:
        """Cases module records from Zoho CRM."""
        url = f"{settings.zoho_crm_base()}/crm/v6/Cases"
        data = self._get(url, params={"fields": "id"})
        return data.get("data", [])

    def get_crm_users(self) -> List[dict]:
        """Active users on the Zoho CRM org."""
        url = f"{settings.zoho_crm_base()}/crm/v6/users"
        data = self._get(url, params={"type": "ActiveUsers"})
        return data.get("users", [])


zoho_client = ZohoClient()
