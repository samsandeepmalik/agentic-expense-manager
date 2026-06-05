"""Google OAuth connect flow."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from ..config import config
from ..services import google_client as gc

router = APIRouter()


@router.get("/api/google/status")
async def status():
    connected = gc.is_connected()
    ready = False
    if connected:
        try:
            await asyncio.to_thread(gc.ensure_spreadsheet)
            ready = True
        except Exception:  # noqa: BLE001
            ready = False
    return {
        "configured": bool(config.google_client_id and config.google_client_secret),
        "connected": connected,
        "ready": ready,
        "sheet_url": gc.spreadsheet_url(),
    }


@router.get("/api/google/auth")
async def auth():
    return RedirectResponse(gc.build_auth_url())


@router.get("/api/google/callback")
async def callback(code: str):
    await asyncio.to_thread(gc.exchange_code, code)
    # Bootstrap spreadsheet + folder right away
    await asyncio.to_thread(gc.ensure_spreadsheet)
    await asyncio.to_thread(gc.ensure_drive_folder)
    return RedirectResponse(f"{config.web_origin}/?google=connected")
