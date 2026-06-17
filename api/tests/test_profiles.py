"""Profiles: table seeded, profile_id everywhere, constraint rebuilt, idempotent."""
import sqlite3

from app.db import DB_PATH, get_db, init_db


def _columns(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_profiles_table_seeded(db_path):
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM profiles").fetchall()
    assert len(rows) == 1
    assert rows[0]["name"] == "Personal"
    assert rows[0]["kind"] == "personal"


def test_profile_id_on_all_partitioned_tables(db_path):
    with get_db() as conn:
        for table in ("transactions", "categories", "tax_profiles", "recurring_rules"):
            assert "profile_id" in _columns(conn, table), table


def test_category_names_unique_per_profile(db_path):
    with get_db() as conn:
        conn.execute("INSERT INTO profiles(name, kind) VALUES ('Inc', 'incorporation')")
        pid = conn.execute("SELECT id FROM profiles WHERE name='Inc'").fetchone()["id"]
        # same name as the seeded Personal 'Groceries' must be allowed under Inc
        conn.execute(
            "INSERT INTO categories(name, type, taxable, profile_id) VALUES "
            "('Groceries', 'expense', 1, ?)", (pid,))


def test_migration_idempotent(db_path):
    init_db()
    init_db()  # twice more — no error, still one Personal
    with get_db() as conn:
        count = conn.execute("SELECT COUNT(*) c FROM profiles").fetchone()["c"]
    assert count == 1


def test_create_profile_seeds_defaults(db_path):
    from app.services import profiles as prof_svc
    with get_db() as conn:
        profile = prof_svc.create_profile(conn, "My Inc", "incorporation")
        cats = conn.execute("SELECT COUNT(*) c FROM categories WHERE profile_id=?",
                            (profile["id"],)).fetchone()["c"]
        taxes = conn.execute("SELECT COUNT(*) c FROM tax_profiles WHERE profile_id=?",
                             (profile["id"],)).fetchone()["c"]
    assert cats == 12 and taxes == 3      # same seeds as a fresh DB


def test_active_profile_default_and_switch(db_path):
    from app.services import profiles as prof_svc
    with get_db() as conn:
        assert prof_svc.active_id(conn) == 1
        profile = prof_svc.create_profile(conn, "Inc", "incorporation")
        prof_svc.set_active(conn, profile["id"])
        assert prof_svc.active_id(conn) == profile["id"]


def test_delete_guards(db_path):
    import pytest
    from app.errors import AppError
    from app.services import profiles as prof_svc
    with get_db() as conn:
        with pytest.raises(AppError):          # can't delete the active profile
            prof_svc.delete_profile(conn, 1)
        profile = prof_svc.create_profile(conn, "Inc", "incorporation")
        prof_svc.set_active(conn, profile["id"])
        conn.execute("INSERT INTO transactions(date,type,category_id,amount,total,counted,profile_id) "
                     "VALUES ('2026-06-01','expense',1,5.0,5.0,5.0,?)", (profile["id"],))
        prof_svc.set_active(conn, 1)
        with pytest.raises(AppError):          # has transactions
            prof_svc.delete_profile(conn, profile["id"])


def test_existing_db_upgrade_preserves_custom_rows(tmp_path, monkeypatch):
    import sqlite3
    from app import db as dbmod
    p = tmp_path / "old.db"
    conn = sqlite3.connect(p)
    conn.executescript('''
      CREATE TABLE categories (id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE, type TEXT NOT NULL, percent REAL NOT NULL DEFAULT 100,
        taxable INTEGER NOT NULL DEFAULT 1, budget_monthly REAL);
      CREATE TABLE tax_profiles (id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE, components TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, type TEXT,
        category_id INTEGER, amount REAL, total REAL, counted REAL, tax_breakdown TEXT DEFAULT '{}',
        sync_status TEXT DEFAULT 'n/a');
      CREATE TABLE recurring_rules (id INTEGER PRIMARY KEY AUTOINCREMENT, template TEXT,
        frequency TEXT, next_run TEXT, active INTEGER DEFAULT 1);
      CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
      CREATE TABLE profiles (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL DEFAULT 'personal', spreadsheet_id TEXT, drive_folder_id TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')));
      INSERT INTO categories(name,type) VALUES ('MY_CUSTOM','expense');
    ''')
    conn.commit()
    conn.close()
    monkeypatch.setattr(dbmod, "DB_PATH", p)
    dbmod._migrate_profiles()
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    names = [r["name"] for r in conn.execute("SELECT name FROM categories")]
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(categories)")}
    conn.close()
    assert "MY_CUSTOM" in names           # data preserved
    assert "profile_id" in cols           # upgraded shape


def test_partition_transactions_and_categories(db_path):
    from app.services import profiles as prof_svc
    from app.services import transactions as txn_svc
    from app.services import categories as cat_svc
    with get_db() as conn:
        txn_svc.create_transaction(conn, {"date": "2026-06-01", "type": "expense",
                                          "category": "Groceries", "total": 10.0})
        inc = prof_svc.create_profile(conn, "Inc", "incorporation")
        prof_svc.set_active(conn, inc["id"])
        assert txn_svc.list_transactions(conn) == []            # other profile's txns hidden
        assert all(c["profile_id"] == inc["id"]
                   for c in cat_svc.list_categories(conn))       # own categories only
        txn_svc.create_transaction(conn, {"date": "2026-06-02", "type": "expense",
                                          "category": "Groceries", "total": 99.0})
        assert len(txn_svc.list_transactions(conn)) == 1
        prof_svc.set_active(conn, 1)
        personal = txn_svc.list_transactions(conn)
        assert len(personal) == 1 and personal[0]["total"] == 10.0


def test_tax_profile_per_profile(db_path):
    from app.services import profiles as prof_svc
    from app.services import tax as tax_svc
    from app.services import categories as cat_svc
    with get_db() as conn:
        inc = prof_svc.create_profile(conn, "Inc2", "incorporation")
        prof_svc.set_active(conn, inc["id"])
        cat_svc.save_tax_profile(conn, "Alberta", [{"name": "GST", "rate": 5.0}], True)
        assert [c["name"] for c in tax_svc.active_components(conn)] == ["GST"]
        prof_svc.set_active(conn, 1)
        names = [c["name"] for c in tax_svc.active_components(conn)]
        assert names == ["GST", "QST"]                           # Personal still Quebec


def test_recurring_rule_fires_for_inactive_profile(db_path):
    from app.services import profiles as prof_svc
    from app.services import categories as cat_svc
    from app.services import recurring as rec_svc
    from app.services import transactions as txn_svc
    from datetime import date
    with get_db() as conn:
        inc = prof_svc.create_profile(conn, "IncRec", "incorporation")
        prof_svc.set_active(conn, inc["id"])
        # a category unique to Inc
        cat_svc.upsert_category(conn, "Consulting", "expense", 100.0, True, None)
        rec_svc.create_rule(conn, {"type": "expense", "category": "Consulting",
                                   "total": 100.0}, "monthly", "2026-06-01")
        prof_svc.set_active(conn, 1)            # switch away — scheduler runs regardless
        created = rec_svc.run_due_rules(conn, today=date(2026, 6, 2))
        assert created >= 1                      # must NOT raise category_not_found
        rows = conn.execute(
            "SELECT profile_id, category_id FROM transactions WHERE source='recurring'"
        ).fetchall()
        assert rows and all(r["profile_id"] == inc["id"] for r in rows)
        # category_id must belong to Inc, not Personal
        for r in rows:
            owner = conn.execute("SELECT profile_id FROM categories WHERE id=?",
                                 (r["category_id"],)).fetchone()["profile_id"]
            assert owner == inc["id"]


def test_prompt_loan_column_exists_and_defaults_false(db_path):
    with get_db() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(profiles)")}
        assert "prompt_loan" in cols
        row = conn.execute("SELECT prompt_loan FROM profiles WHERE id=1").fetchone()
        assert row["prompt_loan"] == 0


def test_prompt_loan_migration_idempotent(db_path):
    init_db()
    init_db()
    with get_db() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(profiles)")}
        assert "prompt_loan" in cols
