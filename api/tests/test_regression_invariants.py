"""Regression invariants: ambiguous category-name resolution and budget/pie
sub-category roll-up edge cases."""

from datetime import date

import pytest

from app.errors import AppError
from app.services import categories as cat_svc
from app.services import recurring as rec_svc
from app.services import transactions as svc


def _expense(conn, **o):
    data = {"date": "2026-06-05", "type": "expense", "category": "Groceries",
            "total": 50.0, "merchant": "m"}
    data.update(o)
    return svc.create_transaction(conn, data)


# --- Fix 1: recurring scheduler must not starve other rules on one bad rule ---

def test_run_due_rules_isolates_a_failing_rule(conn):
    # Rule A points at an ambiguous name; Rule B is valid. B must still fire,
    # and A must be deactivated with an audit note (not loop forever).
    biz = cat_svc.upsert_category(conn, "Business", "expense", 100, True, None)
    cat_svc.upsert_category(conn, "Travel", "expense", 100, True, None)          # top
    cat_svc.upsert_category(conn, "Travel", "expense", 50, True, None,
                            parent_id=biz["id"])                                  # sub → ambiguous
    yesterday = "2026-06-12"
    rec_svc.create_rule(conn, {"type": "expense", "category": "Travel",
                               "total": 100.0, "merchant": "x"}, "monthly", yesterday)
    rec_svc.create_rule(conn, {"type": "expense", "category": "Groceries",
                               "total": 20.0, "merchant": "y"}, "monthly", yesterday)
    before = len(svc.list_transactions(conn, limit=1000))
    rec_svc.run_due_rules(conn, today=date(2026, 6, 13))
    after = svc.list_transactions(conn, limit=1000)
    # The valid Groceries rule fired despite the broken Travel rule.
    assert any(t["merchant"] == "y" for t in after), "valid rule was starved"
    # The broken rule got deactivated so it can't loop forever.
    rules = rec_svc.list_rules(conn)
    travel_rule = next(r for r in rules if r["template"]["category"] == "Travel")
    assert travel_rule["active"] is False


# --- Fix 2: import approval is per-row fault tolerant ---

def test_approve_import_skips_bad_row_keeps_good(conn, monkeypatch, db_path):
    from app.services import imports as imp
    biz = cat_svc.upsert_category(conn, "Business", "expense", 100, True, None)
    cat_svc.upsert_category(conn, "Travel", "expense", 100, True, None)
    cat_svc.upsert_category(conn, "Travel", "expense", 50, True, None,
                            parent_id=biz["id"])                                  # ambiguous
    conn.execute(
        "INSERT INTO imports(id, filename, status, rows, profile_id) "
        "VALUES (1,'f.csv','review',?,1)",
        (__import__("json").dumps([
            {"date": "2026-06-01", "type": "expense", "category": "Groceries",
             "total": 30.0, "merchant": "good"},
            {"date": "2026-06-02", "type": "expense", "category": "Travel",
             "total": 40.0, "merchant": "bad-ambiguous"},
        ]),))
    conn.commit()
    result = imp.approve_import(1, None)
    assert result["created"] == 1                       # the good row imported
    assert len(result.get("failed", [])) == 1           # the bad row reported, not silent
    rows = svc.list_transactions(conn, limit=1000)
    assert any(t["merchant"] == "good" for t in rows)   # good row survived (no batch rollback)


# --- Fix 3: a child with its OWN budget is not double-counted into the parent ---

def test_budget_no_double_count_when_child_has_own_budget(conn):
    food = cat_svc.upsert_category(conn, "Food", "expense", 100, True, 300.0)
    snacks = cat_svc.upsert_category(conn, "Snacks", "expense", 100, True, 80.0,
                                     parent_id=food["id"])
    _expense(conn, category="Snacks", total=50.0)
    data = svc.dashboard_data(conn, None)
    food_b = next(b for b in data["budgets"] if b["name"] == "Food")
    snacks_b = next(b for b in data["budgets"] if b["name"] == "Snacks")
    assert snacks_b["spent"] == 50.0
    assert food_b["spent"] == 0.0          # NOT also 50 — the budgeted child owns it


def test_budget_still_rolls_up_child_without_budget(conn):
    food = cat_svc.upsert_category(conn, "Food", "expense", 100, True, 300.0)
    cat_svc.upsert_category(conn, "Snacks", "expense", 100, True, None,
                            parent_id=food["id"])       # no own budget
    _expense(conn, category="Snacks", total=50.0)
    data = svc.dashboard_data(conn, None)
    food_b = next(b for b in data["budgets"] if b["name"] == "Food")
    assert food_b["spent"] == 50.0          # rolls up (child has no own budget)


# --- Fix 4: an expense child of an INCOME parent must not leak onto the expense pie ---

def test_pie_does_not_leak_expense_child_under_income_parent(conn):
    sal = cat_svc.upsert_category(conn, "Salary", "income", 100, False, None)
    cat_svc.upsert_category(conn, "SideGig", "expense", 100, True, None,
                            parent_id=sal["id"])        # expense child, income parent
    _expense(conn, category="SideGig", total=20.0)
    data = svc.dashboard_data(conn, None)
    assert "Salary" not in data["by_category"]          # income parent not on expense pie
    assert data["by_category"].get("SideGig") == 20.0   # shows under its own leaf instead


# --- Fix 5: filtering by a parent category name includes its sub-category txns ---

def test_bulk_recategorize_by_category_id(conn):
    t = _expense(conn, category="Groceries")
    dining = cat_svc.find_category_by_name(conn, "Dining")
    n = svc.bulk_action(conn, [t["id"]], "recategorize", category_id=dining["id"])
    assert n == 1
    assert svc.get_transaction(conn, t["id"])["category"] == "Dining"


def test_category_tools_target_given_profile(conn):
    # upsert/list scoped to a non-active profile must not touch the active book.
    from app.services import profiles as prof
    other = prof.create_profile(conn, "Biz", "incorporation")
    cat_svc.upsert_category(conn, "PowerTools", "expense", 100, True, None,
                            profile_id=other["id"])
    active_names = [c["name"] for c in cat_svc.list_categories(conn)]
    other_names = [c["name"] for c in cat_svc.list_categories(conn,
                                                              profile_id=other["id"])]
    assert "PowerTools" in other_names and "PowerTools" not in active_names


def test_whatsapp_has_profile_switch_and_recurring_update():
    from app.agent.tools import build_tools
    tools = build_tools("whatsapp", lambda x: None, "whatsapp")
    names = [t.name for t in tools]
    assert "set_active_profile" in names          # WhatsApp can switch profile
    rec = next(t for t in tools if t.name == "manage_recurring")
    assert "update" in rec.parameters["properties"]["action"]["enum"]


def test_run_due_rules_caps_backfill(conn):
    # A rule whose next_run is years in the past must not back-fill hundreds of
    # transactions in one tick.
    rec_svc.create_rule(conn, {"type": "expense", "category": "Groceries",
                               "total": 10.0, "merchant": "z"}, "monthly",
                        "2015-01-01")
    created = rec_svc.run_due_rules(conn, today=date(2026, 6, 14))
    assert created <= 60          # capped (would otherwise be ~137 months)


def test_sheet_counted_pct_column(conn):
    from app.services import sync
    cat_svc.upsert_category(conn, "Rent", "expense", 20, False, None)   # 20% counted
    txn = svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Rent",
        "total": 2409.0, "merchant": "landlord"})
    assert txn["counted"] == 481.8                      # 20% of 2409
    cols = sync._resolve_columns(txn["profile_id"], ["GST", "QST"])
    headers = sync._build_headers(cols)
    assert "Counted %" in headers
    row = sync._build_row(txn, cols, {"receipt_name": "", "receipt_link": ""})
    assert row[headers.index("Counted %")] == "20%"     # the percentage is shown
    assert row[headers.index("Counted")] == 481.8       # alongside the result
    assert row[headers.index("Total")] == 2409.0        # and the base paid


def test_list_by_parent_category_includes_children(conn):
    transport = cat_svc.upsert_category(conn, "Transport", "expense", 100, True, None)
    cat_svc.upsert_category(conn, "Fuel", "expense", 100, True, None,
                            parent_id=transport["id"])
    _expense(conn, category="Fuel", total=30.0, merchant="gas")
    hits = svc.list_transactions(conn, category="Transport")
    assert any(t["merchant"] == "gas" for t in hits)    # parent filter sees the child txn
