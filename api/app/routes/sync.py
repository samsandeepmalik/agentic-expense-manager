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
    return await asyncio.to_thread(svc.reconcile)
