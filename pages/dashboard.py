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


def _day_by_day_html(range_start: dt.date, range_end: dt.date, events_by_day: dict, tech_colors: dict) -> str:
    """
    One row per calendar day (including empty ones, shown minimally, so a
    light day still reads as "nothing scheduled" rather than just
    vanishing from the list) inside a single scrollable panel - the
    "blown up, scrollable, day by day" view, as opposed to the cramped
    small-cell month grid this replaces. Every event is shown in full,
    nothing truncated with a "+N more".
    """
    today = dt.date.today()
    parts = ['<div class="agenda-wrap">']

    current = range_start
    while current <= range_end:
        is_today = current == today
        day_label = f"{current.strftime('%A, %B')} {current.day}"
        if is_today:
            day_label += " — Today"
        header_class = "agenda-day-header" + (" agenda-day-header-today" if is_today else "")
        parts.append(
            f'<div class="agenda-day"><div class="{header_class}">'
            f"{html_lib.escape(day_label)}</div>"
        )

        day_events = events_by_day.get(current, [])
        if not day_events:
            parts.append('<div class="agenda-empty">No jobs scheduled</div>')
        else:
            for event in day_events:
                title = html_lib.escape(event.get("Event_Title") or "(untitled event)")
                tech = html_lib.escape(event.get("tech_name") or "Unassigned")
                color = tech_colors.get(event.get("tech_name") or "Unassigned", _OVERFLOW_COLOR)
                time_str = html_lib.escape(_format_time(event["start"]))
                if event.get("end") and event["end"] != event["start"]:
                    time_str += f" – {html_lib.escape(_format_time(event['end']))}"
                parts.append(
                    f'<div class="agenda-event" style="border-left-color:{color}">'
                    f'<div class="agenda-event-top">'
                    f'<span class="agenda-event-time">{time_str}</span>'
                    f'<span class="agenda-event-tech" style="background:{color}">{tech}</span>'
                    f"</div>"
                    f'<div class="agenda-event-title">{title}</div>'
                    f"</div>"
                )
        parts.append("</div>")
        current += dt.timedelta(days=1)

    parts.append("</div>")
    return "".join(parts)


_CALENDAR_CSS = """
<style>
.agenda-wrap {
    max-height: 72vh; overflow-y: auto; border: 1px solid #e1e0d9; border-radius: 8px;
    padding: 0 18px 18px 18px; background: #fcfcfb;
}
.agenda-day { padding-top: 4px; border-bottom: 1px solid #e1e0d9; }
.agenda-day:last-child { border-bottom: none; }
.agenda-day-header {
    position: sticky; top: 0; background: #fcfcfb; padding: 10px 0 6px 0;
    font-size: 1.1rem; font-weight: 700; color: #0b0b0b; z-index: 1;
}
.agenda-day-header-today { color: #2a78d6; }
.agenda-empty { color: #898781; font-style: italic; font-size: 0.85rem; padding: 0 0 14px 0; }
.agenda-event {
    border-left: 4px solid; border-radius: 6px; padding: 10px 14px; margin: 0 0 12px 0;
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
@media (prefers-color-scheme: dark) {
    .agenda-wrap { border-color: #2c2c2a; background: #1a1a19; }
    .agenda-day { border-bottom-color: #2c2c2a; }
    .agenda-day-header { background: #1a1a19; color: #ffffff; }
    .agenda-day-header-today { color: #3987e5; }
    .agenda-event { background: #0d0d0d; }
    .agenda-event-time { color: #c3c2b7; }
    .agenda-event-title { color: #ffffff; }
}
</style>
"""


def _render_calendar_view(service: DashboardService) -> None:
    st.subheader("📅 Schedule")
    st.caption("From Zoho CRM's Calendar - this month and next, one row per day, scroll for more")

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

    events_by_day: dict = {}
    for event in events:
        events_by_day.setdefault(event["start"].date(), []).append(event)

    if tech_colors:
        legend_items = "".join(
            f'<span class="cal-legend-item"><span class="cal-legend-swatch" '
            f'style="background:{color}"></span>{html_lib.escape(name)}</span>'
            for name, color in tech_colors.items()
        )
        st.markdown(
            _CALENDAR_CSS + f'<div class="cal-legend">{legend_items}</div>',
            unsafe_allow_html=True,
        )

    agenda_html = _day_by_day_html(range_start, range_end, events_by_day, tech_colors)
    st.markdown(_CALENDAR_CSS + agenda_html, unsafe_allow_html=True)

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
