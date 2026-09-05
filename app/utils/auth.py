"""
Simple shared-password gate for the dashboard.

This is intentionally lightweight - one password for the whole team, not
individual logins. Set APP_PASSWORD in .env (locally) or in the app's
Secrets (when deployed) to turn it on; leave it blank to skip the gate
entirely.
"""
import streamlit as st

from app.config.settings import settings


def check_password() -> bool:
    """
    Returns True once the correct password has been entered (or if no
    password is configured at all). Renders a password prompt and returns
    False otherwise - callers should st.stop() when this returns False.
    """
    if not settings.APP_PASSWORD:
        return True

    if st.session_state.get("password_correct", False):
        return True

    def _password_entered():
        if st.session_state.get("password_input") == settings.APP_PASSWORD:
            st.session_state["password_correct"] = True
            del st.session_state["password_input"]
        else:
            st.session_state["password_correct"] = False

    st.title("🏠 Capacity Planner")
    st.text_input(
        "Password",
        type="password",
        on_change=_password_entered,
        key="password_input",
    )

    if st.session_state.get("password_correct") is False:
        st.error("Incorrect password.")

    return False
