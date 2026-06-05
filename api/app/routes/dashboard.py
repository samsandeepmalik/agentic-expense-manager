"""Dashboard data: summary, trend and recent transactions."""

from __future__ import annotations

import asyncio
from datetime import date

from fastapi import APIRouter, Query

from ..services import sheets
from ..services.google_client import spreadsheet_url

router = APIRouter()


def _months_ago(months: int) -> str:
    today = date.today()
    year = today.year
    month = today.month - months
    while month <= 0:
        month += 12
        year -= 1
    return f"{year:04d}-{month:02d}-01"


@router.get("/api/dashboard")
async def dashboard(months: int = Query(default=6, ge=1, le=36)):
    start = _months_ago(months - 1)  # include current month
    summary = await asyncio.to_thread(sheets.summarize, start_date=start)
    transactions = await asyncio.to_thread(sheets.list_transactions, start_date=start)
    recent = sorted(
        transactions, key=lambda t: (t["date"], t["recorded_at"]), reverse=True
    )[:20]
    return {
        "summary": summary,
        "recent": recent,
        "sheet_url": spreadsheet_url(),
    }
