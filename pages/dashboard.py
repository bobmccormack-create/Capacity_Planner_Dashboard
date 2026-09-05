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


def _month_grid_html(year: int, month: int, events_by_day: dict, tech_colors: dict) -> str:
    """
    One month as an HTML table: a weekday header row, then a row per
    week. Cells outside `year`/`month` (the leading/trailing days
    Calendar.monthdatescalendar pads each first/last week with) show only
    a dimmed date number - events are only plotted on their actual day,
    never duplicated onto a neighboring month's cell for the same date.
    """
    cal = calendar.Calendar(firstweekday=6)  # weeks start Sunday
    weeks = cal.monthdatescalendar(year, month)
    month_label = dt.date(year, month, 1).strftime("%B %Y")

    parts = [f'<div class="cal-month"><div class="cal-month-title">{month_label}</div>']
    parts.append('<table class="cal-grid"><thead><tr>')
    for wd in ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"):
        parts.append(f"<th>{wd}</th>")
    parts.append("</tr></thead><tbody>")

    today = dt.date.today()
    for week in weeks:
        parts.append("<tr>")
        for day in week:
            in_month = day.month == month
            is_today = day == today
            cell_classes = "cal-day" + ("" if in_month else " cal-day-outside") + (
                " cal-day-today" if is_today else ""
            )
            parts.append(f'<td class="{cell_classes}"><div class="cal-day-num">{day.day}</div>')

            if in_month:
                day_events = events_by_day.get(day, [])
                shown, extra = day_events[:3], day_events[3:]
                for event in shown:
                    title = html_lib.escape(event.get("Event_Title") or "(untitled event)")
                    tech = html_lib.escape(event.get("tech_name") or "Unassigned")
                    color = tech_colors.get(event.get("tech_name") or "Unassigned", _OVERFLOW_COLOR)
                    time_str = html_lib.escape(_format_time(event["start"]))
                    tooltip = html_lib.escape(
                        f"{event.get('Event_Title') or '(untitled event)'}\n"
                        f"{_format_time(event['start'])}"
                        + (f" - {_format_time(event['end'])}" if event.get("end") else "")
                        + f"\nTech: {event.get('tech_name') or 'Unassigned'}"
                    )
                    parts.append(
                        f'<div class="cal-chip" style="border-left-color:{color}" title="{tooltip}">'
                        f'<span class="cal-chip-time">{time_str}</span> '
                        f'<span class="cal-chip-tech" style="color:{color}">{tech}</span>'
                        f'<div class="cal-chip-title">{title}</div>'
                        f"</div>"
                    )
                if extra:
                    parts.append(f'<div class="cal-more">+{len(extra)} more</div>')

            parts.append("</td>")
        parts.append("</tr>")

    parts.append("</tbody></table></div>")
    return "".join(parts)


_CALENDAR_CSS = """
<style>
.cal-wrap { display: flex; gap: 24px; flex-wrap: wrap; }
.cal-month { flex: 1 1 420px; min-width: 340px; }
.cal-month-title { font-weight: 600; font-size: 1.05rem; margin-bottom: 8px; color: #0b0b0b; }
.cal-grid { width: 100%; border-collapse: collapse; table-layout: fixed; }
.cal-grid th {
    font-size: 0.72rem; font-weight: 600; color: #898781; text-transform: uppercase;
    padding: 4px 2px; text-align: left; border-bottom: 1px solid #e1e0d9;
}
.cal-day {
    vertical-align: top; border: 1px solid #e1e0d9; height: 92px; width: 14.28%;
    padding: 3px; overflow: hidden;
}
.cal-day-outside { background: #f9f9f7; }
.cal-day-outside .cal-day-num { color: #c3c2b7; }
.cal-day-today { background: #f0f6ff; }
.cal-day-num { font-size: 0.78rem; color: #52514e; margin-bottom: 2px; }
.cal-chip {
    border-left: 3px solid; padding: 1px 4px; margin-bottom: 2px; border-radius: 2px;
    background: #fcfcfb; font-size: 0.68rem; line-height: 1.25; cursor: default;
}
.cal-chip-time { color: #52514e; font-weight: 600; }
.cal-chip-tech { font-weight: 600; }
.cal-chip-title { color: #0b0b0b; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cal-more { font-size: 0.66rem; color: #898781; padding-left: 4px; }
.cal-legend { margin-top: 10px; font-size: 0.8rem; }
.cal-legend-item { display: inline-flex; align-items: center; gap: 5px; margin-right: 14px; }
.cal-legend-swatch { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
@media (prefers-color-scheme: dark) {
    .cal-month-title { color: #ffffff; }
    .cal-grid th { color: #c3c2b7; border-bottom-color: #2c2c2a; }
    .cal-day { border-color: #2c2c2a; }
    .cal-day-outside { background: #0d0d0d; }
    .cal-day-outside .cal-day-num { color: #383835; }
    .cal-day-today { background: #16202c; }
    .cal-day-num { color: #c3c2b7; }
    .cal-chip { background: #1a1a19; }
    .cal-chip-time { color: #c3c2b7; }
    .cal-chip-title { color: #ffffff; }
}
</style>
"""


def _render_calendar_view(service: DashboardService) -> None:
    st.subheader("📅 Schedule")
    st.caption("From Zoho CRM's Calendar - this month and next")

    today = dt.date.today()
    month1_year, month1 = today.year, today.month
    month2_year, month2 = (today.year, today.month + 1) if today.month < 12 else (today.year + 1, 1)

    # Fetch across the full span both grids actually display, including
    # the leading/trailing days from neighboring months each grid pads
    # its first/last week with - otherwise those padding cells would
    # always show empty even when Zoho has an event on that exact date.
    weeks1 = calendar.Calendar(firstweekday=6).monthdatescalendar(month1_year, month1)
    weeks2 = calendar.Calendar(firstweekday=6).monthdatescalendar(month2_year, month2)
    range_start = weeks1[0][0]
    range_end = weeks2[-1][-1]

    schedule = service.get_calendar_range(range_start, range_end)

    if schedule["error"] and not schedule["events"]:
        st.warning(f"Couldn't load the calendar right now. ({schedule['error']})")
        return

    events = schedule["events"]
    tech_colors = _assign_tech_colors(events)

    events_by_day: dict = {}
    for event in events:
        events_by_day.setdefault(event["start"].date(), []).append(event)

    grid_html = (
        _CALENDAR_CSS
        + '<div class="cal-wrap">'
        + _month_grid_html(month1_year, month1, events_by_day, tech_colors)
        + _month_grid_html(month2_year, month2, events_by_day, tech_colors)
        + "</div>"
    )
    st.markdown(grid_html, unsafe_allow_html=True)

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
        st.caption(
            "Tech names are Zoho's Owner field on each event - not yet confirmed this is "
            "the assigned tech rather than whoever created the entry. Let me know if these "
            "look wrong and I'll switch it to a different field."
        )

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
