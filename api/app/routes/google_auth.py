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
    from ..services.sync import status as sync_status_fn
    return {
        "configured": bool(config.google_client_id and config.google_client_secret),
        "connected": gc.is_connected(),
        **sync_status_fn(),
    }


@router.get("/api/google/auth")
async def auth():
    return RedirectResponse(gc.build_auth_url())


@router.get("/api/google/callback")
async def callback(code: str):
    await asyncio.to_thread(gc.exchange_code, code)
    from ..services import sync
    await asyncio.to_thread(sync._safe_reconcile)
    return RedirectResponse(f"{config.web_origin}/settings?google=connected")
