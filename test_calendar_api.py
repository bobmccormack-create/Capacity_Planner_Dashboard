"""
One-time helper: try pulling events from Zoho CRM's built-in Calendar
(what you see at crm.zoho.com/crm/org60300254/calendar) using the same
Zoho CRM credentials already in .env - no new sign-in needed, since this
calendar lives inside CRM, not a separate Zoho Calendar product.

Run this locally to get fast feedback (no git push / reboot / log-download
round trip needed):

  .venv\\Scripts\\python.exe test_calendar_api.py      (Windows)
  .venv/bin/python test_calendar_api.py                 (Mac/Linux)

If it works, it'll print your upcoming/recent calendar events. If Zoho
rejects the request, it prints Zoho's actual error message (not just a
generic failure) so we know exactly what to fix.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.api.zoho_projects import zoho_client, ZohoAPIError  # noqa: E402


def main() -> None:
    print("=== Zoho CRM Calendar (Events) test ===\n")
    print("Contacting Zoho...")
    try:
        events = zoho_client.get_calendar_events()
    except ZohoAPIError as exc:
        print(f"\nZoho rejected the request:\n  {exc}")
        print(
            "\nThat error text (especially any 'code'/'message' from Zoho) "
            "is exactly what to send back - it'll say precisely what field "
            "or permission needs fixing."
        )
        sys.exit(1)

    if not events:
        print("\nNo events came back. That could mean the calendar is "
              "empty, or Zoho silently ignored an unrecognized filter - "
              "worth double-checking against the calendar in your browser.")
        return

    print(f"\nGot {len(events)} event(s):\n")
    for e in events[:20]:
        title = e.get("Event_Title", "(untitled)")
        start = e.get("Start_DateTime", "?")
        end = e.get("End_DateTime", "?")
        print(f"  - {title}  |  {start}  ->  {end}")

    if len(events) > 20:
        print(f"  ... and {len(events) - 20} more")


if __name__ == "__main__":
    main()
