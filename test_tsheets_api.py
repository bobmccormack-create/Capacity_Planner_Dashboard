"""
One-time helper: try pulling jobcodes, users, and this week's timesheets
from QuickBooks Time, to confirm the API client's assumptions about field
names and pagination actually match the real account (they were written
from the public API docs, not verified live yet).

Run this locally to get fast feedback (no git push / reboot / log-download
round trip needed):

  .venv\\Scripts\\python.exe test_tsheets_api.py      (Windows)
  .venv/bin/python test_tsheets_api.py                 (Mac/Linux)

If it works, it'll print a sample of jobcodes, users, and timesheets from
the last 7 days. If QuickBooks Time rejects the request, it prints the
actual error text so we know exactly what to fix.
"""
import sys
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.api.tsheets_client import tsheets_client, TSheetsAPIError  # noqa: E402


def main() -> None:
    print("=== QuickBooks Time (T-Sheets) API test ===\n")

    print("Fetching jobcodes...")
    try:
        jobcodes = tsheets_client.get_jobcodes()
    except TSheetsAPIError as exc:
        print(f"\nQuickBooks Time rejected the jobcodes request:\n  {exc}")
        sys.exit(1)
    print(f"Got {len(jobcodes)} jobcode(s). First 5, raw:")
    for jc in jobcodes[:5]:
        print(f"  {jc}")

    print("\nFetching users...")
    try:
        users = tsheets_client.get_users()
    except TSheetsAPIError as exc:
        print(f"\nQuickBooks Time rejected the users request:\n  {exc}")
        sys.exit(1)
    print(f"Got {len(users)} user(s). First 5, raw:")
    for u in users[:5]:
        print(f"  {u}")

    end = datetime.date.today()
    start = end - datetime.timedelta(days=7)
    print(f"\nFetching timesheets from {start} to {end}...")
    try:
        timesheets = tsheets_client.get_timesheets(str(start), str(end))
    except TSheetsAPIError as exc:
        print(f"\nQuickBooks Time rejected the timesheets request:\n  {exc}")
        sys.exit(1)
    print(f"Got {len(timesheets)} timesheet entr(y/ies). First 5, raw:")
    for ts in timesheets[:5]:
        print(f"  {ts}")

    print(
        "\nThat raw output (especially the field names on each record) is "
        "exactly what to send back - it'll show precisely which fields the "
        "dashboard should read for hours, jobcode, and user."
    )


if __name__ == "__main__":
    main()
