"""SQLite source of truth: connection, schema, seeds, settings helpers."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any

from .config import config

DB_PATH = config.data_dir / "expense.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL DEFAULT 'personal'
    CHECK(kind IN ('personal','incorporation','other')),
  spreadsheet_id TEXT,
  drive_folder_id TEXT,
  sheet_in_drive INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS categories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  type TEXT NOT NULL CHECK(type IN ('income','expense')),
  percent REAL NOT NULL DEFAULT 100,
  taxable INTEGER NOT NULL DEFAULT 1,
  budget_monthly REAL,
  parent_id INTEGER NOT NULL DEFAULT 0,
  profile_id INTEGER NOT NULL DEFAULT 1 REFERENCES profiles(id),
  UNIQUE(name, profile_id, parent_id)
);
CREATE TABLE IF NOT EXISTS tax_profiles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  components TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 0,
  profile_id INTEGER NOT NULL DEFAULT 1 REFERENCES profiles(id),
  UNIQUE(name, profile_id)
);
CREATE TABLE IF NOT EXISTS transactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL,
  type TEXT NOT NULL CHECK(type IN ('income','expense')),
  category_id INTEGER NOT NULL REFERENCES categories(id),
  description TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  merchant TEXT NOT NULL DEFAULT '',
  amount REAL NOT NULL,
  tax_breakdown TEXT NOT NULL DEFAULT '{}',
  total REAL NOT NULL,
  counted REAL NOT NULL,
  image_path TEXT,
  source TEXT NOT NULL DEFAULT 'ui',
  external_ref TEXT,
  sync_status TEXT NOT NULL DEFAULT 'n/a',
  profile_id INTEGER NOT NULL DEFAULT 1 REFERENCES profiles(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_txn_date ON transactions(date);
CREATE TABLE IF NOT EXISTS recurring_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  template TEXT NOT NULL,
  frequency TEXT NOT NULL CHECK(frequency IN ('weekly','biweekly','monthly')),
  next_run TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  profile_id INTEGER NOT NULL DEFAULT 1 REFERENCES profiles(id)
);
CREATE TABLE IF NOT EXISTS chat_sessions (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT 'New chat',
  channel TEXT NOT NULL DEFAULT 'ui',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS chat_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS imports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  filename TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'parsing',
  rows TEXT NOT NULL DEFAULT '[]',
  error TEXT,
  profile_id INTEGER NOT NULL DEFAULT 1 REFERENCES profiles(id),
  channel TEXT NOT NULL DEFAULT 'import',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL DEFAULT (datetime('now')),
  channel TEXT NOT NULL DEFAULT '',
  event TEXT NOT NULL,
  ref TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT '',
  profile_id INTEGER
);
"""

DEFAULT_CATEGORIES = [
    # (name, type, taxable)
    ("Groceries", "expense", 1), ("Dining", "expense", 1),
    ("Transport", "expense", 1), ("Petrol", "expense", 1),
    ("Utilities", "expense", 1), ("Rent", "expense", 0),
    ("Health", "expense", 1), ("Entertainment", "expense", 1),
    ("Other", "expense", 1),
    ("Salary", "income", 0), ("Business", "income", 1),
    ("Other Income", "income", 0),
]

TAX_PRESETS = [
    ("Quebec", [{"name": "GST", "rate": 5.0}, {"name": "QST", "rate": 9.975}], 1),
    ("Ontario", [{"name": "HST", "rate": 13.0}], 0),
    ("Alberta", [{"name": "GST", "rate": 5.0}], 0),
]


def _seed_default_profile(conn) -> None:
    """Seed Personal (id=1) if absent, carrying any legacy Google targets from
    the settings table. Must run BEFORE category/tax seeds (they FK to profile 1)."""
    if conn.execute("SELECT COUNT(*) c FROM profiles").fetchone()["c"]:
        return
    conn.execute(
        "INSERT INTO profiles(id, name, kind) VALUES (1, 'Personal', 'personal')")
    for key in ("spreadsheet_id", "drive_folder_id"):
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row:
            conn.execute(f"UPDATE profiles SET {key}=? WHERE id=1",
                         (json.loads(row["value"]),))


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")  # persistent DB-file property
        conn.executescript(SCHEMA)
        _seed_default_profile(conn)  # FIRST: categories/tax_profiles FK to profile 1
        if conn.execute("SELECT COUNT(*) c FROM categories").fetchone()["c"] == 0:
            conn.executemany(
                "INSERT INTO categories(name, type, taxable) VALUES (?,?,?)",
                DEFAULT_CATEGORIES,
            )
        if conn.execute("SELECT COUNT(*) c FROM tax_profiles").fetchone()["c"] == 0:
            conn.executemany(
                "INSERT INTO tax_profiles(name, components, is_active) VALUES (?,?,?)",
                [(n, json.dumps(c), a) for n, c, a in TAX_PRESETS],
            )
        # Migration: sheet_in_drive flag on profiles (tracks Drive folder placement)
        prof_cols = {r["name"] for r in conn.execute("PRAGMA table_info(profiles)")}
        if "sheet_in_drive" not in prof_cols:
            conn.execute(
                "ALTER TABLE profiles ADD COLUMN "
                "sheet_in_drive INTEGER NOT NULL DEFAULT 0")
        # Migration: receipt_link column (was settings junk-drawer keys)
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(transactions)")}
        if "receipt_link" not in columns:
            conn.execute("ALTER TABLE transactions ADD COLUMN receipt_link TEXT")
        if "loan" not in columns:
            conn.execute(
                "ALTER TABLE transactions ADD COLUMN loan INTEGER NOT NULL DEFAULT 0")
        if "notes" not in columns:
            conn.execute(
                "ALTER TABLE transactions ADD COLUMN notes TEXT NOT NULL DEFAULT ''")
        legacy = conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE 'receipt_link_%'"
        ).fetchall()
        for row in legacy:
            txn_id = row["key"].removeprefix("receipt_link_")
            if txn_id.isdigit():
                conn.execute("UPDATE transactions SET receipt_link=? WHERE id=?",
                             (json.loads(row["value"]), int(txn_id)))
            conn.execute("DELETE FROM settings WHERE key=?", (row["key"],))
        # Migration: profile_id on imports (scopes statement imports per profile)
        import_cols = {r["name"] for r in conn.execute("PRAGMA table_info(imports)")}
        if "profile_id" not in import_cols:
            # SQLite forbids ADD COLUMN with REFERENCES + a non-NULL default, so
            # the FK lives only in SCHEMA (fresh DBs); legacy rows default to 1.
            conn.execute("ALTER TABLE imports ADD COLUMN "
                         "profile_id INTEGER NOT NULL DEFAULT 1")
        if "channel" not in import_cols:
            conn.execute("ALTER TABLE imports ADD COLUMN "
                         "channel TEXT NOT NULL DEFAULT 'import'")
        # Migration: profile_id on audit_log (nullable; NULL = global event e.g. sync)
        audit_cols = {r["name"] for r in conn.execute("PRAGMA table_info(audit_log)")}
        if "profile_id" not in audit_cols:
            conn.execute("ALTER TABLE audit_log ADD COLUMN profile_id INTEGER")
    _migrate_profiles()  # own connection: PRAGMA fk=OFF needs autocommit, not get_db's txn


def _atomic_rebuild(conn, table: str, create_sql: str, copy_sql: str) -> None:
    """Rebuild `table` to a new shape inside one BEGIN IMMEDIATE/COMMIT (crash =
    rollback to original). Recovers an orphaned `<table>_new` scratch table left
    by an interrupted run rather than dropping it."""
    scratch = f"{table}_new"
    has_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    has_scratch = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (scratch,)
    ).fetchone()
    if not has_table and has_scratch:
        # Old half-done rebuild left only the scratch copy alive — recover it.
        conn.execute(f"ALTER TABLE {scratch} RENAME TO {table}")
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(f"DROP TABLE IF EXISTS {scratch}")  # inside txn: rolls back on crash
        conn.execute(create_sql)
        conn.execute(copy_sql)
        conn.execute(f"DROP TABLE {table}")
        conn.execute(f"ALTER TABLE {scratch} RENAME TO {table}")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _migrate_profiles() -> None:
    """Upgrade an existing pre-profiles DB in place (guarded, no-op on a fresh DB).
    Runs on a dedicated autocommit connection so PRAGMA foreign_keys=OFF takes
    effect (it is ignored inside an open transaction)."""
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        _seed_default_profile(conn)  # this path may run without init_db (see tests)
        for table in ("transactions", "recurring_rules"):
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if "profile_id" not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN profile_id INTEGER "
                             f"NOT NULL DEFAULT 1 REFERENCES profiles(id)")
        cat_cols = {r["name"] for r in conn.execute("PRAGMA table_info(categories)")}
        if "profile_id" not in cat_cols:
            _atomic_rebuild(
                conn, "categories",
                """CREATE TABLE categories_new (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     name TEXT NOT NULL,
                     type TEXT NOT NULL CHECK(type IN ('income','expense')),
                     percent REAL NOT NULL DEFAULT 100,
                     taxable INTEGER NOT NULL DEFAULT 1,
                     budget_monthly REAL,
                     profile_id INTEGER NOT NULL DEFAULT 1 REFERENCES profiles(id),
                     UNIQUE(name, profile_id)
                   )""",
                """INSERT INTO categories_new(id, name, type, percent, taxable, budget_monthly)
                     SELECT id, name, type, percent, taxable, budget_monthly FROM categories""",
            )
        cat_cols2 = {r["name"] for r in conn.execute("PRAGMA table_info(categories)")}
        if "parent_id" not in cat_cols2:
            _atomic_rebuild(
                conn, "categories",
                """CREATE TABLE categories_new (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     name TEXT NOT NULL,
                     type TEXT NOT NULL CHECK(type IN ('income','expense')),
                     percent REAL NOT NULL DEFAULT 100,
                     taxable INTEGER NOT NULL DEFAULT 1,
                     budget_monthly REAL,
                     parent_id INTEGER NOT NULL DEFAULT 0,
                     profile_id INTEGER NOT NULL DEFAULT 1 REFERENCES profiles(id),
                     UNIQUE(name, profile_id, parent_id)
                   )""",
                """INSERT INTO categories_new(id, name, type, percent, taxable,
                     budget_monthly, profile_id)
                     SELECT id, name, type, percent, taxable, budget_monthly,
                            profile_id FROM categories""",
            )
        tax_cols = {r["name"] for r in conn.execute("PRAGMA table_info(tax_profiles)")}
        if "profile_id" not in tax_cols:
            _atomic_rebuild(
                conn, "tax_profiles",
                """CREATE TABLE tax_profiles_new (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     name TEXT NOT NULL,
                     components TEXT NOT NULL,
                     is_active INTEGER NOT NULL DEFAULT 0,
                     profile_id INTEGER NOT NULL DEFAULT 1 REFERENCES profiles(id),
                     UNIQUE(name, profile_id)
                   )""",
                """INSERT INTO tax_profiles_new(id, name, components, is_active)
                     SELECT id, name, components, is_active FROM tax_profiles""",
            )
    finally:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.close()


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_setting(conn, key: str) -> Any:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else None


def set_setting(conn, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value)),
    )
