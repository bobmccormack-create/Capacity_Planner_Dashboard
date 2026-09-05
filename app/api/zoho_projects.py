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

            if resp.status_code >= 400:
                # raise_for_status() alone only gives "400 Client Error: "
                # with no explanation - Zoho puts the actual reason in the
                # response body, so grab that too or we're debugging blind.
                body = (resp.text or "")[:500]
                logger.error(
                    "Zoho API request to %s failed: %s %s - body: %s",
                    url, resp.status_code, resp.reason, body,
                )
                raise ZohoAPIError(
                    f"Request to {url} failed: {resp.status_code} {resp.reason} - {body}"
                )

            try:
                return resp.json()
            except ValueError as exc:
                # A 2xx status doesn't guarantee a JSON body - an empty or
                # non-JSON response here used to crash the whole page with
                # a raw traceback instead of degrading gracefully. Wrap it
                # like every other failure so callers can fall back.
                body = (resp.text or "")[:500]
                logger.error(
                    "Zoho API response from %s was not valid JSON (status %s): %s - body: %s",
                    url, resp.status_code, exc, body,
                )
                raise ZohoAPIError(
                    f"Response from {url} was not valid JSON (status {resp.status_code}): {body}"
                ) from exc

    # ---------------- Zoho Projects ----------------

    def get_projects(self) -> List[dict]:
        """
        All projects in the configured portal.

        This endpoint paginates - a single unparameterized call only
        returns the first page (Zoho's default page size). Every prior
        run of this dashboard has shown "100 projects", which is exactly
        the kind of round number a default page size produces, so this
        pages through with explicit index/range until a short page says
        there's nothing left, instead of assuming one page is everything.
        """
        url = f"{settings.zoho_projects_base()}/restapi/portal/{settings.ZOHO_PORTAL_ID}/projects/"
        page_size = 100
        index = 1
        all_projects: List[dict] = []
        while True:
            data = self._get(url, params={"index": index, "range": page_size})
            page = data.get("projects", [])
            all_projects.extend(page)
            if len(page) < page_size:
                break
            index += page_size
        return all_projects

    def get_active_projects(self) -> List[dict]:
        """
        Projects with status "active", excluding completed/archived ones.

        Once pagination was fixed, get_projects() went from 100 to 351 -
        looping tasks over all 351 one at a time (even paced) could take
        several minutes per load. Most of those extra 251 are completed/
        archived jobs that don't need a live task count, so both the
        Projects KPI and the task-aggregation loop use this narrower,
        much faster list instead.
        """
        return [p for p in self.get_projects() if str(p.get("status", "")).lower() == "active"]

    def get_tasks(self, project_id: str = None) -> List[dict]:
        """
        All tasks for a project. If project_id isn't given, uses
        ZOHO_PROJECTS_PROJECT_ID from settings, or falls back to
        aggregating tasks across every *active* project (slower - one
        call each, paced to stay under Zoho's rate limit).
        """
        project_id = project_id or settings.ZOHO_PROJECTS_PROJECT_ID

        if project_id:
            base = settings.zoho_projects_base()
            portal = settings.ZOHO_PORTAL_ID
            url = f"{base}/restapi/portal/{portal}/projects/{project_id}/tasks/"
            data = self._get(url)
            return data.get("tasks", [])

        # No single project configured: aggregate across all *active*
        # projects (see get_active_projects() for why not all 351).
        return self.get_tasks_for_projects(self.get_active_projects())

    def get_tasks_for_projects(self, projects: List[dict]) -> List[dict]:
        """
        Same aggregation as get_tasks()'s "all projects" path, but takes
        an already-fetched project list - so a caller that also needs the
        project list itself (e.g. for the Projects count) can fetch it
        once instead of paying for the paginated projects call twice.
        """
        base = settings.zoho_projects_base()
        portal = settings.ZOHO_PORTAL_ID

        all_tasks: List[dict] = []
        attempted = 0
        failed = 0
        for project in projects:
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
            # Every single project failed the same way - could be rate
            # limiting, but could just as easily be an API/auth/portal
            # problem hitting every request identically (the per-project
            # log lines above carry Zoho's actual error body - check
            # those rather than assuming rate limiting). Raise instead of
            # returning [] so the caller's fallback-to-last-cache path
            # kicks in rather than the dashboard showing a false zero.
            raise ZohoAPIError(
                f"All {attempted} project task requests failed - "
                "refusing to report 0 tasks. See logs for the underlying "
                "Zoho error."
            )
        if failed:
            logger.warning(
                "Task aggregation: %s/%s projects failed to return tasks - "
                "partial count returned.",
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

    def get_calendar_events(self, per_page: int = 200) -> List[dict]:
        """
        Events from Zoho CRM's built-in Calendar (crm.zoho.com/.../calendar)
        - this is the CRM "Events" module, not a separate Zoho Calendar
        product, so it reuses the same CRM OAuth token/scope as
        get_cases()/get_crm_users() rather than needing its own app
        registration.

        Zoho's v6 Events endpoint only allows sorting by id, Created_Time
        or Modified_Time server-side (confirmed live - it 400s on anything
        else, including the obvious Start_DateTime) - so we don't ask it
        to sort at all, and instead sort by Start_DateTime ourselves once
        the records are back.
        """
        url = f"{settings.zoho_crm_base()}/crm/v6/Events"
        data = self._get(
            url,
            params={
                "fields": "Event_Title,Start_DateTime,End_DateTime,Owner,Who_Id,What_Id,Description",
                "per_page": per_page,
            },
        )
        events = data.get("data", [])
        events.sort(key=lambda e: e.get("Start_DateTime") or "", reverse=True)
        return events


zoho_client = ZohoClient()
