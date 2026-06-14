"""Google OAuth connect flow."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from ..config import config
from ..services import google_client as gc

router = APIRouter()


class FolderNameIn(BaseModel):
    name: str


class CredentialsIn(BaseModel):
    client_id: str
    client_secret: str


class ColumnsIn(BaseModel):
    profile_id: int
    columns: list[str]


@router.get("/api/google/columns")
async def get_columns(profile_id: int):
    """Available sheet columns + this profile's selected order.

    `tax` is represented as a single selectable entry; it expands into one
    column per tax component only when the sheet is written.
    """
    from ..services import sync
    available = [
        {"key": k, "label": ("Tax columns" if k == "tax" else v["label"])}
        for k, v in sync.COLUMN_REGISTRY.items()
    ]
    return {
        "available": available,
        "selected": sync.get_column_config(profile_id),
        "profile_id": profile_id,
    }


@router.put("/api/google/columns")
async def put_columns(body: ColumnsIn):
    """Validate, force `id` present, save, then trigger a sync so the sheet
    rewrites with the new layout (header change → full-rewrite path)."""
    from fastapi import HTTPException
    from ..services import sync
    unknown = [k for k in body.columns if k not in sync.COLUMN_REGISTRY]
    if unknown:
        raise HTTPException(status_code=400,
                            detail=f"Unknown column keys: {unknown}")
    selected = sync.set_column_config(body.profile_id, body.columns)
    from ..db import get_db
    from ..services import audit
    with get_db() as conn:
        audit.record(conn, "sheet_columns_changed", channel="ui",
                     ref=body.profile_id, detail=",".join(selected))
    sync.request_sync()
    return {"selected": selected, "profile_id": body.profile_id}


@router.get("/api/google/status")
async def status():
    from ..db import get_db
    from ..services.sync import status as sync_status_fn
    with get_db() as conn:
        all_profiles = [
            dict(r) for r in conn.execute(
                "SELECT id, name, spreadsheet_id, drive_folder_id, sheet_in_drive"
                " FROM profiles ORDER BY id"
            )
        ]
        pending_by_profile = {
            r["profile_id"]: r["cnt"]
            for r in conn.execute(
                "SELECT profile_id, COUNT(*) cnt FROM transactions"
                " WHERE sync_status='pending' GROUP BY profile_id"
            )
        }
    profiles_info = [
        {
            "id": p["id"],
            "name": p["name"],
            "sheet_url": (
                f"https://docs.google.com/spreadsheets/d/{p['spreadsheet_id']}"
                if p["spreadsheet_id"] else None
            ),
            "drive_folder_url": (
                f"https://drive.google.com/drive/folders/{p['drive_folder_id']}"
                if p["drive_folder_id"] else None
            ),
            "sheet_in_drive": bool(p["sheet_in_drive"]),
            "pending": pending_by_profile.get(p["id"], 0),
        }
        for p in all_profiles
    ]
    return {
        "configured": gc.is_configured(),
        "connected": gc.is_connected(),
        "redirect_uri": config.google_redirect_uri,
        "folder_name": gc.get_folder_base_name(),
        "scope_version": gc.get_scope_version(),
        "profiles": profiles_info,
        **sync_status_fn(),
    }


@router.post("/api/google/credentials")
async def set_credentials(body: CredentialsIn):
    await asyncio.to_thread(gc.save_client_creds, body.client_id, body.client_secret)
    from ..db import get_db
    from ..services import audit
    with get_db() as conn:
        audit.record(conn, "google_credentials_saved", channel="ui")
    return {"configured": True}


@router.post("/api/google/profiles/{profile_id}/reset-sheet")
async def reset_sheet(profile_id: int):
    """Clear Drive placement for a profile so next sync creates fresh folder + sheet.

    Clears spreadsheet_id, drive_folder_id, and sheet_in_drive so reconcile
    creates a new profile subfolder inside the app root and a new sheet inside it.
    """
    from ..db import get_db
    from ..services import audit
    with get_db() as conn:
        updated = conn.execute(
            "UPDATE profiles SET spreadsheet_id=NULL, drive_folder_id=NULL,"
            " sheet_in_drive=0 WHERE id=?",
            (profile_id,),
        ).rowcount
        if not updated:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Profile not found")
        audit.record(conn, "drive_reset", channel="ui", ref=profile_id,
                     detail="spreadsheet_id + drive_folder_id cleared; next sync creates fresh structure")
    return {"reset": True}


@router.post("/api/google/folder-name")
async def set_folder_name(body: FolderNameIn):
    await asyncio.to_thread(gc.set_folder_base_name, body.name)
    # Reset profile Drive placement so next sync creates subfolders in the new root.
    # Keep spreadsheet_id — old sheet still works; sheet_in_drive=0 shows Reset button.
    from ..db import get_db
    from ..services import audit
    with get_db() as conn:
        conn.execute(
            "UPDATE profiles SET drive_folder_id=NULL, sheet_in_drive=0"
        )
        audit.record(conn, "folder_name_changed", channel="ui",
                     detail=f"name='{body.name.strip()}'; profile Drive folders cleared for re-creation")
    return {"folder_name": body.name.strip()}


@router.get("/api/google/auth")
async def auth():
    return RedirectResponse(gc.build_auth_url())


@router.get("/api/google/callback")
async def callback(code: str):
    await asyncio.to_thread(gc.exchange_code, code)
    from ..services import sync
    await asyncio.to_thread(sync._safe_reconcile)
    return RedirectResponse(f"{config.web_origin}/settings?google=connected")
