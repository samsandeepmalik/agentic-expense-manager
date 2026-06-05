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
