"""
One-time helper: run the QuickBooks Time (Intuit) OAuth "authorization
code" flow end to end - builds the login URL, waits for you to paste back
the code Intuit puts in your browser's address bar, trades it for a
refresh token, and saves it straight into your .env file.

How to use:
  1. In your Intuit Developer app (developer.intuit.com -> your workspace
     -> your app -> Keys & Credentials), add a Redirect URI if you haven't
     already - any https URL works (e.g. https://www.example.com/callback).
     Nothing needs to actually run there; you're only going to copy the
     ?code= value out of the address bar after Intuit redirects you to it.
  2. Run this script:
       .venv\\Scripts\\python.exe get_tsheets_refresh_token.py      (Windows)
       .venv/bin/python get_tsheets_refresh_token.py                 (Mac/Linux)
     No need to edit .env by hand first - if your Client ID/secret aren't
     in there yet, the script asks for them and saves them itself.
  3. It prints a URL - open it in your browser, log into QuickBooks Time
     with the account whose timesheets the dashboard should read, and
     approve access.
  4. Intuit redirects you to your Redirect URI with the page probably
     failing to load (expected, since nothing's listening there) - that's
     fine. Copy the FULL resulting URL out of the address bar and paste it
     back here when asked.
  5. The script pulls the code out of that URL, trades it for a refresh
     token, prints it, and - with your OK - writes it into
     TSHEETS_REFRESH_TOKEN= in .env for you.

One thing that makes this different from the Zoho version of this script:
Intuit's refresh tokens rotate - every time the app uses this refresh
token to get a new access token, Intuit hands back a brand new refresh
token and the old one stops working (after a short grace period). The app
handles that in memory while it's running (see tsheets_auth.py), but if
this refresh token in .env goes unused for a long stretch (Intuit's limit
is 100 days) it'll expire and you'll need to run this script again.
"""
import base64
import os
import re
import sys
import urllib.parse
from pathlib import Path

import requests
from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent / ".env"

load_dotenv(ENV_PATH)

CLIENT_ID = os.getenv("TSHEETS_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("TSHEETS_CLIENT_SECRET", "").strip()

# Populated by main() if CLIENT_ID/CLIENT_SECRET above were blank and had
# to be typed in interactively - see get_or_ask_for_client_credentials().
_ENTERED_CLIENT_ID = None
_ENTERED_CLIENT_SECRET = None

AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
# QuickBooks Time's OAuth scope - grants read/write access to timesheets,
# jobcodes, and users for whichever company the person who approves this
# belongs to.
SCOPE = "com.intuit.quickbooks.timetracking"


def fail(msg: str) -> None:
    print(f"\nERROR: {msg}")
    sys.exit(1)


def get_or_ask_for_client_credentials() -> tuple:
    """
    Returns (client_id, client_secret). If they're not already in .env,
    asks for them right here instead of making you go edit .env by hand -
    then saves them into .env for next time, same as the refresh token
    gets saved at the end of this script.
    """
    global _ENTERED_CLIENT_ID, _ENTERED_CLIENT_SECRET

    client_id, client_secret = CLIENT_ID, CLIENT_SECRET

    if not client_id:
        client_id = input(
            "Paste your QuickBooks Time Client ID "
            "(from the Keys & Credentials page): "
        ).strip()
        _ENTERED_CLIENT_ID = client_id

    if not client_secret:
        client_secret = input(
            "Paste your QuickBooks Time Client secret "
            "(same page, next to Client ID): "
        ).strip()
        _ENTERED_CLIENT_SECRET = client_secret

    if not client_id or not client_secret:
        fail("Both a Client ID and Client secret are required to continue.")

    if _ENTERED_CLIENT_ID or _ENTERED_CLIENT_SECRET:
        set_env_var("TSHEETS_CLIENT_ID", client_id)
        set_env_var("TSHEETS_CLIENT_SECRET", client_secret)
        print(f"\nSaved those into {ENV_PATH.name} so you won't be asked again.")

    return client_id, client_secret


def main() -> None:
    print("=== QuickBooks Time (T-Sheets) refresh token generator ===\n")

    if not ENV_PATH.exists():
        fail(f"Couldn't find a .env file at {ENV_PATH}.")

    client_id, client_secret = get_or_ask_for_client_credentials()
    print(f"\nUsing client ID: {client_id[:12]}...\n")

    redirect_uri = input(
        "Redirect URI you added in Keys & Credentials "
        "[https://www.example.com/callback]: "
    ).strip() or "https://www.example.com/callback"

    auth_params = {
        "client_id": client_id,
        "response_type": "code",
        "scope": SCOPE,
        "redirect_uri": redirect_uri,
        "state": "capacity_planner_setup",
    }
    login_url = f"{AUTH_URL}?{urllib.parse.urlencode(auth_params)}"

    print("\nOpen this URL in your browser and approve access:\n")
    print(f"  {login_url}\n")

    redirected_url = input(
        "After approving, paste the FULL URL you landed on "
        "(the one with ?code=... in it): "
    ).strip()

    if not redirected_url:
        fail("No URL pasted.")

    parsed = urllib.parse.urlparse(redirected_url)
    query = urllib.parse.parse_qs(parsed.query)
    code = (query.get("code") or [None])[0]

    if not code:
        fail(
            "Couldn't find a 'code' parameter in that URL. Make sure you "
            "pasted the full address-bar URL after approving access, not "
            "the login URL from before."
        )

    print(f"\nGot authorization code: {code[:12]}...")
    print("Contacting Intuit...")

    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    headers = {
        "Authorization": f"Basic {basic}",
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }

    try:
        resp = requests.post(TOKEN_URL, headers=headers, data=data, timeout=15)
    except requests.RequestException as exc:
        fail(f"Could not reach Intuit: {exc}")

    try:
        body = resp.json()
    except ValueError:
        fail(
            f"Intuit returned a non-JSON response (HTTP {resp.status_code}):\n"
            f"  {resp.text}"
        )

    if "refresh_token" not in body:
        fail(
            "Intuit didn't return a refresh token. Its response was:\n"
            f"  {body}\n\n"
            "Common causes: the code already expired or was already used "
            "(they're single-use and only last a few minutes) - go get a "
            "fresh one via the URL above and re-run this script right "
            "away; or the redirect_uri you entered doesn't exactly match "
            "the one saved in Keys & Credentials."
        )

    refresh_token = body["refresh_token"]
    access_token = body.get("access_token", "")
    expires_in = body.get("expires_in", "?")
    refresh_expires_in = body.get("x_refresh_token_expires_in", "?")

    print("\nSuccess! Intuit returned:")
    print(f"  refresh_token: {refresh_token}")
    print(
        f"  access_token:  {access_token[:12]}... "
        f"(short-lived, expires in {expires_in}s - the app fetches its own "
        "each hour, so you can ignore this one)"
    )
    print(f"  refresh_token is valid for up to {refresh_expires_in}s if unused")

    choice = input(
        f"\nWrite this refresh token into {ENV_PATH.name} now? [Y/n]: "
    ).strip().lower()

    if choice in ("", "y", "yes"):
        set_env_var("TSHEETS_REFRESH_TOKEN", refresh_token)
        print(f"\nDone - TSHEETS_REFRESH_TOKEN has been saved to {ENV_PATH}.")
        print("Restart the Streamlit app (or refresh the page) to pick it up.")
    else:
        print(
            "\nOK, not touching the file. Copy the refresh_token above into "
            "TSHEETS_REFRESH_TOKEN= in your .env file yourself."
        )


def set_env_var(key: str, value: str) -> None:
    """Add or replace a KEY= line in .env, preserving everything else in the file."""
    text = ENV_PATH.read_text(encoding="utf-8")

    pattern = rf"^{re.escape(key)}=.*$"
    if re.search(pattern, text, flags=re.MULTILINE):
        new_text = re.sub(pattern, f"{key}={value}", text, count=1, flags=re.MULTILINE)
    else:
        new_text = text.rstrip("\n") + f"\n{key}={value}\n"

    ENV_PATH.write_text(new_text, encoding="utf-8")


if __name__ == "__main__":
    main()
