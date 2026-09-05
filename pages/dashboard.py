import streamlit as st

from app.services.dashboard_service import DashboardService
from app.utils.auth import check_password


def _format_time(moment) -> str:
    """
    '09:00 AM' -> '9:00 AM'. Not using strftime's '%-I' (no leading zero)
    here because that's a Linux/macOS-only glibc extension - it raises on
    Windows, and this app gets run locally on Windows during development
    as well as deployed on (Linux) Streamlit Community Cloud.
    """
    return moment.strftime("%I:%M %p").lstrip("0")


def _render_upcoming_schedule(service: DashboardService) -> None:
    st.subheader("📅 Upcoming Schedule")
    st.caption("From Zoho CRM's Calendar - next 14 days")

    schedule = service.get_upcoming_events(days_ahead=14)

    if schedule["error"] and not schedule["events"]:
        st.warning(f"Couldn't load the calendar right now. ({schedule['error']})")
        return

    events = schedule["events"]
    if not events:
        st.info("Nothing on the calendar in the next 14 days.")
        return

    # Group consecutive events by the calendar day they start on, so the
    # page reads like an agenda ("Mon, Sep 8" then its events) rather than
    # one long flat list.
    current_day = None
    for event in events:
        day = event["start"].date()
        if day != current_day:
            current_day = day
            # Same reasoning as _format_time: build the "Sep 8" part by
            # hand instead of strftime's '%-d', which isn't portable to
            # Windows.
            st.markdown(f"**{day.strftime('%A, %b')} {day.day}**")
        title = event.get("Event_Title") or "(untitled event)"
        time_range = _format_time(event["start"])
        if event.get("end") and event["end"] != event["start"]:
            time_range += f" – {_format_time(event['end'])}"
        st.markdown(f"&nbsp;&nbsp;🕒 {time_range} — {title}")

    if schedule["error"]:
        st.caption(f"Note: showing possibly-stale data - last refresh had an error ({schedule['error']})")


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

    _render_upcoming_schedule(service)


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
