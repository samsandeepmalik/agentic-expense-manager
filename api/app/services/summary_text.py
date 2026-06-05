"""Plain-text summaries for WhatsApp."""

from __future__ import annotations

from ..db import get_db
from .transactions import dashboard_data


def weekly_summary_text() -> str:
    with get_db() as conn:
        data = dashboard_data(conn, None)  # current month
    metrics = data["metrics"]
    lines = [
        "📊 Weekly summary (this month so far)",
        f"Income: ${metrics['income']:.2f}",
        f"Expenses: ${metrics['expenses']:.2f}",
        f"Net: ${metrics['net']:.2f}",
    ]
    top = sorted(data["by_category"].items(), key=lambda kv: -kv[1])[:3]
    if top:
        lines.append("Top spending:")
        lines += [f"  • {name}: ${value:.2f}" for name, value in top]
    return "\n".join(lines)
