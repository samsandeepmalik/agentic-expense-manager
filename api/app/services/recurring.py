"""Recurring transaction rules: auto-record on schedule."""

from __future__ import annotations

import calendar
import json
import sqlite3
from datetime import date, timedelta

from . import transactions as txn_svc


def next_run_after(current: date, frequency: str) -> date:
    if frequency == "weekly":
        return current + timedelta(days=7)
    if frequency == "biweekly":
        return current + timedelta(days=14)
    # monthly: same day next month, clamped
    year = current.year + (current.month // 12)
    month = current.month % 12 + 1
    day = min(current.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def create_rule(conn, template: dict, frequency: str, next_run: str) -> dict:
    cursor = conn.execute(
        "INSERT INTO recurring_rules(template, frequency, next_run) VALUES (?,?,?)",
        (json.dumps(template), frequency, next_run),
    )
    return get_rule(conn, cursor.lastrowid)


def get_rule(conn, rule_id: int) -> dict:
    row = conn.execute("SELECT * FROM recurring_rules WHERE id=?", (rule_id,)).fetchone()
    rule = dict(row)
    rule["template"] = json.loads(rule["template"])
    rule["active"] = bool(rule["active"])
    return rule


def list_rules(conn) -> list[dict]:
    return [get_rule(conn, r["id"]) for r in
            conn.execute("SELECT id FROM recurring_rules ORDER BY id")]


def update_rule(conn, rule_id: int, changes: dict) -> dict:
    rule = get_rule(conn, rule_id)
    merged = rule | changes
    conn.execute(
        "UPDATE recurring_rules SET template=?, frequency=?, next_run=?, active=? WHERE id=?",
        (json.dumps(merged["template"]), merged["frequency"], merged["next_run"],
         int(merged["active"]), rule_id),
    )
    return get_rule(conn, rule_id)


def delete_rule(conn, rule_id: int) -> None:
    conn.execute("DELETE FROM recurring_rules WHERE id=?", (rule_id,))


def run_due_rules(conn: sqlite3.Connection, today: date | None = None) -> int:
    today = today or date.today()
    created = 0
    for rule in list_rules(conn):
        if not rule["active"]:
            continue
        next_run = date.fromisoformat(rule["next_run"])
        while next_run <= today:                      # catch up missed periods
            txn_svc.create_transaction(conn, rule["template"] | {
                "date": next_run.isoformat(), "source": "recurring",
            })
            created += 1
            next_run = next_run_after(next_run, rule["frequency"])
        if next_run.isoformat() != rule["next_run"]:
            conn.execute("UPDATE recurring_rules SET next_run=? WHERE id=?",
                         (next_run.isoformat(), rule["id"]))
    return created
