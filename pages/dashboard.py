import calendar
import datetime as dt
import html as html_lib
import re

import streamlit as st

import pandas as pd

from app.services.capacity_service import CapacityService
from app.services.dashboard_service import DashboardService
from app.services.labor_buckets import (
    OVERHEAD_BUCKETS,
    PROJECT_BUCKETS,
    UNAVAILABLE_BUCKETS,
    label,
)
from app.utils.auth import check_password

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
        cell_classes += " ov-day-outside"
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


@st.dialog("Day Details")
def _show_day_dialog(day: dt.date, day_events: list) -> None:
    """
    Everything scheduled on one day - opened by clicking a day's "🔍 N"
    button in the two-month overview grid, so a busy day's full job list
    doesn't have to be puzzled out from three truncated chips and a
    "+N more".
    """
    st.subheader(day.strftime("%A, %B %d, %Y"))

    if not day_events:
        st.info("No jobs scheduled.")
        return

    for event in day_events:
        title = event.get("Event_Title") or "(untitled event)"
        tech_name = event.get("tech_name") or "Unassigned"
        st.write(f"**{_event_time_label(event)}** — {title}")
        st.caption(f"Tech: {tech_name}")
        st.divider()


def _render_month_grid(year: int, month: int, events_by_day: dict, tech_colors: dict, today: dt.date) -> None:
    """
    A compact, classic month grid (weeks starting Sunday) built from real
    Streamlit columns rather than an HTML <table> - the "see everything at
    a glance" companion to the detailed day-by-day list below it. Each day
    is a small preview (up to 3 colored chips + "+N more") with its own
    "🔍" button that pops out the full job list for that day.
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
                st.markdown(
                    _day_cell_html(day, in_month, today, events_by_day, tech_colors),
                    unsafe_allow_html=True,
                )
                if in_month:
                    day_events = events_by_day.get(day, [])
                    label = f"🔍 {len(day_events)}" if day_events else "🔍"
                    if st.button(
                        label,
                        key=f"ovday_{day.isoformat()}",
                        help="View everything scheduled this day",
                        use_container_width=True,
                    ):
                        _show_day_dialog(day, day_events)


_CALENDAR_CSS = """
<style>
.agenda-day-header {
    position: sticky; top: 0; background: #fcfcfb; padding: 10px 0 6px 0;
    margin-top: 14px; border-top: 1px solid #e1e0d9;
    font-size: 1.1rem; font-weight: 700; color: #0b0b0b; z-index: 1;
}
.agenda-day-header-today { color: #2a78d6; }
.agenda-empty { color: #898781; font-style: italic; font-size: 0.85rem; padding: 0 0 14px 0; }
.agenda-event {
    border-left: 4px solid; border-radius: 6px; padding: 10px 14px; margin: 6px 0 8px 0;
    background: #f9f9f7;
}
.agenda-event-top { display: flex; align-items: center; gap: 10px; margin-bottom: 4px; flex-wrap: wrap; }
.agenda-event-time { font-weight: 700; font-size: 0.95rem; color: #52514e; }
.agenda-event-tech {
    color: #ffffff; font-size: 0.78rem; font-weight: 700; padding: 3px 10px; border-radius: 10px;
}
.agenda-event-title { font-size: 1.05rem; color: #0b0b0b; line-height: 1.4; }
.cal-legend { margin-top: 10px; margin-bottom: 12px; font-size: 0.85rem; }
.cal-legend-item { display: inline-flex; align-items: center; gap: 5px; margin-right: 16px; }
.cal-legend-swatch { width: 11px; height: 11px; border-radius: 3px; display: inline-block; }
.ov-month-title { font-weight: 700; font-size: 1rem; margin-bottom: 2px; color: #0b0b0b; }
.ov-day {
    border: 1px solid #e1e0d9; border-radius: 4px; padding: 4px; height: 92px; overflow: hidden;
    margin-bottom: 2px;
}
.ov-day-outside { background: #f9f9f7; }
.ov-day-outside .ov-daynum { color: #c3c2b7; }
.ov-day-today { background: #eaf2fd; border-color: #2a78d6; }
.ov-daynum { font-size: 0.78rem; font-weight: 700; color: #52514e; margin-bottom: 2px; }
.ov-chip {
    color: #ffffff; font-size: 0.66rem; font-weight: 600; padding: 1px 4px; border-radius: 3px;
    margin-bottom: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.ov-more { font-size: 0.65rem; color: #898781; font-style: italic; }
@media (prefers-color-scheme: dark) {
    .agenda-day-header { background: #1a1a19; color: #ffffff; border-top-color: #2c2c2a; }
    .agenda-day-header-today { color: #3987e5; }
    .agenda-event { background: #0d0d0d; }
    .agenda-event-time { color: #c3c2b7; }
    .agenda-event-title { color: #ffffff; }
    .ov-month-title { color: #ffffff; }
    .ov-day { border-color: #2c2c2a; }
    .ov-day-outside { background: #0d0d0d; }
    .ov-day-today { background: #16283f; border-color: #3987e5; }
    .ov-daynum { color: #c3c2b7; }
}
</style>
"""


@st.dialog("Event Details")
def _show_event_dialog(event: dict) -> None:
    """
    Full info for one calendar event, opened by clicking its "Details"
    button in the day-by-day list. Uses st.write/st.markdown (never
    unsafe_allow_html) throughout, so nothing in Zoho's data - a title,
    description, or contact name someone typed into the CRM - can inject
    raw HTML here.
    """
    title = event.get("Event_Title") or "(untitled event)"
    st.subheader(title)

    tech_name = event.get("tech_name") or "Unassigned"
    st.write(f"**Tech:** {tech_name}")

    start = event["start"]
    date_str = f"{start.strftime('%A, %B')} {start.day}, {start.year}"
    st.write(f"**When:** {date_str}, {_event_time_label(event)}")

    who = event.get("Who_Id")
    if isinstance(who, dict) and who.get("name"):
        st.write(f"**Related contact:** {who['name']}")

    what = event.get("What_Id")
    if isinstance(what, dict) and what.get("name"):
        st.write(f"**Related to:** {what['name']}")

    participants = event.get("participant_names") or []
    if participants:
        st.write(f"**Participants:** {', '.join(participants)}")

    description = event.get("Description")
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
            key=f"evjob_{event.get('id') or title[:20]}",
        )
        job = matches[[f"{m['name']}  —  {m['total']:,.0f}h"
                       for m in matches].index(picked_label)]
    else:
        job = matches[0]
        st.caption(job["name"])

    _render_project_hours(job, data["daily"].get(job["jobcode_id"], []),
                          compact=True)


def _render_overview(range_start: dt.date, range_end: dt.date, events_by_day: dict, tech_colors: dict, today: dt.date) -> None:
    """
    The "see everything at a glance" companion view: a compact 2-month
    grid, month1 and month2 side by side - a quick skim for volume and
    busy days, with each day's "🔍" button popping out its full job list
    (_show_day_dialog) for anyone who doesn't want to scroll the detailed
    list below to find it.
    """
    st.markdown("###### Two-Month Overview")
    st.caption("Click 🔍 on any day to see everything scheduled that day")
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

    with st.container(height=700):
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
                    card_col, btn_col = st.columns([8, 1], vertical_alignment="center")
                    with card_col:
                        st.markdown(_event_card_html(event, tech_colors), unsafe_allow_html=True)
                    with btn_col:
                        button_key = f"ev_{current.isoformat()}_{idx}_{event.get('id') or ''}"
                        if st.button("🔍", key=button_key, help="View details", use_container_width=True):
                            _show_event_dialog(event)

            current += dt.timedelta(days=1)


def _render_calendar_view(service: DashboardService) -> None:
    st.subheader("📅 Schedule")
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

    st.markdown("###### Day-by-Day Detail")
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

    st.caption("Scroll for more - click 🔍 on any job to see its full details")
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


def _render_project_hours(job: dict, rows: list, compact: bool = False) -> None:
    """Totals and the daily rough/trim/final breakdown for one jobcode."""
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total hours", f"{job['total']:,.1f}")
    c2.metric("Rough", f"{job['rough']:,.1f}")
    c3.metric("Trim", f"{job['trim']:,.1f}")
    c4.metric("Final", f"{job['final']:,.1f}")

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

    chart = df.set_index("date")[["rough", "trim", "final"]]
    if chart.to_numpy().sum() > 0:
        st.bar_chart(chart, height=220 if compact else 300)
    else:
        st.caption("No rough/trim/final hours - all time on this job is "
                   "other work.")

    st.dataframe(
        df[["date", "rough", "trim", "final", "other", "total"]]
          .sort_values("date", ascending=False),
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
    )

    if job["people"]:
        st.caption("Worked by: " + ", ".join(job["people"]))


def _render_projects_section(capacity_service) -> None:
    """Pick a job, see its daily hours split rough / trim / final."""
    st.markdown("###### Hours by project")

    window = st.selectbox(
        "History",
        list(_WINDOW_CHOICES.keys()),
        index=1,
        key="proj_window",
        help="All of this comes from the same nightly snapshot, so a longer "
             "window costs nothing.",
    )

    data = capacity_service.get_project_hours(_WINDOW_CHOICES[window])
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

    _render_project_hours(job, data["daily"].get(picked_id, []))

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


def _render_capacity_view(capacity_service) -> None:
    """
    Where the hours actually went.

    Sourced entirely from QuickBooks Time, because Zoho holds no time at
    all - every cost field comes back empty and percent-complete can't be
    trusted (901 Seabury Phase 2 sat at 0% with 298 hours logged against
    it). Hours are the only record of what really happened.
    """
    st.subheader("Capacity")

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

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Hours worked", f"{totals['worked']:,.0f}")
    m2.metric("On projects", f"{totals['project']:,.0f}",
              f"{totals['project'] / worked * 100:.1f}% of worked")
    m3.metric("Overhead", f"{totals['overhead']:,.0f}",
              f"{totals['overhead'] / worked * 100:.1f}% of worked",
              delta_color="inverse")
    m4.metric("Capacity staff", len(data["people"]),
              f"{len(data['office'])} office excluded", delta_color="off")

    if totals["untagged"] > 0:
        st.caption(
            f"{totals['untagged']:,.0f}h carry no phase tag, so they aren't "
            "counted as project work. Worth chasing if that number is large."
        )

    st.divider()

    # ---- where the hours went -------------------------------------------
    st.markdown("###### Where the hours went")
    bucket_total = sum(data["buckets"].values()) or 1
    bucket_rows = [
        {
            "Bucket": label(b),
            "Hours": round(h, 1),
            "Share": h / bucket_total * 100,
            "Kind": _bucket_kind(b),
        }
        for b, h in sorted(data["buckets"].items(), key=lambda kv: -kv[1])
    ]
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

    st.divider()

    # ---- people ----------------------------------------------------------
    st.markdown("###### People")
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
    _render_projects_section(capacity_service)


def render():
    """
    Dashboard page.
    """
    service = DashboardService()
    capacity_service = CapacityService()
    kpis = service.get_kpis()

    st.title("🏠 Operations Command Center")
    st.write("Welcome to the Operations Command Center.")
    st.divider()

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Projects", kpis["projects"])

    with col2:
        st.metric("Tasks", kpis["tasks"])

    with col3:
        st.metric("Cases", kpis["cases"])

    with col4:
        st.metric("CRM Users", kpis["users"])

    if kpis["source"] == "zoho":
        st.success("Connected to Zoho CRM")
    else:
        st.warning(
            "Showing last cached data - couldn't reach Zoho just now."
            + (f" ({kpis['error']})" if kpis.get("error") else "")
        )

    st.divider()

    schedule_tab, capacity_tab = st.tabs(["📅 Schedule", "📊 Capacity"])

    with schedule_tab:
        _render_calendar_view(service)

    with capacity_tab:
        _render_capacity_view(capacity_service)


if __name__ == "__main__":
    st.set_page_config(
        page_title="Capacity Planner",
        page_icon="🏠",
        layout="wide",
    )
    if check_password():
        render()
    else:
        st.stop()
