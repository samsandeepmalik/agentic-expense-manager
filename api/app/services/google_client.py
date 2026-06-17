"""Google OAuth, Drive and Sheets clients.

Google is an optional one-way sync target: receipts go to a Drive folder,
transactions to a spreadsheet (see services/sync.py). Tokens persist in the
SQLite settings table.
"""

from __future__ import annotations

import io
from datetime import datetime
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload

from ..config import config
from ..db import get_db, get_setting, set_setting
from ..errors import AppError
from ..settings_keys import (
    DRIVE_FOLDER_BASE_NAME,
    DRIVE_ROOT_FOLDER_ID,
    DRIVE_YEAR_FOLDERS,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_PKCE_VERIFIER,
    GOOGLE_TOKENS,
)

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
]

_DRIVE_SCOPE_FULL = "https://www.googleapis.com/auth/drive"
_DRIVE_SCOPE_FILE = "https://www.googleapis.com/auth/drive.file"
_DEFAULT_FOLDER_BASE = "Expense Manager"


def get_folder_base_name() -> str:
    return _read(DRIVE_FOLDER_BASE_NAME) or _DEFAULT_FOLDER_BASE


def set_folder_base_name(name: str) -> None:
    name = name.strip()
    if not name:
        raise AppError("invalid_folder_name", "Folder name cannot be empty", 422)
    _write(DRIVE_FOLDER_BASE_NAME, name)
    # Clear all cached folder IDs — next sync finds/creates folders with new name.
    _write(DRIVE_ROOT_FOLDER_ID, None)
    _write(DRIVE_YEAR_FOLDERS, None)


def get_scope_version() -> str | None:
    tokens = _read(GOOGLE_TOKENS)
    if not tokens:
        return None
    scopes = tokens.get("scopes", [])
    if _DRIVE_SCOPE_FILE in scopes:
        return "sandboxed"
    if _DRIVE_SCOPE_FULL in scopes:
        return "legacy"
    return None


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


def client_creds() -> tuple[str, str]:
    """OAuth client id + secret: settings table first, .env fallback."""
    client_id = _read(GOOGLE_CLIENT_ID) or config.google_client_id
    client_secret = _read(GOOGLE_CLIENT_SECRET) or config.google_client_secret
    return client_id, client_secret


def is_configured() -> bool:
    return all(client_creds())


def save_client_creds(client_id: str, client_secret: str) -> None:
    client_id, client_secret = client_id.strip(), client_secret.strip()
    if not client_id or not client_secret:
        raise AppError("invalid_credentials",
                       "Both client id and client secret are required", 422)
    _write(GOOGLE_CLIENT_ID, client_id)
    _write(GOOGLE_CLIENT_SECRET, client_secret)


def _client_config() -> dict[str, Any]:
    client_id, client_secret = client_creds()
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [config.google_redirect_uri],
        }
    }


def build_auth_url() -> str:
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES)
    flow.redirect_uri = config.google_redirect_uri
    url, _state = flow.authorization_url(
        access_type="offline", prompt="consent"
    )
    # Desktop app credentials trigger PKCE automatically; persist verifier so
    # exchange_code (a separate request) can complete the handshake.
    if flow.code_verifier:
        _write(GOOGLE_PKCE_VERIFIER, flow.code_verifier)
    return url


def exchange_code(code: str) -> None:
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES)
    flow.redirect_uri = config.google_redirect_uri
    verifier = _read(GOOGLE_PKCE_VERIFIER)
    flow.fetch_token(code=code, **({"code_verifier": verifier} if verifier else {}))
    _write(GOOGLE_PKCE_VERIFIER, None)
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


def ensure_app_folder() -> str:
    """Get or create the single root app folder in Drive. Always verifies existence."""
    drive = drive_service()
    folder_name = get_folder_base_name()
    results = (
        drive.files()
        .list(
            q=(
                f"name='{folder_name}'"
                " and mimeType='application/vnd.google-apps.folder'"
                " and 'root' in parents"
                " and trashed=false"
            ),
            fields="files(id)",
            pageSize=1,
        )
        .execute()
        .get("files", [])
    )
    if results:
        folder_id = results[0]["id"]
    else:
        folder_id = (
            drive.files()
            .create(
                body={"name": folder_name,
                      "mimeType": "application/vnd.google-apps.folder"},
                fields="id",
            )
            .execute()["id"]
        )
    _write(DRIVE_ROOT_FOLDER_ID, folder_id)
    return folder_id


def ensure_drive_folder(profile: dict) -> str:
    """Get or create the profile subfolder inside the app root folder. Always verifies."""
    app_folder_id = ensure_app_folder()
    drive = drive_service()
    profile_name = profile["name"]
    results = (
        drive.files()
        .list(
            q=(
                f"name='{profile_name}' and '{app_folder_id}' in parents"
                " and mimeType='application/vnd.google-apps.folder'"
                " and trashed=false"
            ),
            fields="files(id)",
            pageSize=1,
        )
        .execute()
        .get("files", [])
    )
    if results:
        folder_id = results[0]["id"]
    else:
        folder_id = (
            drive.files()
            .create(
                body={
                    "name": profile_name,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [app_folder_id],
                },
                fields="id",
            )
            .execute()["id"]
        )
    with get_db() as conn:
        conn.execute("UPDATE profiles SET drive_folder_id=? WHERE id=?",
                     (folder_id, profile["id"]))
    return folder_id


def find_spreadsheet(name: str, parent_folder_id: str) -> str | None:
    """Return the id of an existing spreadsheet with this name in the folder,
    or None. Lets sync reuse a sheet instead of creating a duplicate when the
    stored spreadsheet_id is missing (DB swap/restore)."""
    drive = drive_service()
    results = (
        drive.files()
        .list(
            q=(
                f"name='{name}' and '{parent_folder_id}' in parents"
                " and mimeType='application/vnd.google-apps.spreadsheet'"
                " and trashed=false"
            ),
            fields="files(id)",
            pageSize=1,
        )
        .execute()
        .get("files", [])
    )
    return results[0]["id"] if results else None


def is_spreadsheet_alive(spreadsheet_id: str) -> bool:
    """Return True if the file exists in Drive and is not trashed.

    Uses the Drive API (not Sheets API) so trash status is included.
    On 403/404 returns False; other HttpErrors propagate.
    """
    try:
        meta = drive_service().files().get(
            fileId=spreadsheet_id, fields="id,trashed"
        ).execute()
        return not meta.get("trashed", False)
    except HttpError as e:
        if e.resp.status in (403, 404):
            return False
        raise


def drive_create_spreadsheet(title: str, parent_folder_id: str) -> dict:
    """Create a Google Sheet directly inside a Drive folder.

    Uses Drive API (not Sheets API) so the file gets the correct parent at
    creation time — no subsequent move needed and no drive.file scope issues.
    Returns {"id": spreadsheet_id}.
    """
    drive = drive_service()
    return drive.files().create(
        body={
            "name": title,
            "mimeType": "application/vnd.google-apps.spreadsheet",
            "parents": [parent_folder_id],
        },
        fields="id",
    ).execute()


def ensure_year_folder(profile: dict, year: int) -> str:
    """Find or create a year subfolder under the profile's Drive folder.

    Folder IDs are cached in the settings table so each year only needs one
    Drive API call (on first use) rather than a list query every upload.
    """
    cache_key = f"{profile['id']}:{year}"
    cache: dict = _read(DRIVE_YEAR_FOLDERS) or {}
    if cache_key in cache:
        return cache[cache_key]

    root_id = ensure_drive_folder(profile)
    drive = drive_service()

    results = drive.files().list(
        q=(
            f"name='{year}' and '{root_id}' in parents"
            " and mimeType='application/vnd.google-apps.folder'"
            " and trashed=false"
        ),
        fields="files(id)",
        pageSize=1,
    ).execute().get("files", [])

    if results:
        folder_id = results[0]["id"]
    else:
        folder_id = drive.files().create(
            body={
                "name": str(year),
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [root_id],
            },
            fields="id",
        ).execute()["id"]

    cache[cache_key] = folder_id
    _write(DRIVE_YEAR_FOLDERS, cache)
    return folder_id


def upload_receipt_image(filename: str, data: bytes, mime_type: str,
                         profile: dict, date: str = "") -> dict:
    """Upload receipt to Drive year subfolder; return {"link","name"}.

    "name" is the file name created in Drive so callers can show a readable
    receipt label alongside the shareable link.
    """
    year = int(date[:4]) if date and len(date) >= 4 else datetime.now().year
    drive = drive_service()
    for attempt in range(2):
        try:
            folder_id = ensure_year_folder(profile, year)
            media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime_type)
            created = (
                drive.files()
                .create(
                    body={"name": filename, "parents": [folder_id]},
                    media_body=media,
                    fields="id, name, webViewLink",
                )
                .execute()
            )
            # Anyone with the link can view so sheet links work anywhere
            drive.permissions().create(
                fileId=created["id"], body={"type": "anyone", "role": "reader"}
            ).execute()
            return {"link": created["webViewLink"], "name": created.get("name", filename)}
        except HttpError as e:
            if e.resp.status == 404 and attempt == 0:
                # Year folder gone — invalidate cache entry and retry once
                cache: dict = _read(DRIVE_YEAR_FOLDERS) or {}
                cache.pop(f"{profile['id']}:{year}", None)
                _write(DRIVE_YEAR_FOLDERS, cache)
                continue
            raise

