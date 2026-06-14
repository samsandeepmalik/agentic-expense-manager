"""Recurring transaction rules: auto-record on schedule."""

from __future__ import annotations

import calendar
import json
import sqlite3
from datetime import date, timedelta

from . import profiles as prof_svc
from . import transactions as txn_svc
from ..errors import AppError

# Max transactions a single rule may back-fill in one scheduler tick — protects
# against a next_run set far in the past generating an unbounded burst.
_MAX_CATCH_UP = 60


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


def create_rule(conn, template: dict, frequency: str, next_run: str,
                profile_id: int | None = None) -> dict:
    pid = profile_id if profile_id is not None else prof_svc.active_id(conn)
    cursor = conn.execute(
        "INSERT INTO recurring_rules(template, frequency, next_run, profile_id) "
        "VALUES (?,?,?,?)",
        (json.dumps(template), frequency, next_run, pid),
    )
    return get_rule(conn, cursor.lastrowid)


def get_rule(conn, rule_id: int, profile_id: int | None = None) -> dict:
    sql = "SELECT * FROM recurring_rules WHERE id=?"
    params: list = [rule_id]
    if profile_id is not None:
        sql += " AND profile_id=?"; params.append(profile_id)
    row = conn.execute(sql, params).fetchone()
    if not row:
        raise AppError("rule_not_found", "Recurring rule not found", 404)
    rule = dict(row)
    rule["template"] = json.loads(rule["template"])
    rule["active"] = bool(rule["active"])
    return rule


def list_rules(conn, profile_id: int | None = None) -> list[dict]:
    pid = profile_id if profile_id is not None else prof_svc.active_id(conn)
    return [get_rule(conn, r["id"]) for r in
            conn.execute("SELECT id FROM recurring_rules WHERE profile_id=? ORDER BY id",
                         (pid,))]


def update_rule(conn, rule_id: int, changes: dict,
                profile_id: int | None = None) -> dict:
    rule = get_rule(conn, rule_id, profile_id=profile_id)
    merged = rule | changes
    sql = ("UPDATE recurring_rules SET template=?, frequency=?, next_run=?, active=? "
           "WHERE id=?")
    params = [json.dumps(merged["template"]), merged["frequency"], merged["next_run"],
              int(merged["active"]), rule_id]
    if profile_id is not None:
        sql += " AND profile_id=?"; params.append(profile_id)
    conn.execute(sql, params)
    return get_rule(conn, rule_id, profile_id=profile_id)


def delete_rule(conn, rule_id: int, profile_id: int | None = None) -> None:
    sql = "DELETE FROM recurring_rules WHERE id=?"
    params: list = [rule_id]
    if profile_id is not None:
        sql += " AND profile_id=?"; params.append(profile_id)
    cursor = conn.execute(sql, params)
    if cursor.rowcount == 0:
        raise AppError("rule_not_found", "Recurring rule not found", 404)


def run_due_rules(conn: sqlite3.Connection, today: date | None = None) -> int:
    from . import audit
    today = today or date.today()
    created = 0
    for r in conn.execute("SELECT id FROM recurring_rules ORDER BY id"):
        rule = get_rule(conn, r["id"])
        if not rule["active"]:
            continue
        # Isolate each rule: one broken rule (e.g. a template whose category no
        # longer resolves) must NOT stop next_run advancing for itself OR starve
        # every later rule. On failure, deactivate it + audit so the scheduler
        # doesn't retry it forever and the user can see + fix it.
        try:
            next_run = date.fromisoformat(rule["next_run"])
            # Cap catch-up so a rule whose next_run was set far in the past can't
            # back-fill an unbounded number of transactions in a single tick.
            catch_up = 0
            while next_run <= today and catch_up < _MAX_CATCH_UP:
                txn_svc.create_transaction(conn, rule["template"] | {
                    "date": next_run.isoformat(), "source": "recurring",
                    "profile_id": rule["profile_id"],
                })
                created += 1
                catch_up += 1
                next_run = next_run_after(next_run, rule["frequency"])
            if next_run.isoformat() != rule["next_run"]:
                conn.execute("UPDATE recurring_rules SET next_run=? WHERE id=?",
                             (next_run.isoformat(), rule["id"]))
        except Exception as exc:  # noqa: BLE001 — one bad rule can't break the rest
            conn.execute("UPDATE recurring_rules SET active=0 WHERE id=?",
                         (rule["id"],))
            audit.record(conn, "recurring_failed", channel="recurring",
                         ref=str(rule["id"]),
                         detail=f"rule deactivated: {exc}",
                         profile_id=rule["profile_id"])
    return created
