"""SQLite source of truth: connection, schema, seeds, settings helpers."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any

from .config import config

DB_PATH = config.data_dir / "expense.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  type TEXT NOT NULL CHECK(type IN ('income','expense')),
  percent REAL NOT NULL DEFAULT 100,
  taxable INTEGER NOT NULL DEFAULT 1,
  budget_monthly REAL
);
CREATE TABLE IF NOT EXISTS tax_profiles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  components TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS transactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL,
  type TEXT NOT NULL CHECK(type IN ('income','expense')),
  category_id INTEGER NOT NULL REFERENCES categories(id),
  description TEXT NOT NULL DEFAULT '',
  merchant TEXT NOT NULL DEFAULT '',
  amount REAL NOT NULL,
  tax_breakdown TEXT NOT NULL DEFAULT '{}',
  total REAL NOT NULL,
  counted REAL NOT NULL,
  image_path TEXT,
  source TEXT NOT NULL DEFAULT 'ui',
  external_ref TEXT,
  sync_status TEXT NOT NULL DEFAULT 'n/a',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_txn_date ON transactions(date);
CREATE TABLE IF NOT EXISTS recurring_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  template TEXT NOT NULL,
  frequency TEXT NOT NULL CHECK(frequency IN ('weekly','biweekly','monthly')),
  next_run TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1
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
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
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


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_db() as conn:
        conn.executescript(SCHEMA)
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


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
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
