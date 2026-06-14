"""Legacy-DB migration: init_db() must upgrade a pre-existing DB in place.

conftest's `db_path` builds a FRESH DB from SCHEMA, so the ALTER-TABLE
migration branches never run there. These tests build an OLD-shape DB and
run init_db() against it — the path that bit us in Docker (SQLite forbids
ADD COLUMN with REFERENCES + non-NULL default).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def _legacy_db(path: Path) -> None:
    """A pre-profiles-era DB: imports/audit_log without profile_id, with a row."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE profiles (
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE,
          kind TEXT DEFAULT 'personal', spreadsheet_id TEXT,
          drive_folder_id TEXT, created_at TEXT);
        INSERT INTO profiles(id, name) VALUES (1, 'Personal');
        CREATE TABLE imports (
          id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT NOT NULL,
          status TEXT DEFAULT 'parsing', rows TEXT DEFAULT '[]', error TEXT,
          created_at TEXT);
        INSERT INTO imports(filename) VALUES ('legacy.csv');
        CREATE TABLE audit_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, channel TEXT DEFAULT '',
          event TEXT NOT NULL, ref TEXT DEFAULT '', detail TEXT DEFAULT '');
        INSERT INTO audit_log(event) VALUES ('legacy_event');
        """
    )
    conn.commit()
    conn.close()


def test_init_db_migrates_legacy_imports_and_audit(tmp_path, monkeypatch):
    db_file = tmp_path / "legacy.db"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    _legacy_db(db_file)

    from app import db
    db.DB_PATH = db_file
    db.init_db()  # must NOT raise (the REFERENCES+default ALTER would)

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    import_cols = {r["name"] for r in conn.execute("PRAGMA table_info(imports)")}
    audit_cols = {r["name"] for r in conn.execute("PRAGMA table_info(audit_log)")}
    assert "profile_id" in import_cols
    assert "profile_id" in audit_cols
    # legacy import row backfilled to profile 1; legacy audit row stays global (NULL)
    assert conn.execute("SELECT profile_id FROM imports WHERE filename='legacy.csv'"
                        ).fetchone()["profile_id"] == 1
    assert conn.execute("SELECT profile_id FROM audit_log WHERE event='legacy_event'"
                        ).fetchone()["profile_id"] is None
    conn.close()


def test_init_db_adds_notes_column_to_legacy(tmp_path, monkeypatch):
    """Legacy transactions table without a notes column gets it (default '')."""
    db_file = tmp_path / "notes.db"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app import db
    db.DB_PATH = db_file
    db.init_db()
    # Drop the column the schema-fresh DB already has by rebuilding an old shape.
    legacy = sqlite3.connect(db_file)
    legacy.executescript(
        """
        ALTER TABLE transactions RENAME TO _txn_new;
        CREATE TABLE transactions (
          id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL,
          type TEXT NOT NULL, category_id INTEGER NOT NULL,
          description TEXT NOT NULL DEFAULT '', merchant TEXT NOT NULL DEFAULT '',
          amount REAL NOT NULL, tax_breakdown TEXT NOT NULL DEFAULT '{}',
          total REAL NOT NULL, counted REAL NOT NULL, image_path TEXT,
          source TEXT NOT NULL DEFAULT 'ui', external_ref TEXT,
          sync_status TEXT NOT NULL DEFAULT 'n/a',
          profile_id INTEGER NOT NULL DEFAULT 1,
          created_at TEXT, updated_at TEXT);
        INSERT INTO transactions(date,type,category_id,amount,total,counted)
          VALUES ('2026-06-01','expense',1,1,1,1);
        DROP TABLE _txn_new;
        """
    )
    legacy.commit()
    legacy.close()

    db.init_db()  # migration must add notes column without raising

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(transactions)")}
    assert "notes" in cols
    assert conn.execute("SELECT notes FROM transactions").fetchone()["notes"] == ""
    conn.close()


def test_init_db_idempotent_on_legacy(tmp_path, monkeypatch):
    db_file = tmp_path / "legacy.db"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    _legacy_db(db_file)
    from app import db
    db.DB_PATH = db_file
    db.init_db()
    db.init_db()  # second run is a no-op, must not raise
