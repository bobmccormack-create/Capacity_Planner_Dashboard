import streamlit as st

import theme
from app.utils.auth import check_password
from pages.dashboard import render as render_dashboard

st.set_page_config(
    page_title="Capacity Planner",
    page_icon="🏠",
    layout="wide",
)

# Must come after set_page_config and before anything renders, including the
# password gate — otherwise the login screen shows up unstyled for a beat.
theme.apply_theme()

if not check_password():
    st.stop()

render_dashboard()
