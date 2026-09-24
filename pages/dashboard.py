import calendar
import datetime as dt
import html as html_lib
import re

import streamlit as st

import pandas as pd

from app.services import forward_capacity as fwd
from app.services.capacity_service import CapacityService
from app.services.dashboard_service import DashboardService
from app.services.labor_buckets import (
    OVERHEAD_BUCKETS,
    PROJECT_BUCKETS,
    UNAVAILABLE_BUCKETS,
    label,
)
from app.utils.auth import check_password

import theme

# Categorical palette (fixed order - never cycled/reassigned) from the
# house data-viz palette: colorblind-safe adjacent pairs, validated for a
# light surface. One color per tech, assigned in order of first
# appearance in the fetched events - not by name, so the same tech
# doesn't jump colors just because a different tech happened to get
# fetched first on some other day.
_TECH_COLORS = [
    "#2a78d6",  # blue
    "#eb6834",  # orange
    "#1baf7a",  # aqua
    "#eda100",  # yellow
    "#e87ba4",  # magenta
    "#008300",  # green
    "#4a3aa7",  # violet
    "#e34948",  # red
]
_OVERFLOW_COLOR = "#898781"  # muted gray - for the 9th+ tech, and "Unassigned"


def _format_time(moment) -> str:
    """
    '09:00 AM' -> '9:00 AM'. Not using strftime's '%-I' (no leading zero)
    here because that's a Linux/macOS-only glibc extension - it raises on
    Windows, and this app gets run locally on Windows during development
    as well as deployed on (Linux) Streamlit Community Cloud.
    """
    return moment.strftime("%I:%M %p").lstrip("0")


def _assign_tech_colors(events: list) -> dict:
    """
    tech_name -> hex color, first-seen order. Beyond 8 distinct techs,
    everyone past the 8th shares the same muted gray - per the palette's
    own rule, color is never manufactured past the validated slots, and
    identity still comes through in the visible name text either way, not
    the color alone.
    """
    colors = {}
    for event in events:
        name = event.get("tech_name") or "Unassigned"
        if name not in colors:
            colors[name] = (
                _TECH_COLORS[len(colors)] if len(colors) < len(_TECH_COLORS) else _OVERFLOW_COLOR
            )
    return colors


def _last_day_of_month(year: int, month: int) -> dt.date:
    return dt.date(year, month, calendar.monthrange(year, month)[1])


def _event_time_label(event: dict) -> str:
    """
    'All day' (plus the date span, if it runs more than one day) for an
    all-day job, otherwise the normal start-end time range. All-day events
    still carry a real Start_DateTime/End_DateTime from Zoho (usually
    midnight-to-midnight), so without this a 3-day install would show a
    literal, misleading "12:00 AM" on every card.
    """
    if not event.get("is_all_day"):
        time_str = _format_time(event["start"])
        if event.get("end") and event["end"] != event["start"]:
            time_str += f" – {_format_time(event['end'])}"
        return time_str

    start_date = event["start"].date()
    end_date = (event.get("end") or event["start"]).date()
    if end_date > start_date:
        span = f"{start_date.strftime('%b')} {start_date.day}–{end_date.day}"
        if end_date.month != start_date.month:
            span = f"{start_date.strftime('%b')} {start_date.day} – {end_date.strftime('%b')} {end_date.day}"
        return f"All day ({span})"
    return "All day"


def _event_card_html(event: dict, tech_colors: dict) -> str:
    """
    The visual "card" for one event in the detailed day-by-day list: time,
    a colored tech-name pill, and the full (untruncated) title. Pure
    display - the click target that opens the details modal is a separate,
    real Streamlit button rendered alongside this (raw injected HTML can't
    carry a Streamlit click handler), so this only needs to return markup,
    never wire up interactivity itself.
    """
    title = html_lib.escape(event.get("Event_Title") or "(untitled event)")
    tech_name = event.get("tech_name") or "Unassigned"
    tech = html_lib.escape(tech_name)
    color = tech_colors.get(tech_name, _OVERFLOW_COLOR)
    time_str = html_lib.escape(_event_time_label(event))
    return (
        f'<div class="agenda-event" style="border-left-color:{color}">'
        f'<div class="agenda-event-top">'
        f'<span class="agenda-event-time">{time_str}</span>'
        f'<span class="agenda-event-tech" style="background:{color}">{tech}</span>'
        f"</div>"
        f'<div class="agenda-event-title">{title}</div>'
        f"</div>"
    )


def _day_cell_html(day: dt.date, in_month: bool, today: dt.date, events_by_day: dict, tech_colors: dict, max_chips: int = 3) -> str:
    """
    One day's preview cell: the day number plus up to `max_chips` tiny
    colored chips (one per event, hover for a quick peek at time/title/
    tech) and a "+N more" note beyond that. Pure display - the actual pop-
    out is a real Streamlit button rendered right below this in the same
    grid cell (see _render_month_grid), since raw injected HTML can't
    carry a click handler.
    """
    cell_classes = "ov-day"
    if not in_month:
        # Out-of-month cells are spacers. They get no day number and no View
        # button, so drawing their chips made them look like real days whose
        # button had gone missing - and those days already appear, with their
        # button, in the adjacent month's own grid.
        return '<div class="ov-day ov-day-outside"></div>'
    if day == today:
        cell_classes += " ov-day-today"

    day_events = events_by_day.get(day, [])
    chip_html = ""
    for event in day_events[:max_chips]:
        tech_name = event.get("tech_name") or "Unassigned"
        color = tech_colors.get(tech_name, _OVERFLOW_COLOR)
        title = event.get("Event_Title") or "(untitled event)"
        tooltip = html_lib.escape(f"{_event_time_label(event)} – {title} ({tech_name})")
        chip_html += (
            f'<div class="ov-chip" style="background:{color}" title="{tooltip}">'
            f"{html_lib.escape(title)}</div>"
        )
    overflow = len(day_events) - max_chips
    if overflow > 0:
        chip_html += f'<div class="ov-more">+{overflow} more</div>'

    day_num = str(day.day) if in_month else ""
    return f'<div class="{cell_classes}"><div class="ov-daynum">{day_num}</div>{chip_html}</div>'


@st.dialog("Day Details", width="large")
def _show_day_dialog(day: dt.date, day_events: list) -> None:
    """
    Everything scheduled on one day - opened by clicking that day's cell in
    the two-month overview grid, so a busy day's full job list doesn't have
    to be puzzled out from three truncated chips and a "+N more".
    """
    st.subheader(day.strftime("%A, %B %d, %Y"))

    if not day_events:
        st.info("No jobs scheduled.")
        return

    st.caption("Open a job to see the hours logged against it.")

    for idx, event in enumerate(day_events):
        title = event.get("Event_Title") or "(untitled event)"
        tech_name = event.get("tech_name") or "Unassigned"
        st.write(f"**{_event_time_label(event)}** — {title}")
        st.caption(f"Tech: {tech_name}")
        # A dialog can't open another dialog, so the per-event detail lives
        # in an expander here rather than reusing _show_event_dialog.
        with st.expander("Project details"):
            _render_event_body(
                event,
                key_prefix=f"day_{day.isoformat()}_{idx}",
                show_header=False,
            )
        st.divider()


def _render_month_grid(year: int, month: int, events_by_day: dict, tech_colors: dict, today: dt.date) -> None:
    """
    A compact, classic month grid (weeks starting Sunday) built from real
    Streamlit columns rather than an HTML <table> - the "see everything at
    a glance" companion to the detailed day-by-day list below it. Each day
    is a small preview (up to 3 colored chips + "+N more"), and the cell
    itself pops out the full job list for that day when clicked.
    """
    cal = calendar.Calendar(firstweekday=6)
    weeks = cal.monthdatescalendar(year, month)

    header_cols = st.columns(7)
    for col, label in zip(header_cols, ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")):
        col.caption(label)

    for week in weeks:
        cols = st.columns(7)
        for col, day in zip(cols, week):
            in_month = day.month == month
            with col:
                if not in_month:
                    st.markdown(
                        _day_cell_html(day, in_month, today, events_by_day,
                                       tech_colors),
                        unsafe_allow_html=True,
                    )
                    continue
                day_events = events_by_day.get(day, [])
                with st.container(key=f"daylink-{day.isoformat()}"):
                    st.markdown(
                        _day_cell_html(day, in_month, today, events_by_day,
                                       tech_colors),
                        unsafe_allow_html=True,
                    )
                    if st.button(
                        f"View {len(day_events)}" if day_events else "View",
                        key=f"daylink-{day.isoformat()}-btn",
                        use_container_width=True,
                        help=f"Everything scheduled {day.strftime('%B')} {day.day}",
                    ):
                        _show_day_dialog(day, day_events)


_CALENDAR_CSS = f"""
<style>
.agenda-day-header {{
    position: sticky; top: 0; background: {theme.PAPER}; padding: 10px 0 6px 0;
    margin-top: 14px; border-top: 1px solid {theme.RULE};
    font-family: 'Outfit', sans-serif;
    font-size: 1.02rem; font-weight: 600; color: {theme.INK}; z-index: 1;
    letter-spacing: -0.01em;
}}
.agenda-day-header-today {{ color: {theme.PERI}; }}
.agenda-empty {{ color: {theme.SLATE}; font-size: 0.85rem; padding: 0 0 14px 0; }}
.agenda-event {{
    border-left: 3px solid; border-radius: 7px; padding: 10px 14px; margin: 6px 0 8px 0;
    background: {theme.BONE};
}}
.agenda-event-top {{ display: flex; align-items: center; gap: 10px; margin-bottom: 4px; flex-wrap: wrap; }}
.agenda-event-time {{ font-weight: 600; font-size: 0.9rem; color: {theme.SLATE}; }}
.agenda-event-tech {{
    color: #ffffff; font-size: 0.74rem; font-weight: 600; padding: 3px 10px; border-radius: 10px;
}}
.agenda-event-title {{ font-size: 0.98rem; color: {theme.INK}; line-height: 1.45; }}
.cal-legend {{ margin-top: 10px; margin-bottom: 14px; font-size: 0.82rem; color: {theme.SLATE}; }}
.cal-legend-item {{ display: inline-flex; align-items: center; gap: 5px; margin-right: 16px; }}
.cal-legend-swatch {{ width: 10px; height: 10px; border-radius: 3px; display: inline-block; }}
.ov-month-title {{
    font-family: 'Outfit', sans-serif; font-weight: 600; font-size: 0.98rem;
    margin-bottom: 6px; color: {theme.INK};
}}
.ov-day {{
    border: 1px solid {theme.RULE}; border-radius: 6px; padding: 5px; height: 92px;
    overflow: hidden; margin-bottom: 2px; background: {theme.PAPER};
}}
.ov-day-outside {{ background: {theme.BONE}; }}
.ov-day-outside .ov-daynum {{ color: #C4C0B8; }}
.ov-day-today {{ background: #EFF3FC; border-color: {theme.PERI}; }}
.ov-daynum {{ font-size: 0.76rem; font-weight: 600; color: {theme.SLATE}; margin-bottom: 3px; }}
.ov-chip {{
    color: #ffffff; font-size: 0.65rem; font-weight: 500; padding: 1px 5px; border-radius: 3px;
    margin-bottom: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
.ov-more {{ font-size: 0.65rem; color: {theme.SLATE}; }}

</style>
"""


def _render_event_body(event: dict, key_prefix: str,
                       show_header: bool = True) -> None:
    """
    One event's full detail: the extra Zoho fields, then the hours actually
    logged against the matching jobcode.

    Shared so the standalone Event Details dialog and the per-event expander
    inside Day Details show exactly the same thing - clicking a job in the
    day pop-out shouldn't be a lesser view than clicking it in the list
    below. key_prefix keeps the jobcode selectbox unique when several of
    these render at once inside one day.

    Uses st.write/st.markdown (never unsafe_allow_html), so nothing in Zoho's
    data - a title, description, or contact name someone typed into the CRM -
    can inject raw HTML here.
    """
    title = event.get("Event_Title") or "(untitled event)"
    tech_name = event.get("tech_name") or "Unassigned"
    start = event["start"]

    if show_header:
        st.subheader(f"{start.strftime('%A, %B')} {start.day}, {start.year}")
        st.write(f"**{_event_time_label(event)}** — {title}")
        st.caption(f"Tech: {tech_name}")

    who = event.get("Who_Id")
    what = event.get("What_Id")
    participants = event.get("participant_names") or []
    description = event.get("Description")

    if show_header and any([isinstance(who, dict) and who.get("name"),
                            isinstance(what, dict) and what.get("name"),
                            participants, description]):
        st.divider()

    if isinstance(who, dict) and who.get("name"):
        st.write(f"**Related contact:** {who['name']}")
    if isinstance(what, dict) and what.get("name"):
        st.write(f"**Related to:** {what['name']}")
    if participants:
        st.write(f"**Participants:** {', '.join(participants)}")
    if description:
        st.write("**Notes:**")
        st.write(description)

    # ---- hours logged against this job --------------------------------
    # The point of the link: a scheduled event says what was planned, the
    # timesheets say what it cost. Matching is by street number plus a
    # shared word, since the event says "740 Sanchez Rough In" while the
    # jobcode says "6387 - 740 Sanchez_Av Tech".
    try:
        from app.services.capacity_service import CapacityService

        data = CapacityService().get_project_hours(12)   # months
        if data.get("error"):
            return
        matches = _match_jobs_for_title(title, data["jobs"])
    except Exception:  # noqa: BLE001 - never break the dialog over this
        return

    if show_header:
        st.divider()
    if not matches:
        st.caption(
            "No jobcode matched this event title, so no hours to show. "
            "Matching needs a street number and a word in common with the "
            "jobcode name."
        )
        return

    st.markdown("**Hours logged (last 12 months)**")
    if len(matches) > 1:
        picked_label = st.selectbox(
            "Jobcode",
            [f"{m['name']}  —  {m['total']:,.0f}h" for m in matches],
            key=f"evjob_{key_prefix}",
        )
        job = matches[[f"{m['name']}  —  {m['total']:,.0f}h"
                       for m in matches].index(picked_label)]
    else:
        job = matches[0]
        st.caption(job["name"])

    _render_project_hours(job, data["daily"].get(job["jobcode_id"], []),
                          compact=True, names=data.get("names"),
                          key_prefix=key_prefix)


@st.dialog("Event Details", width="large")
def _show_event_dialog(event: dict) -> None:
    """One event, opened from the day-by-day list below the calendar."""
    _render_event_body(
        event,
        key_prefix=f"dlg_{event.get('id') or (event.get('Event_Title') or '')[:20]}",
    )


def _render_overview(range_start: dt.date, range_end: dt.date, events_by_day: dict, tech_colors: dict, today: dt.date) -> None:
    """
    The "see everything at a glance" companion view: a compact 2-month
    grid, month1 and month2 side by side - a quick skim for volume and
    busy days, with each day popping out its full job list
    (_show_day_dialog) for anyone who doesn't want to scroll the detailed
    list below to find it.
    """
    st.markdown("### Two-month overview")
    st.caption("Click any day to see everything scheduled on it")
    col1, col2 = st.columns(2)
    months_seen = []
    current = dt.date(range_start.year, range_start.month, 1)
    while current <= range_end and len(months_seen) < 2:
        months_seen.append((current.year, current.month))
        current = (
            dt.date(current.year + 1, 1, 1)
            if current.month == 12
            else dt.date(current.year, current.month + 1, 1)
        )

    for col, (year, month) in zip((col1, col2), months_seen):
        with col:
            month_label = f"{calendar.month_name[month]} {year}"
            st.markdown(f'<div class="ov-month-title">{html_lib.escape(month_label)}</div>', unsafe_allow_html=True)
            _render_month_grid(year, month, events_by_day, tech_colors, today)


def _render_day_by_day(display_start: dt.date, range_end: dt.date, events_by_day: dict, tech_colors: dict) -> None:
    """
    One row per calendar day (including empty ones) inside a scrollable
    panel - the "blown up, scrollable, day by day" detail view. Every
    event is shown in full, nothing truncated with a "+N more", and each
    has its own small "Details" button that opens the full-info modal
    (_show_event_dialog) - raw HTML injected via st.markdown can't carry a
    real click handler, so each event card is paired with an actual
    Streamlit button rather than being clickable itself.

    display_start (not necessarily the whole range's start) is where the
    list begins - the "jump to date" picker in _render_calendar_view lets
    someone skip straight to a date instead of scrolling day by day from
    the top, by re-rendering the list starting there.
    """
    today = dt.date.today()

    with st.container(height=700, border=True):
        current = display_start
        while current <= range_end:
            is_today = current == today
            day_label = f"{current.strftime('%A, %B')} {current.day}"
            if is_today:
                day_label += " — Today"
            header_class = "agenda-day-header" + (" agenda-day-header-today" if is_today else "")
            st.markdown(
                f'<div class="{header_class}">{html_lib.escape(day_label)}</div>',
                unsafe_allow_html=True,
            )

            day_events = events_by_day.get(current, [])
            if not day_events:
                st.markdown('<div class="agenda-empty">No jobs scheduled</div>', unsafe_allow_html=True)
            else:
                for idx, event in enumerate(day_events):
                    with st.container(key=f"evlink-{current.isoformat()}-{idx}"):
                        st.markdown(_event_card_html(event, tech_colors),
                                    unsafe_allow_html=True)
                        if st.button(
                            "Details",
                            key=f"evlink-{current.isoformat()}-{idx}-btn",
                        ):
                            _show_event_dialog(event)

            current += dt.timedelta(days=1)


def _render_calendar_view(service: DashboardService,
                          capacity_service=None, person: str = "") -> None:
    st.markdown("## Schedule" + (f" - {person}" if person else ""))
    st.caption("From Zoho CRM's Calendar - this month and next")

    today = dt.date.today()
    month1_year, month1 = today.year, today.month
    month2_year, month2 = (today.year, today.month + 1) if today.month < 12 else (today.year + 1, 1)

    range_start = dt.date(month1_year, month1, 1)
    range_end = _last_day_of_month(month2_year, month2)

    schedule = service.get_calendar_range(range_start, range_end)

    if schedule["error"] and not schedule["events"]:
        st.warning(f"Couldn't load the calendar right now. ({schedule['error']})")
        return

    events = schedule["events"]

    if person and capacity_service is not None:
        events, dropped = _events_for_person(events, capacity_service, person)
        if not events:
            st.info(
                f"Nothing on the calendar matched {person}'s projects. "
                "Events are matched to jobcodes by street number and street "
                "name, so a title with no address in it can't be attributed "
                "to anyone."
            )
            return
        st.caption(
            f"Showing {len(events)} of {len(events) + dropped} events - "
            f"those matching a job {person} has logged time to."
        )

    tech_colors = _assign_tech_colors(events)

    # A multi-day job (all-day or otherwise - e.g. a 3-day install running
    # Sept 5-7) needs to appear on EVERY day it spans, not just the day it
    # starts - otherwise it silently vanishes from the 2nd/3rd day of its
    # own span in both the overview grid and the day-by-day list.
    events_by_day: dict = {}
    for event in events:
        span_start = event["start"].date()
        span_end = (event.get("end") or event["start"]).date()
        # Clip to the displayed window - nothing outside it is ever shown,
        # and this also guards against a bad/garbage End_DateTime turning
        # one event into an unbounded loop.
        day = max(span_start, range_start)
        clipped_end = min(span_end, range_end)
        while day <= clipped_end:
            events_by_day.setdefault(day, []).append(event)
            day += dt.timedelta(days=1)

    st.markdown(_CALENDAR_CSS, unsafe_allow_html=True)

    if tech_colors:
        legend_items = "".join(
            f'<span class="cal-legend-item"><span class="cal-legend-swatch" '
            f'style="background:{color}"></span>{html_lib.escape(name)}</span>'
            for name, color in tech_colors.items()
        )
        st.markdown(f'<div class="cal-legend">{legend_items}</div>', unsafe_allow_html=True)

    _render_overview(range_start, range_end, events_by_day, tech_colors, today)

    st.markdown("### Day by day")
    default_date = today if range_start <= today <= range_end else range_start

    # A "Reset to today" click has to update calendar_jump_date *before*
    # the date_input widget below is instantiated this run - Streamlit
    # raises if session_state for a widget's key is written after that
    # widget has already been created in the same script run. So the
    # button just sets a plain flag and reruns; this block, which runs
    # before the widget exists yet, is what actually applies the reset.
    if st.session_state.pop("_reset_calendar_jump", False):
        st.session_state["calendar_jump_date"] = default_date
    # Passing both `value=` and touching session_state for the same
    # widget key logs a Streamlit warning even when they agree - so the
    # initial default is seeded into session_state once here instead of
    # passed as `value=` below, and every later run is driven purely by
    # session_state (the widget's own persistence, or the reset above).
    if "calendar_jump_date" not in st.session_state:
        st.session_state["calendar_jump_date"] = default_date

    jump_col, reset_col = st.columns([3, 1], vertical_alignment="bottom")
    with jump_col:
        display_start = st.date_input(
            "Jump to date",
            min_value=range_start,
            max_value=range_end,
            key="calendar_jump_date",
        )
    with reset_col:
        if st.button("Reset to today", use_container_width=True):
            st.session_state["_reset_calendar_jump"] = True
            st.rerun()

    st.caption("Scroll for more - click any job to see its full details")
    _render_day_by_day(display_start, range_end, events_by_day, tech_colors)

    if not events and not schedule["error"]:
        st.info("Nothing on the calendar in either month.")

    if schedule["error"]:
        st.caption(f"Note: showing possibly-incomplete data - last refresh had an error ({schedule['error']})")


# Months, not days: the snapshot stores per-person hours by month, so a
# shorter window than one month can't be computed from it.
_WINDOW_CHOICES = {
    "This month": 1,
    "Last 3 months": 3,
    "Last 6 months": 6,
    "Last 12 months": 12,
    "Everything": 0,
}


def _bucket_kind(bucket: str) -> str:
    if bucket in PROJECT_BUCKETS:
        return "Project"
    if bucket in OVERHEAD_BUCKETS:
        return "Overhead"
    if bucket in UNAVAILABLE_BUCKETS:
        return "Unavailable"
    return "Untagged"


# ---------------------------------------------------------------------------
# Project drill-in
# ---------------------------------------------------------------------------

_STOP = {
    "the", "and", "svc", "service", "services", "av", "tech", "net", "system",
    "systems", "update", "updates", "upgrade", "install", "installation",
    "project", "rough", "trim", "final", "finish", "prewire", "in", "phase",
    "do", "not", "use", "st", "ave", "rd", "dr", "ln", "ct", "blvd", "way",
    "shade", "shades", "lighting", "design", "support", "walk", "review",
}


def _tokens(text: str) -> tuple:
    """(street numbers, meaningful words) for loose name comparison."""
    cleaned = re.sub(r"[^a-z0-9]+", " ", (text or "").lower())
    parts = cleaned.split()
    nums = {p for p in parts if p.isdigit()}
    words = {p for p in parts if not p.isdigit() and len(p) > 2 and p not in _STOP}
    return nums, words


def _events_for_person(events: list, capacity_service, person: str) -> tuple:
    """
    (kept, dropped) - events whose title matches a job this person works.

    Zoho titles and QuickBooks Time jobcode names describe the same site
    differently ("740 Sanchez Rough In" vs "6387 - 740 Sanchez_Av Tech"),
    so matching needs a shared street number AND a shared word. The number
    alone isn't enough - "2700 Redwolf" and "2700 Pierce" are different
    sites.
    """
    try:
        mine = capacity_service.get_project_hours(12, person=person)
    except Exception as exc:  # noqa: BLE001
        # Falling back to every event is the safe behaviour, but doing it
        # silently made a KeyError look like "the filter isn't working".
        st.warning(f"Couldn't filter the calendar to {person}: {exc}")
        return events, 0
    jobs = mine.get("jobs", [])
    if not jobs:
        return [], len(events)

    kept = [e for e in events
            if _match_jobs_for_title(e.get("Event_Title") or "", jobs)]
    return kept, len(events) - len(kept)


def _match_jobs_for_title(title: str, jobs: list) -> list:
    """
    Jobcodes plausibly matching a calendar event title.

    A Zoho event reads "740 Sanchez Rough In" while the jobcode reads
    "6387 - 740 Sanchez_Av Tech" - so matching is anchored on a shared
    street number AND a shared word. The number alone is not enough:
    "2700 Redwolf" and "2700 Pierce" share 2700 and are different sites.
    """
    t_nums, t_words = _tokens(title)
    if not t_nums or not t_words:
        return []
    hits = []
    for job in jobs:
        # strip the leading job number so it can't be read as a street number
        name = re.sub(r"^\s*\(?(?:DO NOT USE\)?\s*)?#?\d{3,6}\s*[-,:]\s*", "",
                      job["name"], flags=re.IGNORECASE)
        j_nums, j_words = _tokens(name)
        if (t_nums & j_nums) and (t_words & j_words):
            hits.append(job)
    hits.sort(key=lambda j: -j["total"])
    return hits


_WHO_COLUMNS = ["rough", "trim", "final", "prog", "shade", "light", "pm",
                "eng", "travel", "admin", "service", "other"]


def _render_day_attribution(row: dict, names: dict) -> None:
    """
    Who logged the hours on one day of one job.

    Reads the per-day "who" map added in snapshot schema 2. Older snapshots
    don't have it, which is not an error - it just means the breakdown isn't
    available until the next nightly build.
    """
    who = row.get("who") or {}
    if not who:
        st.caption(
            "No per-person breakdown for this day. That detail starts with "
            "the next nightly snapshot build."
        )
        return

    present = [c for c in _WHO_COLUMNS
               if any(slots.get(c) for slots in who.values())]
    table = [
        {
            "Person": names.get(str(uid), f"(user {uid})"),
            **{c: round(slots.get(c, 0.0), 2) for c in present},
            "Total": round(sum(slots.values()), 2),
        }
        for uid, slots in who.items()
    ]
    table.sort(key=lambda r: -r["Total"])
    st.dataframe(
        pd.DataFrame(table), hide_index=True, use_container_width=True,
        column_config={
            c: st.column_config.NumberColumn(format="%.2f")
            for c in present + ["Total"]
        },
    )


def _render_project_hours(job: dict, rows: list, compact: bool = False,
                          names: dict | None = None,
                          key_prefix: str = "proj") -> None:
    """Totals and the daily rough/trim/final breakdown for one jobcode."""
    theme.kpi_row([
        dict(label="Total hours", value=f"{job['total']:,.1f}", unit="h",
             accent=theme.INK),
        dict(label="Rough", value=f"{job['rough']:,.1f}", unit="h",
             accent=theme.PERI),
        dict(label="Trim", value=f"{job['trim']:,.1f}", unit="h",
             accent=theme.SAND),
        dict(label="Final", value=f"{job['final']:,.1f}", unit="h",
             accent=theme.GREEN),
    ])

    if job["other"] > 0:
        st.caption(
            f"{job['other']:,.1f}h logged to this job outside rough/trim/final "
            "(PM, engineering, service, shading, admin)."
        )

    if not rows:
        st.info("No daily entries in this window.")
        return

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])

    # Same reason as the phase totals in capacity_service: a daily row can
    # omit a phase key, and if no row in this job carries one the column
    # never exists. Seed all four so selection and charting can't fail.
    for phase in ("rough", "trim", "final", "other"):
        if phase not in df.columns:
            df[phase] = 0.0
    df[["rough", "trim", "final", "other"]] = (
        df[["rough", "trim", "final", "other"]].fillna(0.0)
    )
    if "total" not in df.columns:
        df["total"] = df[["rough", "trim", "final", "other"]].sum(axis=1)

    chart = df.set_index("date")[["rough", "trim", "final"]]
    if chart.to_numpy().sum() > 0:
        theme.show(
            theme.stacked_bar(
                chart.index,
                {"Rough": chart["rough"], "Trim": chart["trim"],
                 "Final": chart["final"]},
                colors=[theme.PERI, theme.SAND, theme.GREEN],
                height=220 if compact else 300,
            ),
            # Two events on the same day can match the same jobcode, which
            # draws byte-identical figures; without a key Streamlit treats
            # them as one element and raises DuplicateElementId.
            key=f"hourschart_{key_prefix}_{job['jobcode_id']}",
        )
    else:
        st.caption("No rough/trim/final hours - all time on this job is "
                   "other work.")

    shown = (df[["date", "rough", "trim", "final", "other", "total"]]
               .sort_values("date", ascending=False)
               .reset_index(drop=True))
    picked = st.dataframe(
        shown,
        hide_index=True,
        use_container_width=True,
        column_config={
            "date": st.column_config.DateColumn("Date", format="ddd DD MMM YYYY"),
            "rough": st.column_config.NumberColumn(format="%.2f"),
            "trim": st.column_config.NumberColumn(format="%.2f"),
            "final": st.column_config.NumberColumn(format="%.2f"),
            "other": st.column_config.NumberColumn(format="%.2f"),
            "total": st.column_config.NumberColumn(format="%.2f"),
        },
        height=260 if compact else 420,
        # Streamlit selects rows, not cells - so picking the day gives the
        # whole day's split by person rather than just the one figure that
        # was clicked, which is more useful anyway.
        on_select="rerun",
        selection_mode="single-row",
        key=f"dayrows_{key_prefix}_{job['jobcode_id']}",
    )

    picked_rows = getattr(getattr(picked, "selection", None), "rows", None) or []
    if picked_rows:
        idx = picked_rows[0]
        day_value = shown.iloc[idx]["date"]
        day_str = pd.to_datetime(day_value).strftime("%Y-%m-%d")
        source = next((r for r in rows if str(r.get("date")) == day_str), None)
        st.markdown(f"**Who logged these hours — "
                    f"{pd.to_datetime(day_value):%a %d %b %Y}**")
        _render_day_attribution(source or {}, names or {})
    else:
        st.caption("Select a day above to see who logged its hours.")

    if job["people"]:
        st.caption("Worked by: " + ", ".join(job["people"]))


def _render_projects_section(capacity_service, person: str = "") -> None:
    """Pick a job, see its daily hours split rough / trim / final."""
    st.markdown("## Hours by project")

    window = st.selectbox(
        "History",
        list(_WINDOW_CHOICES.keys()),
        index=1,
        key="proj_window",
        help="All of this comes from the same nightly snapshot, so a longer "
             "window costs nothing.",
    )

    data = capacity_service.get_project_hours(_WINDOW_CHOICES[window],
                                              person=person)
    if data["error"]:
        st.warning(f"Couldn't read project hours. ({data['error']})")
        return
    if not data["jobs"]:
        st.info("No project hours in this window.")
        return

    jobs = data["jobs"]
    only_install = st.checkbox(
        "Only jobs with rough/trim/final hours", value=False,
        help="Jobs are already sorted with the busiest installs first. "
             "Tick this to hide jobs that are all PM, engineering or service.",
    )
    shown = [j for j in jobs
             if not only_install or j.get("install_total", 0) > 0]
    if not shown:
        st.info("No jobs with install hours in this window.")
        return
    st.caption(f"{len(shown):,} jobs with hours in this window "
               "(Admin, Drive, Holiday and similar are excluded)")

    labels = {
        f"{j['name']}  —  {j['total']:,.0f}h": j["jobcode_id"] for j in shown
    }
    picked_label = st.selectbox("Project", list(labels.keys()), key="proj_pick")
    picked_id = labels[picked_label]
    job = next(j for j in shown if j["jobcode_id"] == picked_id)

    st.caption(
        f"{job['name']}"
        + (f"  ·  job {job['job_number']}" if job["job_number"] else "")
        + (f"  ·  {job['first_day']} to {job['last_day']}"
           if job["first_day"] else "")
        + ("" if job["active"] else "  ·  INACTIVE")
    )

    _render_project_hours(job, data["daily"].get(picked_id, []),
                          names=data.get("names"), key_prefix="section")

    # Other jobcodes on the same site - a job usually has several, split by
    # vertical, and a PM thinks of them as one project.
    if job["job_number"]:
        nums, words = _tokens(re.sub(
            r"^\s*\(?(?:DO NOT USE\)?\s*)?#?\d{3,6}\s*[-,:]\s*", "",
            job["name"], flags=re.IGNORECASE))
        siblings = []
        for other in jobs:
            if other["jobcode_id"] == picked_id:
                continue
            o_nums, o_words = _tokens(re.sub(
                r"^\s*\(?(?:DO NOT USE\)?\s*)?#?\d{3,6}\s*[-,:]\s*", "",
                other["name"], flags=re.IGNORECASE))
            if (nums & o_nums) and (words & o_words):
                siblings.append(other)
        if siblings:
            with st.expander(
                f"Other jobcodes at this site ({len(siblings)}) — "
                f"{sum(s['total'] for s in siblings):,.0f}h more"
            ):
                st.dataframe(
                    pd.DataFrame([
                        {"Jobcode": s["name"], "Total": s["total"],
                         "Rough": s["rough"], "Trim": s["trim"],
                         "Final": s["final"]}
                        for s in siblings
                    ]),
                    hide_index=True, use_container_width=True,
                )
                st.caption(
                    "Sites usually carry several jobcodes split by vertical "
                    "(AV, shades, lighting). Budget comparisons should use "
                    "the site total, not one code."
                )


def _render_capacity_view(capacity_service, person: str = "") -> None:
    """
    Where the hours actually went.

    Sourced entirely from QuickBooks Time, because Zoho holds no time at
    all - every cost field comes back empty and percent-complete can't be
    trusted (901 Seabury Phase 2 sat at 0% with 298 hours logged against
    it). Hours are the only record of what really happened.
    """
    st.markdown("## Capacity")

    ctrl_left, ctrl_right = st.columns([3, 1], vertical_alignment="bottom")
    with ctrl_left:
        window_label = st.selectbox(
            "Window",
            list(_WINDOW_CHOICES.keys()),
            index=1,
            help="Longer windows take about a minute to pull the first "
                 "time, then they're cached for an hour.",
        )
    with ctrl_right:
        if st.button("Reload", use_container_width=True,
                     help="Re-read the snapshot file. To pull fresh hours "
                          "from QuickBooks Time, run the nightly build."):
            capacity_service.clear_cache()
            st.rerun()

    status = capacity_service.status()
    if not status["ok"]:
        st.warning(
            f"No snapshot available. ({status['error']})\n\n"
            "The dashboard reads a nightly snapshot rather than calling "
            "QuickBooks Time directly. Build one with:\n\n"
            "`python scripts/build_snapshot.py --months 24 --local-only`"
        )
        return

    data = capacity_service.get_capacity(_WINDOW_CHOICES[window_label])

    if data.get("error"):
        st.warning(f"Couldn't read the snapshot. ({data['error']})")
        return
    if data.get("note"):
        st.info(data["note"])

    if not data["people"] and not data["office"]:
        st.info("No timesheet entries in this window.")
        return

    w = status["window"] or {}
    st.caption(
        f"Snapshot {status['age']}"
        + (f" · {status['entries']:,} entries" if status.get("entries") else "")
        + (f" · covering {w.get('start')} to {w.get('end')}" if w else "")
        + f" · read from {status['source']}"
    )

    totals = data["totals"]
    worked = totals["worked"] or 1

    project_share = totals["project"] / worked * 100
    overhead_share = totals["overhead"] / worked * 100

    theme.kpi_row([
        dict(label="Hours worked", value=f"{totals['worked']:,.0f}",
             unit="h", accent=theme.PERI),
        dict(label="On projects", value=f"{totals['project']:,.0f}", unit="h",
             note=f"{project_share:.1f}% of worked", note_tone="up",
             accent=theme.GREEN),
        dict(label="Overhead", value=f"{totals['overhead']:,.0f}", unit="h",
             note=f"{overhead_share:.1f}% of worked", note_tone="down",
             accent=theme.CORAL),
        dict(label="Capacity staff", value=len(data["people"]),
             note=f"{len(data['office'])} office excluded", accent=theme.SLATE),
    ])

    if totals["untagged"] > 0:
        st.caption(
            f"{totals['untagged']:,.0f}h carry no phase tag, so they aren't "
            "counted as project work. Worth chasing if that number is large."
        )

    st.write("")

    # ---- where the hours went -------------------------------------------
    st.markdown("## Where the hours went")
    bucket_total = sum(data["buckets"].values()) or 1

    with st.container(border=True):
        kind_totals = {}
        for b, h in data["buckets"].items():
            kind_totals[_bucket_kind(b)] = kind_totals.get(_bucket_kind(b), 0) + h
        order = ["Project", "Overhead", "Unavailable", "Untagged"]
        present = [k for k in order if kind_totals.get(k)]
        ring_left, ring_right = st.columns([1, 1.5], gap="large")
        with ring_left:
            st.markdown("### By kind")
            theme.show(theme.donut(
                present,
                [kind_totals[k] for k in present],
                center_value=f"{bucket_total:,.0f}h",
                center_label="logged",
                colors=[{"Project": theme.GREEN, "Overhead": theme.CORAL,
                         "Unavailable": theme.SLATE,
                         "Untagged": theme.ROSE}[k] for k in present],
            ))
        with ring_right:
            st.markdown("### Biggest buckets")
            top = sorted(data["buckets"].items(), key=lambda kv: -kv[1])[:6]
            theme.show(theme.hbar([label(b) for b, _ in top],
                                  [h for _, h in top], suffix="h"))

    bucket_rows = [
        {
            "Bucket": label(b),
            "Hours": round(h, 1),
            "Share": h / bucket_total * 100,
            "Kind": _bucket_kind(b),
        }
        for b, h in sorted(data["buckets"].items(), key=lambda kv: -kv[1])
    ]
    with st.container(border=True):
        st.markdown("### Every bucket")
        st.dataframe(
            pd.DataFrame(bucket_rows),
            hide_index=True,
            use_container_width=True,
            column_config={
                "Hours": st.column_config.NumberColumn(format="%.1f"),
                "Share": st.column_config.ProgressColumn(
                    "Share", format="%.1f%%", min_value=0, max_value=100),
            },
        )
    st.caption(
        "Time off and unpaid break are excluded from utilisation entirely - "
        "capacity that never existed, rather than capacity that went unused."
    )

    st.write("")

    # ---- people ----------------------------------------------------------
    st.markdown("## People")
    roles = sorted({p["role"] for p in data["people"]})
    chosen = st.multiselect("Filter by role", roles, default=roles)

    people_rows = [
        {
            "Name": p["name"],
            "Role": p["role"],
            "Worked": p["worked"],
            "On projects": p["project"],
            "Utilisation": p["utilisation"],
            "Jobs": p["jobs"],
            "Confidence": p["confidence"],
        }
        for p in data["people"] if p["role"] in chosen
    ]

    if people_rows:
        with st.container(border=True):
            st.dataframe(
                pd.DataFrame(people_rows),
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Worked": st.column_config.NumberColumn(format="%.1f"),
                    "On projects": st.column_config.NumberColumn(format="%.1f"),
                    "Utilisation": st.column_config.ProgressColumn(
                        "Utilisation", format="%.1f%%", min_value=0, max_value=100),
                },
            )
        st.caption(
            "Utilisation is project hours divided by hours worked. A low "
            "figure means time went to admin or drive - not that someone "
            "was idle."
        )
    else:
        st.info("No roles selected.")

    if data["office"]:
        office_hours = sum(p["worked"] for p in data["office"])
        with st.expander(
            f"Office staff — {len(data['office'])} people, "
            f"{office_hours:,.0f}h (excluded from capacity)"
        ):
            st.dataframe(
                pd.DataFrame([
                    {"Name": p["name"], "Worked": p["worked"],
                     "Admin + drive": p["overhead"]}
                    for p in data["office"]
                ]),
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Worked": st.column_config.NumberColumn(format="%.1f"),
                    "Admin + drive": st.column_config.NumberColumn(format="%.1f"),
                },
            )
            st.caption(
                "Almost all of their time is Admin with no project phase. "
                "Counting them dragged company-wide utilisation down without "
                "saying anything about field capacity."
            )

    st.divider()
    _render_projects_section(capacity_service, person)


_FWD_FREE = "#dcefe3"
_FWD_BUSY = "#b9dfc6"
_FWD_FULL = "#fbe6a6"
_FWD_CONFLICT = "#f4c2c2"
_FWD_OFF = "#e7e5e0"


def _non_field_names(capacity_service) -> set:
    """
    Office, PM and engineering staff, taken from how they actually log time
    rather than a hand-kept list. They turn up on the calendar now and then
    but aren't field capacity, and would clutter the grid with empty rows.
    """
    try:
        cap = capacity_service.get_capacity(3)
    except Exception:  # noqa: BLE001
        return set()
    out = {p["name"] for p in cap.get("office", [])}
    out |= {p["name"] for p in cap.get("people", [])
            if p.get("role") in ("project_mgmt", "engineering", "office")}
    return out


def _render_forward_view(capacity_service) -> None:
    """
    Who's booked, who's free and who's double-booked, from the calendar.

    Trusted because the calendar held 95-115% of what field techs actually
    logged once participants are counted alongside the lead tech - see
    scripts/schedule_coverage.py.
    """
    st.markdown("## Forward capacity")
    st.caption("From the Zoho calendar - lead tech and crew both counted. "
               "Time off lowers capacity; it isn't booked work.")

    c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
    with c1:
        weeks = st.select_slider("Weeks ahead", options=[2, 4, 6, 8, 12],
                                 value=8, key="fwd_weeks")
    with c2:
        if st.button("Reload", key="fwd_reload", use_container_width=True):
            fwd.clear_cache()
            st.rerun()

    data = fwd.forward_capacity(weeks, exclude=_non_field_names(capacity_service))
    if data["error"] and not data["people"]:
        st.warning(f"Couldn't read the calendar. ({data['error']})")
        return
    if not data["people"]:
        st.info("Nothing scheduled in this window.")
        return

    mondays = data["weeks"]
    rows = data["people"]
    conflicts = data["conflicts"]

    total_b = sum(r["booked_total"] for r in rows)
    total_a = sum(r["available_total"] for r in rows)
    free_2w = sum(r["free_next_2w"] for r in rows)
    theme.kpi_row([
        dict(label="Field staff", value=len(rows), accent=theme.SLATE),
        dict(label="Booked",
             value=f"{total_b / total_a * 100:.0f}%" if total_a else "-",
             accent=theme.PERI),
        dict(label="Free, next 2 weeks", value=f"{free_2w:,.0f}h",
             accent=theme.GREEN),
        dict(label="Double-bookings", value=len(conflicts), accent=theme.CORAL),
    ])

    # ---- heatmap ---------------------------------------------------------
    st.markdown("###### Booked, by week")
    labels = [f"{m:%b %d}" for m in mondays]
    text, color = {}, {}
    for r in rows:
        t_row, c_row = [], []
        for m in mondays:
            w = r["weeks"][m]
            if w["pct"] is None:
                t_row.append("off")
                c_row.append(_FWD_OFF)
                continue
            cell = f"{w['pct']}%"
            if w["conflict_days"]:
                cell += f" !{w['conflict_days']}"
                c_row.append(_FWD_CONFLICT)
            elif w["pct"] >= 90:
                c_row.append(_FWD_FULL)
            elif w["pct"] >= 60:
                c_row.append(_FWD_BUSY)
            else:
                c_row.append(_FWD_FREE)
            t_row.append(cell)
        text[r["name"]] = t_row
        color[r["name"]] = c_row

    grid = pd.DataFrame.from_dict(text, orient="index", columns=labels)
    shades = pd.DataFrame.from_dict(color, orient="index", columns=labels)
    grid.index.name = "Tech"
    styled = grid.style.apply(
        lambda _: shades.map(lambda c: f"background-color:{c}; color:#1f2328"),
        axis=None)
    st.dataframe(styled, use_container_width=True,
                 height=min(38 * len(rows) + 40, 720))
    st.caption("Green = room to add work. Amber = 90%+ booked. "
               "Red with ! = double-booked on that many days. "
               "Grey = off all week.")

    left, right = st.columns(2)

    # ---- open capacity ---------------------------------------------------
    with left:
        st.markdown("###### Open capacity, next 2 weeks")
        free = sorted((r for r in rows if r["free_next_2w"] >= 8),
                      key=lambda r: -r["free_next_2w"])
        if free:
            st.dataframe(
                pd.DataFrame([{"Tech": r["name"],
                               "Free hours": r["free_next_2w"],
                               "Free days": round(r["free_next_2w"]
                                                  / fwd.WORKDAY_HOURS, 1)}
                              for r in free]),
                hide_index=True, use_container_width=True,
                column_config={"Free hours":
                               st.column_config.NumberColumn(format="%.0f")})
        else:
            st.caption("Nobody has a full free day in the next two weeks.")

    # ---- conflicts -------------------------------------------------------
    with right:
        st.markdown("###### Double-bookings")
        if conflicts:
            st.dataframe(
                pd.DataFrame([{"Date": c["date"], "Tech": c["person"],
                               "Booked": c["hours"],
                               "Jobs": " + ".join(c["jobs"])}
                              for c in conflicts]),
                hide_index=True, use_container_width=True,
                column_config={
                    "Date": st.column_config.DateColumn(format="ddd MMM D"),
                    "Booked": st.column_config.NumberColumn(format="%.1fh")})
            st.caption("Someone on two jobs the same day. Fix these "
                       "before the day arrives.")
        else:
            st.caption("No double-bookings in this window.")


def render():
    """
    Dashboard page.
    """
    # Called here as well as in main.py because pages/ makes this a Streamlit
    # multipage app: opening "dashboard" from the sidebar runs this file
    # directly and never touches main.py. Injecting the CSS twice in one run
    # is harmless; not injecting it at all leaves the page unstyled.
    theme.apply_theme()

    service = DashboardService()
    capacity_service = CapacityService()
    kpis = service.get_kpis()

    theme.page_title(
        "Operations Command Center",
        "Live from Zoho CRM" if kpis["source"] == "zoho"
        else "Showing the last cached pull - Zoho didn't answer just now",
    )

    theme.kpi_row([
        dict(label="Projects", value=kpis["projects"], accent=theme.PERI),
        dict(label="Tasks", value=kpis["tasks"], accent=theme.GREEN),
        dict(label="Cases", value=kpis["cases"], accent=theme.CORAL),
        dict(label="CRM users", value=kpis["users"], accent=theme.SLATE),
    ])

    if kpis["source"] != "zoho" and kpis.get("error"):
        st.caption(f"Zoho error: {kpis['error']}")

    st.write("")

    # One dashboard, two scopes. A personal view is the same data filtered,
    # not a separate app - so the team view can't drift from it.
    names = capacity_service.people_names()
    choice = st.selectbox(
        "View",
        ["Whole team"] + names,
        key="view_scope",
        help="Picking a name narrows the schedule and the project list to "
             "the jobs that person has logged time to.",
    )
    person = "" if choice == "Whole team" else choice

    schedule_tab, forward_tab, capacity_tab = st.tabs(
        ["Schedule", "Forward capacity", "Capacity"])

    with schedule_tab:
        _render_calendar_view(service, capacity_service, person)

    with forward_tab:
        _render_forward_view(capacity_service)

    with capacity_tab:
        _render_capacity_view(capacity_service, person)


if __name__ == "__main__":
    st.set_page_config(
        page_title="Capacity Planner",
        page_icon="🏠",
        layout="wide",
    )
    theme.apply_theme()
    if check_password():
        render()
    else:
        st.stop()
