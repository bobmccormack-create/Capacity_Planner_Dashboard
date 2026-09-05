"""
Zoho OAuth token manager.

Zoho access tokens expire (~1 hour). This class exchanges the long-lived
refresh token for a short-lived access token and caches it in memory,
re-fetching automatically when it's about to expire.

Setup required (one-time, done outside this app):
  1. Register a "Server-based Application" at https://api-console.zoho.com
  2. Grant it the scopes you need, e.g.:
       ZohoProjects.portals.READ,ZohoProjects.projects.READ,
       ZohoProjects.tasks.READ,ZohoCRM.modules.ALL,ZohoCRM.users.READ
  3. Generate a refresh token via the standard Zoho OAuth "grant" flow
  4. Put ZOHO_CLIENT_ID / ZOHO_CLIENT_SECRET / ZOHO_REFRESH_TOKEN in .env
"""
import time
from typing import Optional

import requests

from app.config.settings import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ZohoAuthError(Exception):
    """Raised when Zoho OAuth fails (bad credentials, network error, etc.)."""


class ZohoAuth:
    def __init__(self):
        self._access_token: Optional[str] = None
        self._expires_at: float = 0.0

    def get_access_token(self) -> str:
        """Return a valid access token, refreshing it if needed."""
        if self._access_token and time.time() < self._expires_at:
            return self._access_token

        if not settings.has_zoho_credentials():
            raise ZohoAuthError(
                "Missing Zoho credentials. Set ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET "
                "and ZOHO_REFRESH_TOKEN in your .env file."
            )

        url = f"{settings.zoho_accounts_base()}/oauth/v2/token"
        params = {
            "refresh_token": settings.ZOHO_REFRESH_TOKEN,
            "client_id": settings.ZOHO_CLIENT_ID,
            "client_secret": settings.ZOHO_CLIENT_SECRET,
            "grant_type": "refresh_token",
        }

        try:
            resp = requests.post(url, params=params, timeout=settings.REQUEST_TIMEOUT_SECONDS)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            logger.error("Zoho token refresh failed: %s", exc)
            raise ZohoAuthError(f"Could not reach Zoho accounts server: {exc}") from exc

        if "access_token" not in data:
            logger.error("Zoho token refresh returned no access_token: %s", data)
            raise ZohoAuthError(f"Zoho did not return an access token: {data}")

        self._access_token = data["access_token"]
        # Zoho returns expires_in seconds; refresh a little early to be safe.
        self._expires_at = time.time() + int(data.get("expires_in", 3600)) - 60
        logger.info("Refreshed Zoho access token (expires in %ss)", data.get("expires_in"))

        return self._access_token

    def auth_header(self) -> dict:
        return {"Authorization": f"Zoho-oauthtoken {self.get_access_token()}"}


# Single shared instance so the token cache is reused across the app
zoho_auth = ZohoAuth()
