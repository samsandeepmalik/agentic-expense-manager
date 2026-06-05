"""Dashboard data — always renders; zeros on a fresh DB, never errors."""

from __future__ import annotations

from fastapi import APIRouter

from ..db import get_db
from ..services.transactions import dashboard_data

router = APIRouter()


@router.get("/api/dashboard")
async def dashboard(period: str | None = None):
    with get_db() as conn:
        return dashboard_data(conn, period)
