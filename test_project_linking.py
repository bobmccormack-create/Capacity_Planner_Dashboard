"""
One-time helper: checks whether Zoho Projects project names and Zoho CRM
calendar event titles can realistically be matched up automatically (e.g.
a project named "26535 Altamont" matching an event titled
"26535 Altamont Rough In").

No changes are made anywhere - this only reads data and prints a report.

Run locally:

  .venv\\Scripts\\python.exe test_project_linking.py      (Windows)
  .venv/bin/python test_project_linking.py                  (Mac/Linux)
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.api.zoho_projects import zoho_client, ZohoAPIError  # noqa: E402


def normalize(text: str) -> str:
    """Lowercase, collapse whitespace/punctuation, for loose comparison."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


# Lots of project names look like "6387 - 740 Sanchez" (an internal job
# code, then the address) while calendar events just say "740 Sanchez
# Rough In" with no job code - so a plain "is one a substring of the
# other" check misses those. Stripping the job-code prefix and comparing
# on shared tokens (requiring at least one shared *number*, since street
# numbers are the strongest signal) catches those without needing to
# hardcode every prefix/suffix Zoho or your team happens to use.
_JOB_CODE_PREFIX = re.compile(r"^\d+\s*-\s*")


def address_tokens(project_name: str) -> set:
    return set(normalize(_JOB_CODE_PREFIX.sub("", project_name)).split())


def event_title_tokens(title: str) -> set:
    return set(normalize(title).split())


def tokens_match(project_tokens: set, event_tokens: set) -> bool:
    if not project_tokens:
        return False
    overlap = project_tokens & event_tokens
    # Allow one token to differ (renamed/abbreviated word) but require the
    # match to be anchored by at least one shared number (a street number,
    # not just a common word like "street" or "green").
    return len(overlap) >= max(1, len(project_tokens) - 1) and any(
        t.isdigit() for t in overlap
    )


def main() -> None:
    print("=== Project <-> Calendar event name-matching check ===\n")

    print("Fetching Zoho Projects...")
    try:
        projects = zoho_client.get_projects()
    except ZohoAPIError as exc:
        print(f"Could not fetch projects: {exc}")
        sys.exit(1)

    if not projects:
        print("No projects came back - nothing to compare.")
        return

    # Zoho Projects field names haven't been confirmed live yet - print the
    # raw first record so we can see exactly what's available.
    print(f"\nGot {len(projects)} project(s). Raw fields on the first one:")
    print(f"  {projects[0]}\n")

    # Try the field names that are most likely to hold the project's name.
    def project_name(p: dict) -> str:
        return p.get("name") or p.get("project_name") or p.get("Name") or ""

    names = [project_name(p) for p in projects]
    print("First 15 project names, as Zoho Projects has them:")
    for n in names[:15]:
        print(f"  - {n!r}")

    print("\nFetching Zoho CRM calendar events...")
    try:
        events = zoho_client.get_calendar_events()
    except ZohoAPIError as exc:
        print(f"Could not fetch events: {exc}")
        sys.exit(1)

    titles = [e.get("Event_Title", "") for e in events]

    # Matching attempt: strip any leading job-code prefix off the project
    # name, then check whether most of its address tokens (anchored by at
    # least one shared number) show up in the event title.
    project_token_sets = [(n, address_tokens(n)) for n in names if n]
    matched_events = 0
    unmatched_events = []
    match_examples = []

    for title in titles:
        etoks = event_title_tokens(title)
        hit = None
        for original_name, ptoks in project_token_sets:
            if tokens_match(ptoks, etoks):
                hit = original_name
                break
        if hit:
            matched_events += 1
            if len(match_examples) < 10:
                match_examples.append((title, hit))
        else:
            unmatched_events.append(title)

    print(f"\n{matched_events} of {len(titles)} calendar events matched a project by name.")

    print("\nSample matches (event -> project):")
    for title, hit in match_examples:
        print(f"  {title!r}  ->  {hit!r}")

    print(f"\nSample UNmatched events (first 10 of {len(unmatched_events)}):")
    for title in unmatched_events[:10]:
        print(f"  {title!r}")


if __name__ == "__main__":
    main()
