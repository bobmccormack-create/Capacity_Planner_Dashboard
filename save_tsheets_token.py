"""
One-time helper: saves a QuickBooks Time access token into your .env file,
so you don't have to go edit .env by hand.

This replaces get_tsheets_refresh_token.py, which was built on a wrong
assumption (that QuickBooks Time uses the same OAuth setup as QuickBooks
Online, through developer.intuit.com - it doesn't; see tsheets_auth.py
for the full story). You can delete get_tsheets_refresh_token.py, it's no
longer used.

How to get a token to paste in here:
  1. Log into QuickBooks Time itself (the app your team already uses day
     to day - tsheets.intuit.com or the QuickBooks Time app - NOT
     developer.intuit.com).
  2. As an admin, go to Feature Add-ons -> API.
  3. Click "Add a new application" (a name like "Capacity Planner
     Dashboard" is fine - it's just a label).
  4. That should give you an access token directly, no further sign-in or
     approval screen needed (QuickBooks Time's own docs recommend this
     over the full OAuth flow for exactly this situation - one app,
     reading your own company's data).
  5. While you're there, look for an option to extend that token's
     expiration as far out as it allows - that saves you from having to
     repeat this later.

Run this script:
  .venv\\Scripts\\python.exe save_tsheets_token.py      (Windows)
  .venv/bin/python save_tsheets_token.py                 (Mac/Linux)
"""
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent / ".env"

load_dotenv(ENV_PATH)


def fail(msg: str) -> None:
    print(f"\nERROR: {msg}")
    sys.exit(1)


def set_env_var(key: str, value: str) -> None:
    """Add or replace a KEY= line in .env, preserving everything else in the file."""
    text = ENV_PATH.read_text(encoding="utf-8")

    pattern = rf"^{re.escape(key)}=.*$"
    if re.search(pattern, text, flags=re.MULTILINE):
        new_text = re.sub(pattern, f"{key}={value}", text, count=1, flags=re.MULTILINE)
    else:
        new_text = text.rstrip("\n") + f"\n{key}={value}\n"

    ENV_PATH.write_text(new_text, encoding="utf-8")


def main() -> None:
    print("=== Save QuickBooks Time access token ===\n")

    if not ENV_PATH.exists():
        fail(f"Couldn't find a .env file at {ENV_PATH}.")

    token = input("Paste your QuickBooks Time access token: ").strip()
    if not token:
        fail("No token entered.")

    set_env_var("TSHEETS_ACCESS_TOKEN", token)
    print(f"\nSaved TSHEETS_ACCESS_TOKEN to {ENV_PATH}.")
    print("Restart the Streamlit app (or refresh the page) to pick it up.")
    print(
        "\nNext: run test_tsheets_api.py to confirm it works against your "
        "real QuickBooks Time account."
    )


if __name__ == "__main__":
    main()
