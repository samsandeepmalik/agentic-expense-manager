"""Read-only activity feed."""

from __future__ import annotations

from fastapi import APIRouter

from ..db import get_db
from ..services import audit as svc

router = APIRouter()


@router.get("/api/audit")
async def recent(limit: int = 100):
    with get_db() as conn:
        return svc.recent(conn, min(limit, 500))
