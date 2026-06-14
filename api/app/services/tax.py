"""Tax back-calculation: user enters total paid; components are derived.

amount = total / (1 + sum(rates)/100); component_i = amount * rate_i / 100.
"""

from __future__ import annotations

import json
import sqlite3

from . import profiles as prof_svc


def back_calculate(total: float, components: list[dict], taxable: bool) -> dict:
    if not taxable or not components:
        return {"amount": round(total, 2), "breakdown": {}}
    rate_sum = sum(c["rate"] for c in components)
    amount = total / (1 + rate_sum / 100)
    breakdown = {c["name"]: round(amount * c["rate"] / 100, 2) for c in components}
    return {"amount": round(amount, 2), "breakdown": breakdown}


def active_components(conn: sqlite3.Connection, profile_id: int | None = None) -> list[dict]:
    pid = profile_id if profile_id is not None else prof_svc.active_id(conn)
    row = conn.execute(
        "SELECT components FROM tax_profiles WHERE is_active=1 AND profile_id=?",
        (pid,)).fetchone()
    return json.loads(row["components"]) if row else []
