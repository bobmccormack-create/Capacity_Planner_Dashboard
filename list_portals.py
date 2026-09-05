"""
One-time helper: list every Zoho Projects portal your account can see,
along with its Portal ID, so you don't have to hunt through menus for it.

Run this after ZOHO_CLIENT_ID / ZOHO_CLIENT_SECRET / ZOHO_REFRESH_TOKEN
are all filled in .env:

  .venv\\Scripts\\python.exe list_portals.py      (Windows)
  .venv/bin/python list_portals.py                 (Mac/Linux)

It'll also offer to write the Portal ID (and, optionally, your CRM Org ID)
straight into .env for you.
"""
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.api.zoho_auth import zoho_auth, ZohoAuthError  # noqa: E402
from app.config.settings import settings  # noqa: E402

ENV_PATH = Path(__file__).resolve().parent / ".env"


def fail(msg: str) -> None:
    print(f"\nERROR: {msg}")
    sys.exit(1)


def main() -> None:
    print("=== Zoho Projects portal lookup ===\n")

    try:
        headers = zoho_auth.auth_header()
    except ZohoAuthError as exc:
        fail(str(exc))

    url = f"{settings.zoho_projects_base()}/restapi/portals/"
    print("Contacting Zoho Projects...")
    try:
        resp = requests.get(url, headers=headers, timeout=settings.REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        fail(f"Could not reach Zoho: {exc}")

    try:
        data = resp.json()
    except ValueError:
        fail(f"Zoho returned a non-JSON response (HTTP {resp.status_code}):\n  {resp.text}")

    portals = data.get("portals", [])
    if not portals:
        fail(f"No portals came back. Zoho's response was:\n  {data}")

    print("\nPortals you have access to:\n")
    for i, p in enumerate(portals, start=1):
        name = p.get("name") or p.get("name_formatted") or "?"
        pid = p.get("id") or p.get("id_string")
        print(f"  {i}. {name}  ->  Portal ID: {pid}")

    if len(portals) == 1:
        chosen_id = portals[0].get("id") or portals[0].get("id_string")
        print(f"\nOnly one portal, using it: {chosen_id}")
    else:
        pick = input(f"\nWhich one is yours? Enter a number (1-{len(portals)}): ").strip()
        try:
            chosen = portals[int(pick) - 1]
        except (ValueError, IndexError):
            fail("Not a valid choice.")
        chosen_id = chosen.get("id") or chosen.get("id_string")

    choice = input(f"\nWrite ZOHO_PORTAL_ID={chosen_id} into .env now? [Y/n]: ").strip().lower()
    if choice in ("", "y", "yes"):
        set_env_value("ZOHO_PORTAL_ID", str(chosen_id))
        print("Saved ZOHO_PORTAL_ID.")

    crm_org = input(
        "\nAlso set your CRM Org ID now? Paste it, or leave blank to skip: "
    ).strip()
    if crm_org:
        set_env_value("ZOHO_CRM_ORG_ID", crm_org)
        print("Saved ZOHO_CRM_ORG_ID.")

    print("\nDone. Restart the Streamlit app (or refresh the page) to pick these up.")


def set_env_value(key: str, value: str) -> None:
    """Replace KEY= line in .env, preserving everything else."""
    text = ENV_PATH.read_text(encoding="utf-8")
    pattern = rf"^{re.escape(key)}=.*$"
    if re.search(pattern, text, flags=re.MULTILINE):
        new_text = re.sub(pattern, f"{key}={value}", text, count=1, flags=re.MULTILINE)
    else:
        new_text = text.rstrip("\n") + f"\n{key}={value}\n"
    ENV_PATH.write_text(new_text, encoding="utf-8")


if __name__ == "__main__":
    main()
