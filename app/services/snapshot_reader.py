"""
Read the nightly snapshot instead of calling QuickBooks Time.

The app used to fetch on page load. With ten people on it that means ten
separate pulls through one shared token, each taking minutes on a long
window, with a spinner that looks the same whether it's working or hung.

So a scheduled job builds a snapshot once a night (scripts/build_snapshot.py)
and writes it to Drive. This reads that file. The app makes no QuickBooks
Time calls at all.

Where it looks, in order:
  1. .cache/capacity_snapshot.json  - local, written by a manual build
  2. Google Drive                   - what the nightly job writes

Local first so you can rebuild and see the result immediately without
waiting for the next scheduled run. On Streamlit Cloud there is no local
copy (the filesystem is wiped when the app sleeps), so it always comes
from Drive.

Credentials: the same service account the export uses, read-only here.
Missing credentials are not fatal - the page says the snapshot is
unavailable and when it was last good.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
from pathlib import Path

import streamlit as st

from app.utils.logger import get_logger

logger = get_logger(__name__)

SNAPSHOT_NAME = "capacity_snapshot.json"
LOCAL_COPY = Path(__file__).resolve().parent.parent.parent / ".cache" / SNAPSHOT_NAME

# The snapshot changes once a night. An hour keeps the page quick without
# pinning a stale copy for a whole working day.
SNAPSHOT_TTL_SECONDS = 3600

# Read-only: this process should never be able to overwrite the snapshot
# that the nightly job produces.
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def _service_account_info() -> dict | None:
    try:
        if "gcp_service_account" in st.secrets:
            return dict(st.secrets["gcp_service_account"])
    except Exception:  # noqa: BLE001 - no secrets file at all is fine
        pass
    raw = (os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or "").strip()
    if not raw:
        return None
    if raw.startswith("{"):
        return json.loads(raw)
    if os.path.exists(raw):
        with open(raw, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def _folder_id() -> str | None:
    try:
        if "DRIVE_FOLDER_ID" in st.secrets:
            return str(st.secrets["DRIVE_FOLDER_ID"])
    except Exception:  # noqa: BLE001
        pass
    return os.environ.get("DRIVE_FOLDER_ID") or None


def _read_from_drive() -> dict | None:
    info = _service_account_info()
    folder = _folder_id()
    if not info or not folder:
        logger.warning("No Drive credentials configured for the snapshot")
        return None

    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload

    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    # Shared drives need these flags, or the folder reads as "not found"
    # even when the service account can see it perfectly well.
    q = (f"name = '{SNAPSHOT_NAME}' and '{folder}' in parents "
         f"and trashed = false")
    files = service.files().list(
        q=q, spaces="drive", fields="files(id, modifiedTime)", pageSize=1,
        corpora="allDrives", supportsAllDrives=True,
        includeItemsFromAllDrives=True).execute().get("files", [])
    if not files:
        logger.warning("No %s in the Drive folder", SNAPSHOT_NAME)
        return None

    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(
        buf, service.files().get_media(fileId=files[0]["id"],
                                       supportsAllDrives=True))
    done = False
    while not done:
        _, done = downloader.next_chunk()
    buf.seek(0)
    payload = json.load(buf)
    payload["_source"] = "drive"
    payload["_drive_modified"] = files[0].get("modifiedTime")
    return payload


@st.cache_data(ttl=SNAPSHOT_TTL_SECONDS, show_spinner="Loading snapshot...")
def load_snapshot() -> dict:
    """
    Returns the snapshot, or {"error": str} if it can't be read.

    Never raises - a missing snapshot should show a message on the page,
    not a stack trace.
    """
    if LOCAL_COPY.exists():
        try:
            payload = json.loads(LOCAL_COPY.read_text(encoding="utf-8"))
            payload["_source"] = "local"
            return payload
        except Exception as exc:  # noqa: BLE001
            logger.warning("Local snapshot unreadable (%s) - trying Drive", exc)

    try:
        payload = _read_from_drive()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Drive snapshot read failed: %s", exc)
        return {"error": str(exc)}

    if payload is None:
        return {"error": "No snapshot found. Has the nightly job run yet?"}
    return payload


def snapshot_age(payload: dict) -> str:
    """Human-readable age, for the 'as of' line on the page."""
    stamp = payload.get("generated_at_utc")
    if not stamp:
        return "unknown age"
    try:
        made = dt.datetime.fromisoformat(stamp)
    except ValueError:
        return stamp
    if made.tzinfo is None:
        made = made.replace(tzinfo=dt.timezone.utc)
    delta = dt.datetime.now(dt.timezone.utc) - made
    hrs = delta.total_seconds() / 3600
    if hrs < 1:
        return f"{int(delta.total_seconds() // 60)} minutes old"
    if hrs < 48:
        return f"{int(hrs)} hours old"
    return f"{int(hrs // 24)} days old"


def clear() -> None:
    load_snapshot.clear()
