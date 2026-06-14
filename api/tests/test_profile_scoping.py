import pytest

from app import db
from app.errors import AppError
from app.services import profiles as prof_svc
from app.services import transactions as txn_svc


def _expense(category="Groceries", total=10.0, date="2026-06-01"):
    return {"date": date, "type": "expense", "category": category, "total": total}


def test_imports_and_audit_have_profile_id_column(conn):
    import_cols = {r["name"] for r in conn.execute("PRAGMA table_info(imports)")}
    audit_cols = {r["name"] for r in conn.execute("PRAGMA table_info(audit_log)")}
    assert "profile_id" in import_cols
    assert "profile_id" in audit_cols


def test_audit_recent_scoped_to_active_plus_global(conn):
    from app.services import audit
    audit.record(conn, "evt_p1", profile_id=1)
    inc = prof_svc.create_profile(conn, "Inc", "incorporation")
    audit.record(conn, "evt_p2", profile_id=inc["id"])
    audit.record(conn, "evt_global")  # profile_id None = global
    events = {r["event"] for r in audit.recent(conn, profile_id=1)}
    assert "evt_p1" in events
    assert "evt_global" in events
    assert "evt_p2" not in events


def test_get_delete_transaction_scoped(conn):
    inc = prof_svc.create_profile(conn, "Inc", "incorporation")
    prof_svc.set_active(conn, inc["id"])
    t = txn_svc.create_transaction(conn, _expense())
    prof_svc.set_active(conn, 1)               # other profile's txn invisible now
    with pytest.raises(AppError) as got:
        txn_svc.get_transaction(conn, t["id"])
    assert got.value.code == "transaction_not_found"
    with pytest.raises(AppError):
        txn_svc.delete_transaction(conn, t["id"])
    prof_svc.set_active(conn, inc["id"])
    assert txn_svc.get_transaction(conn, t["id"])["id"] == t["id"]


def test_bulk_delete_scoped(conn):
    inc = prof_svc.create_profile(conn, "Inc", "incorporation")
    prof_svc.set_active(conn, inc["id"])
    t = txn_svc.create_transaction(conn, _expense())
    prof_svc.set_active(conn, 1)
    txn_svc.bulk_action(conn, [t["id"]], "delete")     # no-op across profiles
    prof_svc.set_active(conn, inc["id"])
    assert txn_svc.get_transaction(conn, t["id"])["id"] == t["id"]


def test_update_transaction_scoped(conn):
    inc = prof_svc.create_profile(conn, "Inc", "incorporation")
    prof_svc.set_active(conn, inc["id"])
    t = txn_svc.create_transaction(conn, _expense())
    prof_svc.set_active(conn, 1)
    with pytest.raises(AppError) as got:
        txn_svc.update_transaction(conn, t["id"], {"total": 99.0})
    assert got.value.code == "transaction_not_found"


def test_recurring_rule_scoped(conn):
    from app.services import recurring as rec_svc
    inc = prof_svc.create_profile(conn, "Inc", "incorporation")
    prof_svc.set_active(conn, inc["id"])
    rule = rec_svc.create_rule(conn, {"type": "expense", "category": "Rent",
                                      "total": 100.0}, "monthly", "2026-07-01")
    prof_svc.set_active(conn, 1)
    with pytest.raises(AppError):
        rec_svc.get_rule(conn, rule["id"], profile_id=prof_svc.active_id(conn))
    with pytest.raises(AppError):
        rec_svc.delete_rule(conn, rule["id"], profile_id=prof_svc.active_id(conn))
    from datetime import date
    created = rec_svc.run_due_rules(conn, date(2026, 7, 2))
    assert created >= 1


def test_dedup_scoped_to_profile(conn):
    from app.services import dedup
    txn_svc.create_transaction(conn, _expense(total=50.0, date="2026-06-05"))
    inc = prof_svc.create_profile(conn, "Inc", "incorporation")
    prof_svc.set_active(conn, inc["id"])
    flags = dedup.flag_duplicates(conn, [{"date": "2026-06-05", "total": 50.0}])
    assert flags == [False]
    txn_svc.create_transaction(conn, _expense(total=50.0, date="2026-06-05"))
    flags2 = dedup.flag_duplicates(conn, [{"date": "2026-06-05", "total": 50.0}])
    assert flags2 == [True]


def test_import_addressed_by_id_across_active_profile(conn, db_path):
    # An import carries its OWN profile_id (chosen at upload). It is addressed
    # by id and stays reachable even after the active profile changes — users
    # may import into a non-active book without switching to it first.
    from app.services import imports as imp_svc
    inc = prof_svc.create_profile(conn, "Inc", "incorporation")
    prof_svc.set_active(conn, inc["id"])
    conn.commit()
    cur = conn.execute("INSERT INTO imports(filename, profile_id, status, rows) "
                       "VALUES ('s.csv', ?, 'review', '[]')", (inc["id"],))
    import_id = cur.lastrowid
    conn.commit()
    prof_svc.set_active(conn, 1)
    conn.commit()
    record = imp_svc.get_import(import_id)
    assert record["profile_id"] == inc["id"]


def test_dashboard_budgets_scoped(conn):
    from app.services import categories as cat_svc
    cat_svc.upsert_category(conn, "Groceries", "expense", 100.0, True, 300.0)
    inc = prof_svc.create_profile(conn, "Inc", "incorporation")
    prof_svc.set_active(conn, inc["id"])
    cat_svc.upsert_category(conn, "Groceries", "expense", 100.0, True, 999.0)
    data = txn_svc.dashboard_data(conn, None)
    grocery_budgets = [b for b in data["budgets"] if b["name"] == "Groceries"]
    assert len(grocery_budgets) == 1
    assert grocery_budgets[0]["budget"] == 999.0


def test_delete_category_scoped(conn):
    from app.services import categories as cat_svc
    target = cat_svc.find_category_by_name(conn, "Entertainment", profile_id=1)
    inc = prof_svc.create_profile(conn, "Inc", "incorporation")
    prof_svc.set_active(conn, inc["id"])
    with pytest.raises(AppError) as got:
        cat_svc.delete_category(conn, target["id"])
    assert got.value.code == "category_not_found"
    assert cat_svc.find_category_by_name(conn, "Entertainment", profile_id=1)
