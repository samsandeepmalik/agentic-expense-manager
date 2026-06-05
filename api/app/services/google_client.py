"""Google OAuth, Drive and Sheets clients.

All business state lives in Google: receipts in a Drive folder, transactions
and categories in a spreadsheet. Tokens persist in the local JSON store.
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
from ..store import read_settings, write_settings

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
]

SPREADSHEET_TITLE = "Expense Manager"
DRIVE_FOLDER_NAME = "Expense Receipts"

TRANSACTIONS_SHEET = "Transactions"
CATEGORIES_SHEET = "Categories"

TRANSACTION_HEADERS = [
    "Date", "Type", "Category", "Description", "Merchant",
    "Amount", "GST", "QST", "Total", "Counted Amount",
    "Image Link", "Source", "Recorded At",
]
CATEGORY_HEADERS = ["Name", "Type", "Percent"]

DEFAULT_CATEGORIES = [
    ["Groceries", "expense", 100],
    ["Dining", "expense", 100],
    ["Transport", "expense", 100],
    ["Utilities", "expense", 100],
    ["Rent", "expense", 100],
    ["Health", "expense", 100],
    ["Entertainment", "expense", 100],
    ["Other", "expense", 100],
    ["Salary", "income", 100],
    ["Business", "income", 100],
    ["Other Income", "income", 100],
]


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
    write_settings(google_tokens=_creds_to_dict(creds))


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
    tokens = read_settings().get("google_tokens")
    if not tokens:
        raise GoogleNotConnectedError()
    creds = Credentials(**tokens)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        write_settings(google_tokens=_creds_to_dict(creds))
    return creds


def is_connected() -> bool:
    return bool(read_settings().get("google_tokens"))


# ---------------------------------------------------------------------------
# Service builders
# ---------------------------------------------------------------------------


def drive_service():
    return build("drive", "v3", credentials=get_credentials(), cache_discovery=False)


def sheets_service():
    return build("sheets", "v4", credentials=get_credentials(), cache_discovery=False)


# ---------------------------------------------------------------------------
# Bootstrap: spreadsheet + folder
# ---------------------------------------------------------------------------


def ensure_drive_folder() -> str:
    settings = read_settings()
    folder_id = config.google_drive_folder_id or settings.get("drive_folder_id")
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
    write_settings(drive_folder_id=folder_id)
    return folder_id


def ensure_spreadsheet() -> str:
    settings = read_settings()
    spreadsheet_id = config.google_spreadsheet_id or settings.get("spreadsheet_id")
    sheets = sheets_service()

    if not spreadsheet_id:
        created = (
            sheets.spreadsheets()
            .create(
                body={
                    "properties": {"title": SPREADSHEET_TITLE},
                    "sheets": [
                        {"properties": {"title": TRANSACTIONS_SHEET}},
                        {"properties": {"title": CATEGORIES_SHEET}},
                    ],
                },
                fields="spreadsheetId",
            )
            .execute()
        )
        spreadsheet_id = created["spreadsheetId"]
        write_settings(spreadsheet_id=spreadsheet_id)

    _ensure_headers(sheets, spreadsheet_id)
    return spreadsheet_id


def _ensure_headers(sheets, spreadsheet_id: str) -> None:
    meta = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    existing_titles = {s["properties"]["title"] for s in meta.get("sheets", [])}

    requests = []
    for title in (TRANSACTIONS_SHEET, CATEGORIES_SHEET):
        if title not in existing_titles:
            requests.append({"addSheet": {"properties": {"title": title}}})
    if requests:
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": requests}
        ).execute()

    # Headers
    first_row = (
        sheets.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"{TRANSACTIONS_SHEET}!A1:M1")
        .execute()
        .get("values", [])
    )
    if not first_row:
        sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{TRANSACTIONS_SHEET}!A1",
            valueInputOption="RAW",
            body={"values": [TRANSACTION_HEADERS]},
        ).execute()

    cat_rows = (
        sheets.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"{CATEGORIES_SHEET}!A1:C1")
        .execute()
        .get("values", [])
    )
    if not cat_rows:
        sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{CATEGORIES_SHEET}!A1",
            valueInputOption="RAW",
            body={"values": [CATEGORY_HEADERS, *DEFAULT_CATEGORIES]},
        ).execute()


def spreadsheet_url() -> str | None:
    settings = read_settings()
    sid = config.google_spreadsheet_id or settings.get("spreadsheet_id")
    return f"https://docs.google.com/spreadsheets/d/{sid}" if sid else None


# ---------------------------------------------------------------------------
# Drive upload
# ---------------------------------------------------------------------------


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
