"""
Labour bucket definitions - the single place that decides what kind of
work an hour represents.

Both the standalone analysis scripts (roles.py, analyze_time.py) and the
dashboard import from here, so a bucket can't drift between what a report
says and what the page shows.

Derived from the live account, not from the docs - every phase value below
was confirmed present in 120 days of real timesheets:

    Admin, Finish, (blank), Trim, Prewire, Project Management, Service,
    Production - Engineering, Shading Install, Design - Engineering,
    Drive, Lighting, Shading PM, Travel, Commute, Programming, Warranty

No Streamlit, no network calls, no I/O - pure functions, so this stays
importable from a plain script and testable without a running app.
"""
from __future__ import annotations

from collections import Counter

SECONDS_PER_HOUR = 3600.0

# The customfield carrying the work phase. Detected at runtime by
# detect_phase_field() rather than trusted blindly, since a customfield ID
# is account-specific - this is only the fallback.
DEFAULT_PHASE_FIELD = "91646"

# Order matters: first match wins. "shading install" MUST be tested before
# "install", or shade work silently disappears into the field bucket.
PHASE_BUCKETS = [
    ("shading_install", ["shading install"]),
    ("shading_pm",      ["shading pm"]),
    ("design_eng",      ["design - engineering", "design engineering"]),
    ("production_eng",  ["production - engineering", "production engineering"]),
    ("project_mgmt",    ["project management"]),
    ("service",         ["service", "programming", "warranty"]),
    ("lighting",        ["lighting"]),
    ("field",           ["prewire", "trim", "finish", "rough", "install"]),
    ("admin",           ["admin"]),
    ("drive",           ["drive", "travel", "commute"]),
]

# Time attributable to a job.
PROJECT_BUCKETS = frozenset({
    "field", "shading_install", "shading_pm", "design_eng",
    "production_eng", "project_mgmt", "service", "lighting",
})
# Real time worked, not attributable to any job.
OVERHEAD_BUCKETS = frozenset({"admin", "drive"})
# Capacity that never existed - excluded from utilisation entirely, since
# counting holiday as "unutilised" makes everyone look idle for no reason.
UNAVAILABLE_BUCKETS = frozenset({"time_off", "unpaid_break"})

# Rolled up for role classification only. An engineer splitting time
# between design and production is still an engineer - but the hours stay
# in separate buckets, because design work tracks the sales pipeline while
# production work tracks sold jobs.
ROLLUP = {"design_eng": "engineering", "production_eng": "engineering"}

BUCKET_LABELS = {
    "field": "Field",
    "shading_install": "Shading install",
    "shading_pm": "Shading PM",
    "design_eng": "Design engineering",
    "production_eng": "Production engineering",
    "project_mgmt": "Project management",
    "service": "Service",
    "lighting": "Lighting",
    "admin": "Admin",
    "drive": "Drive",
    "time_off": "Time off",
    "unpaid_break": "Unpaid break",
    "untagged": "Untagged",
}

# Above this share of admin, with almost no project work, a person is
# office staff rather than field capacity. The nine people this catches
# run 83-91% admin; the two it deliberately misses (a shading coordinator
# and a PM) sit at 63-65% with real project hours underneath.
OFFICE_ADMIN_SHARE = 0.75
OFFICE_PROJECT_CEILING = 0.15

DOMINANT = 0.60
DUAL = 0.85


def hours(seconds) -> float:
    """QuickBooks Time reports duration in seconds. 17100 -> 4.75."""
    try:
        return (seconds or 0) / SECONDS_PER_HOUR
    except TypeError:
        return 0.0


def rollup(bucket: str) -> str:
    return ROLLUP.get(bucket, bucket)


def label(bucket: str) -> str:
    return BUCKET_LABELS.get(bucket, bucket.replace("_", " ").title())


def bucket_of(phase: str, jobcode_type: str = "regular") -> str:
    """
    Which bucket an entry belongs to.

    Jobcode type is checked first and wins outright: holiday, vacation and
    lunch carry no phase at all, so classifying on phase alone dumped
    4,658 hours of time off into an "unclassified" pile.
    """
    if jobcode_type == "unpaid_break":
        return "unpaid_break"
    if jobcode_type in ("pto", "unpaid_time_off"):
        return "time_off"

    p = (phase or "").strip().lower()
    for bucket, needles in PHASE_BUCKETS:
        if any(n in p for n in needles):
            return bucket
    return "untagged"


def detect_phase_field(timesheets) -> str:
    """
    Which customfield actually holds the phase, by whichever is populated
    on the most entries. Beats hardcoding an ID that differs per account.
    """
    counts = Counter()
    for ts in timesheets:
        for key, val in (ts.get("customfields") or {}).items():
            if str(val).strip():
                counts[key] += 1
    return counts.most_common(1)[0][0] if counts else DEFAULT_PHASE_FIELD


def split_totals(mix: dict) -> dict:
    """
    Given {bucket: hours}, return the three headline figures.

    worked      = everything except time off and unpaid break
    project     = the part attributable to jobs
    overhead    = admin and drive
    utilisation = project / worked
    """
    worked = sum(h for b, h in mix.items() if b not in UNAVAILABLE_BUCKETS)
    project = sum(h for b, h in mix.items() if b in PROJECT_BUCKETS)
    overhead = sum(h for b, h in mix.items() if b in OVERHEAD_BUCKETS)
    unavailable = sum(h for b, h in mix.items() if b in UNAVAILABLE_BUCKETS)
    return {
        "worked": worked,
        "project": project,
        "overhead": overhead,
        "unavailable": unavailable,
        "untagged": mix.get("untagged", 0.0),
        "utilisation": (project / worked * 100) if worked else 0.0,
    }


def classify_person(mix: dict) -> tuple:
    """
    (role, confidence) from a person's bucket mix.

    Time off is excluded from the denominator first - somebody back from
    two weeks' holiday shouldn't read as half-idle.
    """
    working = {b: h for b, h in mix.items() if b not in UNAVAILABLE_BUCKETS}
    total = sum(working.values())
    if total <= 0:
        return "no-hours", "n/a"

    project = sum(h for b, h in working.items() if b in PROJECT_BUCKETS)
    if (working.get("admin", 0) / total >= OFFICE_ADMIN_SHARE
            and project / total < OFFICE_PROJECT_CEILING):
        return "office", "clear"

    dept = {}
    for b, h in working.items():
        key = rollup(b)
        dept[key] = dept.get(key, 0.0) + h

    shares = sorted(((h / total, d) for d, h in dept.items()), reverse=True)
    top_share, top_dept = shares[0]

    if top_share >= DOMINANT:
        return top_dept, "clear" if top_share >= 0.8 else "likely"
    if len(shares) >= 2 and (shares[0][0] + shares[1][0]) >= DUAL:
        return f"mixed: {shares[0][1]} + {shares[1][1]}", "split"
    return "mixed", "unclear"
