"""
One-time helper: exchange a Zoho "grant token" for a long-lived refresh
token, and save it straight into your .env file.

How to use:
  1. Go to https://api-console.zoho.com, open your Self Client, and go to
     the "Generate Code" tab.
  2. Scopes: ZohoProjects.portals.READ,ZohoProjects.projects.READ,
             ZohoProjects.tasks.READ,ZohoCRM.modules.ALL,ZohoCRM.users.READ
  3. Set a short duration (e.g. 10 minutes), add any description, click
     Create.
  4. Copy the grant code it gives you (starts with "1000.") - it's only
     valid for a few minutes and can only be used once, so don't dawdle.
  5. Right after copying it, run this script from the project folder:
       .venv\\Scripts\\python.exe get_refresh_token.py      (Windows)
       .venv/bin/python get_refresh_token.py                (Mac/Linux)
  6. Paste the grant code when it asks.

The script reads ZOHO_CLIENT_ID / ZOHO_CLIENT_SECRET / ZOHO_REGION from
your existing .env file, trades the grant code for a refresh token via
Zoho's OAuth endpoint, prints it, and - with your OK - writes it straight
into ZOHO_REFRESH_TOKEN= in .env for you.
"""
import os
import re
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent / ".env"

load_dotenv(ENV_PATH)

CLIENT_ID = os.getenv("ZOHO_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET", "").strip()
REGION = os.getenv("ZOHO_REGION", "com").strip() or "com"


def fail(msg: str) -> None:
    print(f"\nERROR: {msg}")
    sys.exit(1)


def main() -> None:
    print("=== Zoho refresh token generator ===\n")

    if not ENV_PATH.exists():
        fail(f"Couldn't find a .env file at {ENV_PATH}.")

    if not CLIENT_ID or not CLIENT_SECRET:
        fail(
            "ZOHO_CLIENT_ID and/or ZOHO_CLIENT_SECRET are missing from your "
            f".env file ({ENV_PATH}). Fill those in first, then re-run this."
        )

    print(f"Using region: {REGION}")
    print(f"Using client ID: {CLIENT_ID[:12]}...\n")

    grant_code = input(
        "Paste the grant/authorization code from Zoho\n"
        "(starts with '1000.', only valid for a few minutes): "
    ).strip()

    if not grant_code:
        fail("No grant code entered.")

    redirect_uri = input(
        "Redirect URI you used to get that code "
        "[https://www.example.com]: "
    ).strip() or "https://www.example.com"

    url = f"https://accounts.zoho.{REGION}/oauth/v2/token"
    params = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": grant_code,
        "redirect_uri": redirect_uri,
    }

    print("\nContacting Zoho...")
    try:
        resp = requests.post(url, params=params, timeout=15)
    except requests.RequestException as exc:
        fail(f"Could not reach Zoho: {exc}")

    try:
        data = resp.json()
    except ValueError:
        fail(
            f"Zoho returned a non-JSON response (HTTP {resp.status_code}):\n"
            f"  {resp.text}"
        )

    if "refresh_token" not in data:
        fail(
            "Zoho didn't return a refresh token. Its response was:\n"
            f"  {data}\n\n"
            "Common causes: the code already expired or was already used "
            "(they're single-use and only last a few minutes) - go get a "
            "fresh one and re-run this script right away; or the redirect "
            "URI you entered doesn't exactly match the one you used to "
            "get the code (and the one saved in the Zoho API Console)."
        )

    refresh_token = data["refresh_token"]
    access_token = data.get("access_token", "")
    expires_in = data.get("expires_in", "?")

    print("\nSuccess! Zoho returned:")
    print(f"  refresh_token: {refresh_token}")
    print(
        f"  access_token:  {access_token[:12]}... "
        f"(short-lived, expires in {expires_in}s - the app fetches its own "
        "each hour, so you can ignore this one)"
    )

    choice = input(
        f"\nWrite this refresh token into {ENV_PATH.name} now? [Y/n]: "
    ).strip().lower()

    if choice in ("", "y", "yes"):
        update_env_file(refresh_token)
        print(f"\nDone - ZOHO_REFRESH_TOKEN has been saved to {ENV_PATH}.")
        print("Restart the Streamlit app (or refresh the page) to pick it up.")
    else:
        print(
            "\nOK, not touching the file. Copy the refresh_token above into "
            "ZOHO_REFRESH_TOKEN= in your .env file yourself."
        )


def update_env_file(refresh_token: str) -> None:
    """Replace the ZOHO_REFRESH_TOKEN= line in .env, preserving everything else."""
    text = ENV_PATH.read_text(encoding="utf-8")

    if re.search(r"^ZOHO_REFRESH_TOKEN=.*$", text, flags=re.MULTILINE):
        new_text = re.sub(
            r"^ZOHO_REFRESH_TOKEN=.*$",
            f"ZOHO_REFRESH_TOKEN={refresh_token}",
            text,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        # Shouldn't normally happen, but append it just in case the line's missing.
        new_text = text.rstrip("\n") + f"\nZOHO_REFRESH_TOKEN={refresh_token}\n"

    ENV_PATH.write_text(new_text, encoding="utf-8")


if __name__ == "__main__":
    main()
