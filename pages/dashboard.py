import calendar
import datetime as dt
import html as html_lib

import streamlit as st

from app.services.dashboard_service import DashboardService
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


def render():
    """
    Dashboard page
    """

    service = DashboardService()
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

    st.divider()

    if kpis["source"] == "zoho":
        st.success("Connected to Zoho CRM")
    else:
        st.warning(
            "Showing last cached data - couldn't reach Zoho just now."
            + (f" ({kpis['error']})" if kpis.get("error") else "")
        )

    st.divider()

    _render_calendar_view(service)


# Streamlit's "pages/" folder is auto-detected and turned into a multipage
# sidebar nav item ("dashboard"), separate from main.py importing and
# calling render() directly. When Streamlit runs this file *as that page*
# (not when main.py imports it), it executes it with __name__ == "__main__",
# so this guard renders the same content there too - without it, clicking
# "dashboard" in the sidebar showed a blank page (render() was never
# called), and switch to that page didn't produce a first-run error since
# Streamlit ran the script but nothing in it drew anything.
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
