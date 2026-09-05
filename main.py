import streamlit as st

from pages.dashboard import render as render_dashboard

st.set_page_config(
    page_title="Capacity Planner",
    page_icon="🏠",
    layout="wide",
)

render_dashboard()
