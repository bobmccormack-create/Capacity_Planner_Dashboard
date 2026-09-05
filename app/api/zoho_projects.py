"""
Thin client for the Zoho Projects and Zoho CRM REST APIs.

Only the read endpoints needed for the dashboard KPIs are implemented.
Each method returns plain Python data (lists/dicts) so callers don't need
to know anything about Zoho's response envelope.

Rate limiting: aggregating tasks across every project (no single
ZOHO_PROJECTS_PROJECT_ID configured) means one request per project, fired
back-to-back. With 100 projects that's a burst of 100 calls in a few
seconds, which is exactly the shape of traffic Zoho's per-minute rate
limit is built to reject - and previously a rejected (429) call was
silently skipped, so a fully-rate-limited run quietly reported 0 tasks
as if that were the real number. _get() now retries a 429 a couple of
times with backoff, get_tasks() paces its per-project calls, and if every
single project's request still fails, get_tasks() raises instead of
handing back an empty list - so the dashboard falls back to its last
known-good cached count instead of overwriting it with a false zero.
"""
import time
from typing import Any, Dict, List

import requests

from app.api.zoho_auth import zoho_auth, ZohoAuthError
from app.config.settings import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Pause between per-project task requests when aggregating across every
# project, to avoid bursting past Zoho's rate limit in the first place.
# (Zoho doesn't publish an exact number for this endpoint, so this errs
# conservative rather than tuning to a guessed threshold.)
_TASK_LOOP_DELAY_SECONDS = 0.5

# How many times to retry a single request after a 429 (Too Many Requests)
# before giving up on it, waiting longer each time.
_MAX_RATE_LIMIT_RETRIES = 2


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

        attempt = 0
        while True:
            try:
                resp = self._session.get(
                    url, headers=headers, params=params or {},
                    timeout=settings.REQUEST_TIMEOUT_SECONDS,
                )
            except requests.RequestException as exc:
                logger.error("Zoho API request to %s failed: %s", url, exc)
                raise ZohoAPIError(f"Request to {url} failed: {exc}") from exc

            if resp.status_code == 429 and attempt < _MAX_RATE_LIMIT_RETRIES:
                wait = 2 ** (attempt + 1)  # 2s, then 4s
                logger.warning(
                    "Zoho rate limit (429) on %s, retrying in %ss (attempt %s/%s)",
                    url, wait, attempt + 1, _MAX_RATE_LIMIT_RETRIES,
                )
                time.sleep(wait)
                attempt += 1
                continue

            try:
                resp.raise_for_status()
            except requests.RequestException as exc:
                logger.error("Zoho API request to %s failed: %s", url, exc)
                raise ZohoAPIError(f"Request to {url} failed: {exc}") from exc

            return resp.json()

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
        aggregating tasks across every project (slower - one call each,
        paced to stay under Zoho's rate limit).
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
        attempted = 0
        failed = 0
        for project in self.get_projects():
            pid = project.get("id") or project.get("id_string")
            if not pid:
                continue
            attempted += 1
            url = f"{base}/restapi/portal/{portal}/projects/{pid}/tasks/"
            try:
                data = self._get(url)
                all_tasks.extend(data.get("tasks", []))
            except ZohoAPIError as exc:
                failed += 1
                logger.warning("Skipping tasks for project %s: %s", pid, exc)
            time.sleep(_TASK_LOOP_DELAY_SECONDS)

        if attempted and failed == attempted:
            # Every single project failed - almost certainly rate-limited
            # (or an auth/portal problem hitting every request the same
            # way). Raise instead of returning [] so the caller's
            # fallback-to-last-cache path kicks in rather than the
            # dashboard showing a false zero.
            raise ZohoAPIError(
                f"All {attempted} project task requests failed "
                "(likely rate-limited) - refusing to report 0 tasks."
            )
        if failed:
            logger.warning(
                "Task aggregation: %s/%s projects failed to return tasks "
                "(rate limit or transient error) - partial count returned.",
                failed, attempted,
            )
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
