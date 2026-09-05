"""
Thin client for the QuickBooks Time (T-Sheets) REST API.

Only the read endpoints needed for the dashboard are implemented: jobcodes
(T-Sheets' name for the things people clock time against - often mirrored
1:1 with Zoho Projects jobs), users, and timesheets (the actual clocked
time entries).

Response shape: unlike Zoho, QuickBooks Time returns collections as a dict
keyed by record ID rather than a list - e.g. {"1234": {...}, "5678": {...}}
- and pages through a "more" flag rather than a short-page/total-count
signal. _get_all_pages() below normalizes both of those so callers just
get a plain list, the same shape zoho_projects.py hands back.

Field names and the pagination params (page/limit, "more", "results" ->
"supplemental_data") were confirmed against QuickBooks Time's live API
reference docs (tsheetsteam.github.io/api_docs), including real example
responses for jobcodes and timesheets - but this client itself hasn't
been run against a real account's data yet. test_tsheets_api.py is a
throwaway script (same pattern as test_calendar_api.py) to run locally
and confirm that before anything here gets wired into the dashboard.
"""
from typing import Any, Dict, List

import requests

from app.api.tsheets_auth import tsheets_auth, TSheetsAuthError
from app.config.settings import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

BASE_URL = "https://rest.tsheets.com/api/v1"


class TSheetsAPIError(Exception):
    """Raised when a QuickBooks Time API call fails."""


class TSheetsClient:
    def __init__(self):
        self._session = requests.Session()

    def _get_all_pages(self, path: str, result_key: str, params: dict = None) -> List[dict]:
        """
        GETs every page of a QuickBooks Time list endpoint and returns the
        records as a plain list (the API itself hands back a dict keyed by
        record ID per page - {"results": {result_key: {"1": {...}, ...}}} -
        so this also flattens that).
        """
        try:
            headers = tsheets_auth.auth_header()
        except TSheetsAuthError as exc:
            raise TSheetsAPIError(str(exc)) from exc

        url = f"{BASE_URL}/{path}"
        all_records: List[dict] = []
        page = 1
        base_params = dict(params or {})
        # "limit" is QuickBooks Time's current pagination parameter (the
        # older "per_page" is documented as deprecated and caps at 50 -
        # "limit" goes up to 200 per page, fewer round trips).
        base_params.setdefault("limit", 200)

        while True:
            try:
                resp = self._session.get(
                    url, headers=headers, params={**base_params, "page": page},
                    timeout=settings.REQUEST_TIMEOUT_SECONDS,
                )
            except requests.RequestException as exc:
                logger.error("QuickBooks Time request to %s failed: %s", url, exc)
                raise TSheetsAPIError(f"Request to {url} failed: {exc}") from exc

            if resp.status_code >= 400:
                body = (resp.text or "")[:500]
                logger.error(
                    "QuickBooks Time request to %s failed: %s %s - body: %s",
                    url, resp.status_code, resp.reason, body,
                )
                raise TSheetsAPIError(
                    f"Request to {url} failed: {resp.status_code} {resp.reason} - {body}"
                )

            try:
                data = resp.json()
            except ValueError as exc:
                body = (resp.text or "")[:500]
                logger.error(
                    "QuickBooks Time response from %s was not valid JSON: %s - body: %s",
                    url, exc, body,
                )
                raise TSheetsAPIError(
                    f"Response from {url} was not valid JSON: {body}"
                ) from exc

            page_records = (data.get("results") or {}).get(result_key) or {}
            all_records.extend(page_records.values())

            if not data.get("more"):
                break
            page += 1

        return all_records

    def get_jobcodes(self, active_only: bool = True) -> List[dict]:
        """
        Jobcodes are what QuickBooks Time calls the things people clock
        time against - for a construction/trades org this is usually
        1:1 with Zoho Projects jobs.
        """
        params = {"active": "yes"} if active_only else {}
        return self._get_all_pages("jobcodes", "jobcodes", params=params)

    def get_users(self, active_only: bool = True) -> List[dict]:
        params = {"active": "yes"} if active_only else {}
        return self._get_all_pages("users", "users", params=params)

    def get_timesheets(self, start_date: str, end_date: str) -> List[dict]:
        """
        Clocked time entries between start_date and end_date (both
        'YYYY-MM-DD', inclusive) across the whole company.
        """
        return self._get_all_pages(
            "timesheets", "timesheets",
            params={"start_date": start_date, "end_date": end_date},
        )


tsheets_client = TSheetsClient()
