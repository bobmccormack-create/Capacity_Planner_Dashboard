"""
Central configuration for the Capacity Planner.

All values are pulled from environment variables (see .env.example).
Nothing in this file should contain secrets - only defaults and lookups.

Locally, those environment variables come from .env (via python-dotenv).
When deployed on Streamlit Community Cloud, there's no .env file - instead
you set the same keys in the app's "Secrets" settings (TOML format), and
Streamlit exposes them via st.secrets. The block below copies anything in
st.secrets into the environment so the os.getenv() calls below work the
same way in both places without any other code changes.
"""
import os
from dotenv import load_dotenv

load_dotenv()

try:
    import streamlit as st

    for _key, _value in st.secrets.items():
        os.environ.setdefault(_key, str(_value))
except Exception:
    # No secrets.toml configured (e.g. running scripts like
    # get_refresh_token.py outside of Streamlit) - that's fine, .env
    # or real environment variables still work.
    pass


class Settings:
    # --- Zoho OAuth ---
    ZOHO_CLIENT_ID = os.getenv("ZOHO_CLIENT_ID", "")
    ZOHO_CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET", "")
    ZOHO_REFRESH_TOKEN = os.getenv("ZOHO_REFRESH_TOKEN", "")
    ZOHO_REGION = os.getenv("ZOHO_REGION", "com")  # com, eu, in, com.au, jp

    # --- Zoho Projects ---
    ZOHO_PORTAL_ID = os.getenv("ZOHO_PORTAL_ID", "")
    ZOHO_PROJECTS_PROJECT_ID = os.getenv("ZOHO_PROJECTS_PROJECT_ID", "")  # optional: scope tasks to one project

    # --- Zoho CRM ---
    ZOHO_CRM_ORG_ID = os.getenv("ZOHO_CRM_ORG_ID", "")

    # --- Database (local cache) ---
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///capacity_planner.db")

    # --- App behavior ---
    CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "300"))
    REQUEST_TIMEOUT_SECONDS = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "15"))

    @classmethod
    def zoho_accounts_base(cls) -> str:
        return f"https://accounts.zoho.{cls.ZOHO_REGION}"

    @classmethod
    def zoho_projects_base(cls) -> str:
        return f"https://projectsapi.zoho.{cls.ZOHO_REGION}"

    @classmethod
    def zoho_crm_base(cls) -> str:
        return f"https://www.zohoapis.{cls.ZOHO_REGION}"

    @classmethod
    def has_zoho_credentials(cls) -> bool:
        return bool(
            cls.ZOHO_CLIENT_ID and cls.ZOHO_CLIENT_SECRET and cls.ZOHO_REFRESH_TOKEN
        )


settings = Settings()
