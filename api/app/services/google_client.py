"""Google OAuth, Drive and Sheets clients.

Google is an optional one-way sync target: receipts go to a Drive folder,
transactions to a spreadsheet (see services/sync.py). Tokens persist in the
SQLite settings table.
"""

from __future__ import annotations

import io
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from ..config import config
from ..db import get_db, get_setting, set_setting
from ..settings_keys import DRIVE_FOLDER_ID, GOOGLE_TOKENS

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
]

DRIVE_FOLDER_NAME = "Expense Receipts"


def _read(key):
    with get_db() as conn:
        return get_setting(conn, key)


def _write(key, value):
    with get_db() as conn:
        set_setting(conn, key, value)


class GoogleNotConnectedError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "Google account is not connected. Open the dashboard and connect "
            "Google under Settings, or visit /api/google/auth."
        )


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------


def _client_config() -> dict[str, Any]:
    return {
        "web": {
            "client_id": config.google_client_id,
            "client_secret": config.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [config.google_redirect_uri],
        }
    }


def build_auth_url() -> str:
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES)
    flow.redirect_uri = config.google_redirect_uri
    url, _state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent"
    )
    return url


def exchange_code(code: str) -> None:
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES)
    flow.redirect_uri = config.google_redirect_uri
    flow.fetch_token(code=code)
    creds = flow.credentials
    _write(GOOGLE_TOKENS, _creds_to_dict(creds))


def _creds_to_dict(creds: Credentials) -> dict[str, Any]:
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or SCOPES),
    }


def get_credentials() -> Credentials:
    tokens = _read(GOOGLE_TOKENS)
    if not tokens:
        raise GoogleNotConnectedError()
    creds = Credentials(**tokens)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _write(GOOGLE_TOKENS, _creds_to_dict(creds))
    return creds


def is_connected() -> bool:
    return bool(_read(GOOGLE_TOKENS))


# ---------------------------------------------------------------------------
# Service builders
# ---------------------------------------------------------------------------


def drive_service():
    return build("drive", "v3", credentials=get_credentials(), cache_discovery=False)


def sheets_service():
    return build("sheets", "v4", credentials=get_credentials(), cache_discovery=False)


# ---------------------------------------------------------------------------
# Drive folder + upload
# ---------------------------------------------------------------------------


def ensure_drive_folder() -> str:
    folder_id = config.google_drive_folder_id or _read(DRIVE_FOLDER_ID)
    if folder_id:
        return folder_id

    drive = drive_service()
    created = (
        drive.files()
        .create(
            body={
                "name": DRIVE_FOLDER_NAME,
                "mimeType": "application/vnd.google-apps.folder",
            },
            fields="id",
        )
        .execute()
    )
    folder_id = created["id"]
    _write(DRIVE_FOLDER_ID, folder_id)
    return folder_id


def upload_receipt_image(filename: str, data: bytes, mime_type: str) -> str:
    """Upload a receipt image to the Drive folder; return a shareable link."""
    folder_id = ensure_drive_folder()
    drive = drive_service()
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime_type)
    created = (
        drive.files()
        .create(
            body={"name": filename, "parents": [folder_id]},
            media_body=media,
            fields="id, webViewLink",
        )
        .execute()
    )
    # Anyone with the link can view (so the sheet link works for the user anywhere)
    drive.permissions().create(
        fileId=created["id"], body={"type": "anyone", "role": "reader"}
    ).execute()
    return created["webViewLink"]
