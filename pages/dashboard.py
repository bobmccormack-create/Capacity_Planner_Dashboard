import streamlit as st

from app.services.dashboard_service import DashboardService
from app.utils.auth import check_password


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
