from __future__ import annotations

import asyncio

from fastapi import APIRouter

from ..services import sync as svc

router = APIRouter()


@router.get("/api/sync/status")
async def sync_status():
    return svc.status()


@router.post("/api/sync/now")
async def sync_now():
    # _safe_reconcile records both success and errors to settings so the
    # status endpoint always reflects the last attempt.
    await asyncio.to_thread(svc._safe_reconcile)
    return svc.status()
