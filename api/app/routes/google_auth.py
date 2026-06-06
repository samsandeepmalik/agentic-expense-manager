"""Google OAuth connect flow."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from ..config import config
from ..services import google_client as gc

router = APIRouter()


class FolderIn(BaseModel):
    folder: str


@router.get("/api/google/status")
async def status():
    from ..db import get_db, get_setting
    from ..services.sync import status as sync_status_fn
    from ..settings_keys import DRIVE_FOLDER_ID
    with get_db() as conn:
        folder_id = get_setting(conn, DRIVE_FOLDER_ID)
    return {
        "configured": bool(config.google_client_id and config.google_client_secret),
        "connected": gc.is_connected(),
        "folder_id": folder_id,
        **sync_status_fn(),
    }


@router.get("/api/google/folders")
async def list_folders(parent: str | None = None):
    folders = await asyncio.to_thread(gc.list_folders, parent)
    return {"folders": folders}


@router.post("/api/google/folder")
async def set_folder(body: FolderIn):
    return await asyncio.to_thread(gc.set_drive_folder, body.folder)


@router.get("/api/google/auth")
async def auth():
    return RedirectResponse(gc.build_auth_url())


@router.get("/api/google/callback")
async def callback(code: str):
    await asyncio.to_thread(gc.exchange_code, code)
    from ..services import sync
    await asyncio.to_thread(sync._safe_reconcile)
    return RedirectResponse(f"{config.web_origin}/settings?google=connected")
