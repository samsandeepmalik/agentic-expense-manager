"""Resolve a period query param to an inclusive (start, end) ISO date range.

Accepted: None (current month), "YYYY-MM", "last3", "last6", "ytd",
"YYYY-MM-DD:YYYY-MM-DD".
"""

from __future__ import annotations

import calendar
import re
from datetime import date


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    last = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last:02d}"


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def resolve_period(period: str | None, today: date | None = None) -> tuple[str, str]:
    today = today or date.today()
    if not period:
        return _month_bounds(today.year, today.month)
    if re.fullmatch(r"\d{4}-\d{2}", period):
        year, month = int(period[:4]), int(period[5:7])
        return _month_bounds(year, month)
    if period in ("last3", "last6"):
        months = 3 if period == "last3" else 6
        start_year, start_month = _shift_month(today.year, today.month, -(months - 1))
        return f"{start_year:04d}-{start_month:02d}-01", _month_bounds(today.year, today.month)[1]
    if period == "ytd":
        return f"{today.year:04d}-01-01", _month_bounds(today.year, today.month)[1]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}:\d{4}-\d{2}-\d{2}", period):
        start, end = period.split(":")
        return start, end
    raise ValueError(f"Invalid period: {period}")
