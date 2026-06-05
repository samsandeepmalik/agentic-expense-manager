from app import db


def test_init_creates_tables_and_seeds(conn):
    tables = {
        r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"transactions", "categories", "tax_profiles", "recurring_rules",
            "chat_sessions", "chat_messages", "imports", "settings"} <= tables

    cats = conn.execute("SELECT name, taxable FROM categories").fetchall()
    names = {c["name"]: c["taxable"] for c in cats}
    assert names["Rent"] == 0 and names["Groceries"] == 1
    assert names["Salary"] == 0

    active = conn.execute(
        "SELECT name FROM tax_profiles WHERE is_active=1"
    ).fetchone()
    assert active["name"] == "Quebec"


def test_settings_roundtrip(conn):
    db.set_setting(conn, "foo", {"a": 1})
    assert db.get_setting(conn, "foo") == {"a": 1}
    assert db.get_setting(conn, "missing") is None


def test_connection_pragmas(conn):
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_receipt_link_column_and_migration(tmp_path, monkeypatch):
    from app import db
    db.DB_PATH = tmp_path / "migrate.db"
    db.init_db()
    with db.get_db() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(transactions)")}
        assert "receipt_link" in cols
        # simulate legacy junk-drawer rows, then re-init to migrate
        conn.execute("""INSERT INTO transactions(date,type,category_id,amount,
                        total,counted) VALUES ('2026-06-01','expense',1,1,1,1)""")
        txn_id = conn.execute("SELECT max(id) m FROM transactions").fetchone()["m"]
        db.set_setting(conn, f"receipt_link_{txn_id}", "https://drive/x")
    db.init_db()
    with db.get_db() as conn:
        row = conn.execute("SELECT receipt_link FROM transactions WHERE id=?",
                           (txn_id,)).fetchone()
        assert row["receipt_link"] == "https://drive/x"
        assert db.get_setting(conn, f"receipt_link_{txn_id}") is None
