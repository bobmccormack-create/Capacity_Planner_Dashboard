import streamlit as st

from app.utils.auth import check_password
from pages.dashboard import render as render_dashboard

st.set_page_config(
    page_title="Capacity Planner",
    page_icon="🏠",
    layout="wide",
)

if not check_password():
    st.stop()

render_dashboard()
