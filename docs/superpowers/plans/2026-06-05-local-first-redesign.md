# Local-First Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild Expense Manager around a local SQLite source of truth with a warm-themed frontend rewrite, per the approved spec `docs/superpowers/specs/2026-06-05-ui-ux-redesign-design.md`.

**Architecture:** Backend refactored in place — SQLite replaces Google-Sheets-as-storage; Google becomes an optional one-way sync module; Pi agent runtime, Claude Max OAuth provider, neonize WhatsApp, and NVIDIA OCR are kept unchanged. Frontend (`web/src`) is deleted and rebuilt (react-router + TanStack Query + recharts, warm theme, desktop-only).

**Tech Stack:** Python 3.13, FastAPI, sqlite3 (stdlib), pi-agent SDK, neonize, openpyxl, pypdf, React 18, TypeScript, Vite, react-router-dom, @tanstack/react-query, recharts.

**DO NOT TOUCH (verified live, fragile):**
- `api/app/agent/anthropic_provider.py` — Claude Max OAuth (Bearer + `anthropic-beta: oauth-2025-04-20` + Claude Code system block)
- neonize wiring in `api/app/channels/whatsapp.py` — QR arrives via `client.event.qr(callback)`, NOT `QREv`
- `api/app/services/ocr.py` — NVIDIA NIM client
- `api/app/routes/google_auth.py` OAuth flow (only its post-connect bootstrap changes)

**Conventions used throughout:**
- All new backend tests live under `api/tests/`; run with `cd api && poetry run pytest`.
- Every service function takes an explicit `conn: sqlite3.Connection` first arg (no hidden globals); routes obtain one via `with get_db() as conn:`.
- Money rounded with `round(x, 2)` at service boundaries.
- API errors raised as `AppError(code, message, status)` — never raw exceptions to clients.

---

## File Structure (target)

```
api/
  app/
    config.py                 (modify: drop sheet/folder env reliance for reads)
    db.py                     (new: connection, schema, seeds, init)
    errors.py                 (new: AppError + FastAPI handler)
    main.py                   (rewrite: routers, lifespan w/ schedulers, error handler)
    services/
      tax.py                  (new: back-calculation engine)
      periods.py              (new: period param → date range)
      categories.py           (new: categories + tax profiles CRUD)
      transactions.py         (new: CRUD, bulk, CSV, dashboard aggregates)
      dedup.py                (new: duplicate matcher)
      recurring.py            (new: rules + next-run logic)
      chat_store.py           (new: sessions/messages persistence)
      imports.py              (new: file parse → agent rows → approve)
      sync.py                 (new: one-way Google push/reconcile)
      summary_text.py         (new: WhatsApp weekly summary text)
      google_client.py        (modify: keep OAuth/Drive upload; drop ensure_spreadsheet-as-storage)
      receipts.py             (modify: save image locally; Drive upload moves to sync)
      ocr.py                  (KEEP)
      store.py                (delete after migration — replaced by settings table)
      sheets.py               (delete after migration — replaced by transactions.py)
    agent/
      runtime.py              (modify: persistent sessions, tools get conn factory)
      tools.py                (rewrite: SQLite-backed + manage_budgets, manage_recurring)
      prompts.py              (modify: budgets/recurring mention; unchanged structure)
      anthropic_provider.py   (KEEP)
    routes/
      dashboard.py            (rewrite)
      transactions.py         (new)
      categories.py           (rewrite: + taxable/budget; + tax profiles)
      recurring.py            (new)
      imports.py              (new)
      chat.py                 (rewrite: sessions + SSE)
      sync.py                 (new)
      whatsapp.py             (KEEP)
      google_auth.py          (modify: callback enables sync instead of creating sheet-storage)
    channels/whatsapp.py      (modify: handler reply format only — wiring untouched)
  tests/
    conftest.py               (new: temp-DB fixture)
    test_tax.py  test_periods.py  test_transactions.py  test_dedup.py
    test_recurring.py  test_api.py  test_chat_store.py  test_sync.py
  pyproject.toml              (modify: + openpyxl, pypdf, pytest)
web/
  src/                        (delete all; rebuild)
    main.tsx  App.tsx  api.ts  theme.css
    components/ TopBar.tsx QuickAdd.tsx BudgetRail.tsx Charts.tsx
                RecentTable.tsx Lightbox.tsx ChatBubble.tsx GenUI.tsx
                ChatThread.tsx ImportReview.tsx
    pages/      Dashboard.tsx Transactions.tsx Chat.tsx Settings.tsx
  package.json                (modify: + react-router-dom @tanstack/react-query)
```

---

# PART A — BACKEND

### Task 1: SQLite foundation (`db.py`)

**Files:**
- Create: `api/app/db.py`
- Create: `api/tests/conftest.py`
- Create: `api/tests/test_db.py`
- Modify: `api/pyproject.toml` (add dev deps)

- [x] **Step 1: Add test deps**

In `api/pyproject.toml` add to `[project]` dependencies: `"openpyxl (>=3.1,<4.0)", "pypdf (>=5.0,<6.0)"`, and a dev group:

```toml
[tool.poetry.group.dev.dependencies]
pytest = "^8.3"
pytest-asyncio = "^0.25"
```

Run: `cd api && poetry lock && poetry install --no-root` — expect success.

- [x] **Step 2: Write failing test**

`api/tests/conftest.py`:

```python
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app import db
    db.DB_PATH = path
    db.init_db()
    return path


@pytest.fixture()
def conn(db_path):
    from app import db
    with db.get_db() as connection:
        yield connection
```

`api/tests/test_db.py`:

```python
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
```

- [x] **Step 3: Run to verify failure**

Run: `cd api && poetry run pytest tests/test_db.py -v`
Expected: FAIL (`ModuleNotFoundError: app.db`)

- [x] **Step 4: Implement `api/app/db.py`**

```python
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
```

- [x] **Step 5: Run tests**

Run: `cd api && poetry run pytest tests/test_db.py -v`
Expected: 2 PASS

- [x] **Step 6: Commit**

```bash
git add api/app/db.py api/tests/ api/pyproject.toml api/poetry.lock
git commit -m "feat(db): SQLite schema, seeds, settings store"
```

---

### Task 2: Error contract (`errors.py`)

**Files:**
- Create: `api/app/errors.py`
- Test: `api/tests/test_api.py` (started here, grown in later tasks)

- [x] **Step 1: Write failing test**

`api/tests/test_api.py`:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.errors import AppError, register_error_handler


def make_app():
    app = FastAPI()
    register_error_handler(app)

    @app.get("/boom")
    def boom():
        raise AppError("not_found", "Thing missing", 404)

    @app.get("/crash")
    def crash():
        raise RuntimeError("secret traceback")

    return app


def test_app_error_contract():
    client = TestClient(make_app(), raise_server_exceptions=False)
    response = client.get("/boom")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Thing missing"}}


def test_unexpected_error_hidden():
    client = TestClient(make_app(), raise_server_exceptions=False)
    response = client.get("/crash")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "internal"
    assert "secret" not in body["error"]["message"]
```

- [x] **Step 2: Run to verify failure**

Run: `cd api && poetry run pytest tests/test_api.py -v` → FAIL (no `app.errors`)

- [x] **Step 3: Implement `api/app/errors.py`**

```python
"""Typed errors and the API error contract: {"error": {code, message}}."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def register_error_handler(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        logger.exception("Unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal", "message": "Something went wrong."}},
        )
```

- [x] **Step 4: Run tests** — `poetry run pytest tests/test_api.py -v` → 2 PASS

- [x] **Step 5: Commit**

```bash
git add api/app/errors.py api/tests/test_api.py
git commit -m "feat(api): typed AppError and error contract handler"
```

---

### Task 3: Tax engine (`services/tax.py`)

**Files:**
- Create: `api/app/services/tax.py`
- Test: `api/tests/test_tax.py`

- [x] **Step 1: Write failing tests**

`api/tests/test_tax.py`:

```python
from app.services import tax


QC = [{"name": "GST", "rate": 5.0}, {"name": "QST", "rate": 9.975}]
ON = [{"name": "HST", "rate": 13.0}]


def test_quebec_back_calculation():
    result = tax.back_calculate(114.98, QC, taxable=True)
    assert result["amount"] == 100.0
    assert result["breakdown"] == {"GST": 5.0, "QST": 9.98}


def test_ontario_back_calculation():
    result = tax.back_calculate(113.0, ON, taxable=True)
    assert result["amount"] == 100.0
    assert result["breakdown"] == {"HST": 13.0}


def test_non_taxable_passthrough():
    result = tax.back_calculate(1500.0, QC, taxable=False)
    assert result == {"amount": 1500.0, "breakdown": {}}


def test_active_profile_components(conn):
    components = tax.active_components(conn)
    assert components == QC  # Quebec seeded active
```

- [x] **Step 2: Run** → FAIL (no module)

- [x] **Step 3: Implement `api/app/services/tax.py`**

```python
"""Tax back-calculation: user enters total paid; components are derived.

amount = total / (1 + sum(rates)/100); component_i = amount * rate_i / 100.
"""

from __future__ import annotations

import json
import sqlite3


def back_calculate(total: float, components: list[dict], taxable: bool) -> dict:
    if not taxable or not components:
        return {"amount": round(total, 2), "breakdown": {}}
    rate_sum = sum(c["rate"] for c in components)
    amount = total / (1 + rate_sum / 100)
    breakdown = {c["name"]: round(amount * c["rate"] / 100, 2) for c in components}
    return {"amount": round(amount, 2), "breakdown": breakdown}


def active_components(conn: sqlite3.Connection) -> list[dict]:
    row = conn.execute(
        "SELECT components FROM tax_profiles WHERE is_active=1"
    ).fetchone()
    return json.loads(row["components"]) if row else []
```

Also create empty `api/app/services/__init__.py` if missing (it exists from v1 — leave as is).

- [x] **Step 4: Run** — `poetry run pytest tests/test_tax.py -v` → 4 PASS

- [x] **Step 5: Commit**

```bash
git add api/app/services/tax.py api/tests/test_tax.py
git commit -m "feat(tax): back-calculation engine with active profile"
```

---

### Task 4: Period parser (`services/periods.py`)

**Files:**
- Create: `api/app/services/periods.py`
- Test: `api/tests/test_periods.py`

- [ ] **Step 1: Write failing tests**

`api/tests/test_periods.py`:

```python
from datetime import date

from app.services.periods import resolve_period


TODAY = date(2026, 6, 5)


def test_month():
    assert resolve_period("2026-06", TODAY) == ("2026-06-01", "2026-06-30")


def test_default_is_current_month():
    assert resolve_period(None, TODAY) == ("2026-06-01", "2026-06-30")


def test_last3():
    assert resolve_period("last3", TODAY) == ("2026-04-01", "2026-06-30")


def test_last6_across_year():
    assert resolve_period("last6", date(2026, 2, 10)) == ("2025-09-01", "2026-02-28")


def test_ytd():
    assert resolve_period("ytd", TODAY) == ("2026-01-01", "2026-06-30")


def test_custom():
    assert resolve_period("2026-01-15:2026-03-10", TODAY) == ("2026-01-15", "2026-03-10")
```

- [ ] **Step 2: Run** → FAIL

- [ ] **Step 3: Implement `api/app/services/periods.py`**

```python
"""Resolve a period query param to an inclusive (start, end) ISO date range.

Accepted: None (current month), "YYYY-MM", "last3", "last6", "ytd",
"YYYY-MM-DD:YYYY-MM-DD".
"""

from __future__ import annotations

import calendar
import re
from datetime import date


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    last = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last:02d}"


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def resolve_period(period: str | None, today: date | None = None) -> tuple[str, str]:
    today = today or date.today()
    if not period:
        return _month_bounds(today.year, today.month)
    if re.fullmatch(r"\d{4}-\d{2}", period):
        year, month = int(period[:4]), int(period[5:7])
        return _month_bounds(year, month)
    if period in ("last3", "last6"):
        months = 3 if period == "last3" else 6
        start_year, start_month = _shift_month(today.year, today.month, -(months - 1))
        return f"{start_year:04d}-{start_month:02d}-01", _month_bounds(today.year, today.month)[1]
    if period == "ytd":
        return f"{today.year:04d}-01-01", _month_bounds(today.year, today.month)[1]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}:\d{4}-\d{2}-\d{2}", period):
        start, end = period.split(":")
        return start, end
    raise ValueError(f"Invalid period: {period}")
```

- [ ] **Step 4: Run** — 6 PASS
- [ ] **Step 5: Commit**

```bash
git add api/app/services/periods.py api/tests/test_periods.py
git commit -m "feat(periods): period param resolver"
```

---

### Task 5: Categories + tax profiles service & routes

**Files:**
- Create: `api/app/services/categories.py`
- Rewrite: `api/app/routes/categories.py`
- Test: extend `api/tests/test_api.py`

- [ ] **Step 1: Implement `api/app/services/categories.py`** (logic thin enough to test through API)

```python
"""Categories (+ percent, taxable, budget) and tax profiles."""

from __future__ import annotations

import json
import sqlite3

from ..errors import AppError


def list_categories(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM categories ORDER BY type, name").fetchall()
    return [dict(r) | {"taxable": bool(r["taxable"])} for r in rows]


def upsert_category(conn, name: str, type_: str, percent: float,
                    taxable: bool, budget_monthly: float | None) -> dict:
    if type_ not in ("income", "expense"):
        raise AppError("invalid_type", "Category type must be income or expense")
    percent = max(0.0, min(float(percent), 100.0))
    conn.execute(
        """INSERT INTO categories(name, type, percent, taxable, budget_monthly)
           VALUES (?,?,?,?,?)
           ON CONFLICT(name) DO UPDATE SET type=excluded.type,
             percent=excluded.percent, taxable=excluded.taxable,
             budget_monthly=excluded.budget_monthly""",
        (name.strip(), type_, percent, int(taxable), budget_monthly),
    )
    row = conn.execute("SELECT * FROM categories WHERE name=?", (name.strip(),)).fetchone()
    return dict(row) | {"taxable": bool(row["taxable"])}


def delete_category(conn, category_id: int) -> None:
    used = conn.execute(
        "SELECT COUNT(*) c FROM transactions WHERE category_id=?", (category_id,)
    ).fetchone()["c"]
    if used:
        raise AppError("category_in_use",
                       f"Category has {used} transactions; recategorize them first", 409)
    conn.execute("DELETE FROM categories WHERE id=?", (category_id,))


def get_category(conn, category_id: int) -> dict:
    row = conn.execute("SELECT * FROM categories WHERE id=?", (category_id,)).fetchone()
    if not row:
        raise AppError("category_not_found", "Category not found", 404)
    return dict(row) | {"taxable": bool(row["taxable"])}


def find_category_by_name(conn, name: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM categories WHERE lower(name)=lower(?)", (name.strip(),)
    ).fetchone()
    return (dict(row) | {"taxable": bool(row["taxable"])}) if row else None


def list_tax_profiles(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM tax_profiles ORDER BY id").fetchall()
    return [dict(r) | {"components": json.loads(r["components"]),
                       "is_active": bool(r["is_active"])} for r in rows]


def save_tax_profile(conn, name: str, components: list[dict],
                     activate: bool) -> dict:
    for component in components:
        if not component.get("name") or not isinstance(component.get("rate"), (int, float)):
            raise AppError("invalid_component", "Each component needs name and rate")
    conn.execute(
        """INSERT INTO tax_profiles(name, components) VALUES (?,?)
           ON CONFLICT(name) DO UPDATE SET components=excluded.components""",
        (name.strip(), json.dumps(components)),
    )
    if activate:
        conn.execute("UPDATE tax_profiles SET is_active=0")
        conn.execute("UPDATE tax_profiles SET is_active=1 WHERE name=?", (name.strip(),))
    return [p for p in list_tax_profiles(conn) if p["name"] == name.strip()][0]
```

- [ ] **Step 2: Rewrite `api/app/routes/categories.py`**

```python
"""Category + tax profile configuration endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..db import get_db
from ..services import categories as svc

router = APIRouter()


class CategoryIn(BaseModel):
    name: str = Field(min_length=1)
    type: str = Field(pattern="^(income|expense)$")
    percent: float = Field(default=100.0, ge=0, le=100)
    taxable: bool = True
    budget_monthly: float | None = Field(default=None, ge=0)


class TaxComponent(BaseModel):
    name: str
    rate: float = Field(ge=0)


class TaxProfileIn(BaseModel):
    name: str = Field(min_length=1)
    components: list[TaxComponent]
    activate: bool = False


@router.get("/api/categories")
async def list_categories():
    with get_db() as conn:
        return svc.list_categories(conn)


@router.post("/api/categories")
async def upsert_category(body: CategoryIn):
    with get_db() as conn:
        return svc.upsert_category(conn, body.name, body.type, body.percent,
                                   body.taxable, body.budget_monthly)


@router.delete("/api/categories/{category_id}")
async def delete_category(category_id: int):
    with get_db() as conn:
        svc.delete_category(conn, category_id)
    return {"ok": True}


@router.get("/api/tax-profiles")
async def list_tax_profiles():
    with get_db() as conn:
        return svc.list_tax_profiles(conn)


@router.post("/api/tax-profiles")
async def save_tax_profile(body: TaxProfileIn):
    with get_db() as conn:
        return svc.save_tax_profile(
            conn, body.name, [c.model_dump() for c in body.components], body.activate
        )
```

- [ ] **Step 3: Add API tests** (append to `api/tests/test_api.py`)

```python
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def client(db_path):
    from app.errors import register_error_handler
    from app.routes import categories as categories_routes

    app = FastAPI()
    register_error_handler(app)
    app.include_router(categories_routes.router)
    return TestClient(app, raise_server_exceptions=False)


def test_category_crud_with_taxable_and_budget(client):
    response = client.post("/api/categories", json={
        "name": "Coffee", "type": "expense", "percent": 50,
        "taxable": True, "budget_monthly": 80,
    })
    assert response.status_code == 200
    created = response.json()
    assert created["percent"] == 50 and created["budget_monthly"] == 80

    listing = client.get("/api/categories").json()
    assert any(c["name"] == "Coffee" for c in listing)

    assert client.delete(f"/api/categories/{created['id']}").json() == {"ok": True}


def test_tax_profile_activate(client):
    response = client.post("/api/tax-profiles", json={
        "name": "Ontario", "components": [{"name": "HST", "rate": 13.0}],
        "activate": True,
    })
    assert response.status_code == 200
    profiles = client.get("/api/tax-profiles").json()
    active = [p for p in profiles if p["is_active"]]
    assert len(active) == 1 and active[0]["name"] == "Ontario"
```

- [ ] **Step 4: Run** — `poetry run pytest tests/test_api.py -v` → all PASS
- [ ] **Step 5: Commit**

```bash
git add api/app/services/categories.py api/app/routes/categories.py api/tests/test_api.py
git commit -m "feat(categories): SQLite categories + tax profiles with budgets/taxable"
```

---

### Task 6: Transactions service & routes

**Files:**
- Create: `api/app/services/transactions.py`
- Create: `api/app/routes/transactions.py`
- Test: `api/tests/test_transactions.py`

- [ ] **Step 1: Write failing tests**

`api/tests/test_transactions.py`:

```python
from app.services import transactions as svc


def _create(conn, **overrides):
    data = {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 114.98, "merchant": "Metro", "description": "", "source": "ui",
    }
    data.update(overrides)
    return svc.create_transaction(conn, data)


def test_create_back_calculates_quebec_taxes(conn):
    txn = _create(conn)
    assert txn["amount"] == 100.0
    assert txn["tax_breakdown"] == {"GST": 5.0, "QST": 9.98}
    assert txn["total"] == 114.98
    assert txn["counted"] == 114.98  # percent 100


def test_non_taxable_category_skips_tax(conn):
    txn = _create(conn, category="Rent", total=1500.0)
    assert txn["amount"] == 1500.0 and txn["tax_breakdown"] == {}


def test_counted_uses_category_percent(conn):
    conn.execute("UPDATE categories SET percent=50 WHERE name='Dining'")
    txn = _create(conn, category="Dining", total=100.0)
    assert txn["counted"] == 50.0


def test_list_filters(conn):
    _create(conn)
    _create(conn, type="income", category="Salary", total=5000.0, date="2026-06-01")
    only_income = svc.list_transactions(conn, type_="income")
    assert len(only_income) == 1 and only_income[0]["category"] == "Salary"
    june = svc.list_transactions(conn, start="2026-06-01", end="2026-06-30")
    assert len(june) == 2


def test_update_recomputes(conn):
    txn = _create(conn)
    updated = svc.update_transaction(conn, txn["id"], {"total": 229.96})
    assert updated["amount"] == 200.01 or updated["amount"] == 200.0


def test_bulk_delete(conn):
    ids = [_create(conn)["id"] for _ in range(3)]
    svc.bulk_action(conn, ids[:2], "delete")
    assert len(svc.list_transactions(conn)) == 1


def test_csv_export(conn):
    _create(conn)
    csv_text = svc.export_csv(conn)
    assert "Metro" in csv_text and csv_text.startswith("id,date,type")
```

- [ ] **Step 2: Run** → FAIL

- [ ] **Step 3: Implement `api/app/services/transactions.py`**

```python
"""Transaction CRUD, filters, bulk ops, CSV — all money math lives here."""

from __future__ import annotations

import csv
import io
import json
import sqlite3

from ..errors import AppError
from . import categories as cat_svc
from . import tax as tax_svc

COLUMNS = ["id", "date", "type", "category_id", "description", "merchant",
           "amount", "tax_breakdown", "total", "counted", "image_path",
           "source", "external_ref", "sync_status", "created_at", "updated_at"]


def _row_to_dict(conn, row) -> dict:
    txn = dict(row)
    txn["tax_breakdown"] = json.loads(txn["tax_breakdown"])
    category = conn.execute(
        "SELECT name FROM categories WHERE id=?", (txn["category_id"],)
    ).fetchone()
    txn["category"] = category["name"] if category else "?"
    return txn


def _compute(conn, category: dict, total: float) -> dict:
    components = tax_svc.active_components(conn)
    calc = tax_svc.back_calculate(total, components, bool(category["taxable"]))
    counted = round(total * category["percent"] / 100, 2)
    return {"amount": calc["amount"], "breakdown": calc["breakdown"], "counted": counted}


def create_transaction(conn: sqlite3.Connection, data: dict) -> dict:
    category = cat_svc.find_category_by_name(conn, data["category"])
    if category is None:
        raise AppError("category_not_found", f"Unknown category: {data['category']}", 404)
    if data["type"] not in ("income", "expense"):
        raise AppError("invalid_type", "type must be income or expense")
    total = round(float(data["total"]), 2)
    parts = _compute(conn, category, total)
    cursor = conn.execute(
        """INSERT INTO transactions(date, type, category_id, description, merchant,
           amount, tax_breakdown, total, counted, image_path, source, external_ref,
           sync_status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (data["date"], data["type"], category["id"], data.get("description", ""),
         data.get("merchant", ""), parts["amount"], json.dumps(parts["breakdown"]),
         total, parts["counted"], data.get("image_path"), data.get("source", "ui"),
         data.get("external_ref"), "pending"),
    )
    row = conn.execute("SELECT * FROM transactions WHERE id=?", (cursor.lastrowid,)).fetchone()
    return _row_to_dict(conn, row)


def list_transactions(conn, *, start: str | None = None, end: str | None = None,
                      type_: str | None = None, category: str | None = None,
                      q: str | None = None, limit: int = 500, offset: int = 0) -> list[dict]:
    sql = """SELECT t.* FROM transactions t
             JOIN categories c ON c.id = t.category_id WHERE 1=1"""
    params: list = []
    if start:
        sql += " AND t.date >= ?"; params.append(start)
    if end:
        sql += " AND t.date <= ?"; params.append(end)
    if type_:
        sql += " AND t.type = ?"; params.append(type_)
    if category:
        sql += " AND lower(c.name) = lower(?)"; params.append(category)
    if q:
        sql += " AND (t.merchant LIKE ? OR t.description LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY t.date DESC, t.id DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    return [_row_to_dict(conn, r) for r in conn.execute(sql, params)]


def get_transaction(conn, txn_id: int) -> dict:
    row = conn.execute("SELECT * FROM transactions WHERE id=?", (txn_id,)).fetchone()
    if not row:
        raise AppError("transaction_not_found", "Transaction not found", 404)
    return _row_to_dict(conn, row)


def update_transaction(conn, txn_id: int, changes: dict) -> dict:
    current = get_transaction(conn, txn_id)
    merged = current | changes
    if "category" in changes:
        category = cat_svc.find_category_by_name(conn, changes["category"])
        if category is None:
            raise AppError("category_not_found", "Unknown category", 404)
        merged["category_id"] = category["id"]
    else:
        category = cat_svc.get_category(conn, merged["category_id"])
    parts = _compute(conn, category, round(float(merged["total"]), 2))
    conn.execute(
        """UPDATE transactions SET date=?, type=?, category_id=?, description=?,
           merchant=?, amount=?, tax_breakdown=?, total=?, counted=?,
           sync_status='pending', updated_at=datetime('now') WHERE id=?""",
        (merged["date"], merged["type"], merged["category_id"], merged["description"],
         merged["merchant"], parts["amount"], json.dumps(parts["breakdown"]),
         round(float(merged["total"]), 2), parts["counted"], txn_id),
    )
    return get_transaction(conn, txn_id)


def delete_transaction(conn, txn_id: int) -> None:
    conn.execute("DELETE FROM transactions WHERE id=?", (txn_id,))


def bulk_action(conn, ids: list[int], action: str, category: str | None = None) -> int:
    if action == "delete":
        conn.executemany("DELETE FROM transactions WHERE id=?", [(i,) for i in ids])
        return len(ids)
    if action == "recategorize":
        target = cat_svc.find_category_by_name(conn, category or "")
        if target is None:
            raise AppError("category_not_found", "Unknown category", 404)
        for txn_id in ids:
            update_transaction(conn, txn_id, {"category": target["name"]})
        return len(ids)
    raise AppError("invalid_action", f"Unknown bulk action: {action}")


def export_csv(conn) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id", "date", "type", "category", "description", "merchant",
                     "amount", "taxes", "total", "counted", "source"])
    for txn in list_transactions(conn, limit=100000):
        writer.writerow([txn["id"], txn["date"], txn["type"], txn["category"],
                         txn["description"], txn["merchant"], txn["amount"],
                         json.dumps(txn["tax_breakdown"]), txn["total"],
                         txn["counted"], txn["source"]])
    return buffer.getvalue()
```

- [ ] **Step 4: Run** — `poetry run pytest tests/test_transactions.py -v` → all PASS

- [ ] **Step 5: Create `api/app/routes/transactions.py`**

```python
"""Transactions API: CRUD, bulk, CSV export, receipt images."""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from ..db import get_db
from ..errors import AppError
from ..services import transactions as svc
from ..services.periods import resolve_period

router = APIRouter()


class TransactionIn(BaseModel):
    date: str
    type: str = Field(pattern="^(income|expense)$")
    category: str
    total: float = Field(gt=0)
    merchant: str = ""
    description: str = ""


class TransactionPatch(BaseModel):
    date: str | None = None
    type: str | None = None
    category: str | None = None
    total: float | None = None
    merchant: str | None = None
    description: str | None = None


class BulkIn(BaseModel):
    ids: list[int]
    action: str = Field(pattern="^(delete|recategorize)$")
    category: str | None = None


@router.get("/api/transactions")
async def list_transactions(period: str | None = None,
                            type: str | None = Query(default=None),
                            category: str | None = None, q: str | None = None,
                            limit: int = 100, offset: int = 0):
    start, end = resolve_period(period) if period else (None, None)
    with get_db() as conn:
        return svc.list_transactions(conn, start=start, end=end, type_=type,
                                     category=category, q=q, limit=limit, offset=offset)


@router.post("/api/transactions")
async def create_transaction(body: TransactionIn):
    with get_db() as conn:
        txn = svc.create_transaction(conn, body.model_dump())
    _schedule_sync_push(txn["id"])
    return txn


@router.patch("/api/transactions/{txn_id}")
async def update_transaction(txn_id: int, body: TransactionPatch):
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    with get_db() as conn:
        txn = svc.update_transaction(conn, txn_id, changes)
    _schedule_sync_push(txn_id)
    return txn


@router.delete("/api/transactions/{txn_id}")
async def delete_transaction(txn_id: int):
    with get_db() as conn:
        svc.delete_transaction(conn, txn_id)
    return {"ok": True}


@router.post("/api/transactions/bulk")
async def bulk(body: BulkIn):
    with get_db() as conn:
        count = svc.bulk_action(conn, body.ids, body.action, body.category)
    return {"ok": True, "affected": count}


@router.get("/api/transactions/export.csv")
async def export_csv():
    with get_db() as conn:
        return PlainTextResponse(svc.export_csv(conn), media_type="text/csv")


@router.get("/api/receipts/{txn_id}")
async def receipt_image(txn_id: int):
    with get_db() as conn:
        txn = svc.get_transaction(conn, txn_id)
    if not txn["image_path"]:
        raise AppError("no_receipt", "Transaction has no receipt image", 404)
    return FileResponse(txn["image_path"])


def _schedule_sync_push(txn_id: int) -> None:
    """Fire-and-forget Google push; defined in sync service (Task 12)."""
    try:
        from ..services.sync import schedule_push
        schedule_push(txn_id)
    except ImportError:
        pass  # sync module lands in Task 12
```

- [ ] **Step 6: API smoke test** (append to `api/tests/test_api.py`; extend the `client` fixture's app to also include `transactions_routes.router`)

```python
def test_quick_add_api(client):
    response = client.post("/api/transactions", json={
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 114.98, "merchant": "Metro",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["amount"] == 100.0 and body["tax_breakdown"]["QST"] == 9.98

    export = client.get("/api/transactions/export.csv")
    assert "Metro" in export.text
```

- [ ] **Step 7: Run all** — `poetry run pytest -v` → PASS
- [ ] **Step 8: Commit**

```bash
git add api/app/services/transactions.py api/app/routes/transactions.py api/tests/
git commit -m "feat(transactions): SQLite CRUD, bulk ops, CSV, receipt serving"
```

---

### Task 7: Dashboard aggregates & route

**Files:**
- Modify: `api/app/services/transactions.py` (add `dashboard_data`)
- Rewrite: `api/app/routes/dashboard.py`
- Test: extend `api/tests/test_transactions.py`

- [ ] **Step 1: Write failing test** (append to `test_transactions.py`)

```python
def test_dashboard_data_fresh_db_returns_zeros(conn):
    data = svc.dashboard_data(conn, "2026-06")
    assert data["metrics"] == {"income": 0.0, "expenses": 0.0, "net": 0.0, "count": 0}
    assert data["recent"] == [] and isinstance(data["budgets"], list)


def test_dashboard_data_aggregates(conn):
    conn.execute("UPDATE categories SET budget_monthly=600 WHERE name='Groceries'")
    _create(conn)                                   # expense 114.98 Groceries
    _create(conn, type="income", category="Salary", total=5000.0)
    data = svc.dashboard_data(conn, "2026-06")
    assert data["metrics"]["income"] == 5000.0
    assert data["metrics"]["expenses"] == 114.98
    assert data["metrics"]["net"] == 4885.02
    assert data["by_category"] == {"Groceries": 114.98}
    groceries = [b for b in data["budgets"] if b["name"] == "Groceries"][0]
    assert groceries["budget"] == 600 and groceries["spent"] == 114.98
    assert len(data["trend"]) == 6 and data["trend"][-1]["month"] == "2026-06"
```

- [ ] **Step 2: Run** → FAIL (no `dashboard_data`)

- [ ] **Step 3: Append to `api/app/services/transactions.py`**

```python
def dashboard_data(conn, period: str | None) -> dict:
    from datetime import date
    from .periods import resolve_period, _shift_month, _month_bounds

    start, end = resolve_period(period)
    txns = list_transactions(conn, start=start, end=end, limit=100000)

    income = round(sum(t["counted"] for t in txns if t["type"] == "income"), 2)
    expenses = round(sum(t["counted"] for t in txns if t["type"] == "expense"), 2)

    by_category: dict[str, float] = {}
    for t in txns:
        if t["type"] == "expense":
            by_category[t["category"]] = round(by_category.get(t["category"], 0) + t["counted"], 2)

    # Trend: 6 months ending at the period's end month
    end_year, end_month = int(end[:4]), int(end[5:7])
    trend = []
    for delta in range(-5, 1):
        year, month = _shift_month(end_year, end_month, delta)
        month_start, month_end = _month_bounds(year, month)
        month_txns = list_transactions(conn, start=month_start, end=month_end, limit=100000)
        trend.append({
            "month": f"{year:04d}-{month:02d}",
            "income": round(sum(t["counted"] for t in month_txns if t["type"] == "income"), 2),
            "expenses": round(sum(t["counted"] for t in month_txns if t["type"] == "expense"), 2),
        })

    budgets = []
    for category in conn.execute(
        "SELECT name, budget_monthly FROM categories "
        "WHERE budget_monthly IS NOT NULL AND type='expense' ORDER BY name"
    ):
        spent = by_category.get(category["name"], 0.0)
        budgets.append({"name": category["name"], "budget": category["budget_monthly"],
                        "spent": spent,
                        "pct": round(100 * spent / category["budget_monthly"], 1)
                               if category["budget_monthly"] else 0})

    return {
        "period": {"start": start, "end": end},
        "metrics": {"income": income, "expenses": expenses,
                    "net": round(income - expenses, 2), "count": len(txns)},
        "by_category": by_category,
        "trend": trend,
        "budgets": budgets,
        "recent": txns[:20],
    }
```

- [ ] **Step 4: Rewrite `api/app/routes/dashboard.py`**

```python
"""Dashboard data — always renders; zeros on a fresh DB, never errors."""

from __future__ import annotations

from fastapi import APIRouter

from ..db import get_db
from ..services.transactions import dashboard_data

router = APIRouter()


@router.get("/api/dashboard")
async def dashboard(period: str | None = None):
    with get_db() as conn:
        return dashboard_data(conn, period)
```

- [ ] **Step 5: Run** — all PASS
- [ ] **Step 6: Commit**

```bash
git add api/app/services/transactions.py api/app/routes/dashboard.py api/tests/test_transactions.py
git commit -m "feat(dashboard): SQLite aggregates with budgets and 6-month trend"
```

---

### Task 8: Dedup matcher

**Files:**
- Create: `api/app/services/dedup.py`
- Test: `api/tests/test_dedup.py`

- [ ] **Step 1: Failing tests**

`api/tests/test_dedup.py`:

```python
from app.services import transactions as txn_svc
from app.services.dedup import flag_duplicates


def test_flags_same_amount_within_one_day(conn):
    txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 114.98, "merchant": "Metro",
    })
    rows = [
        {"date": "2026-06-06", "total": 114.98},   # within ±1 day → dup
        {"date": "2026-06-05", "total": 99.99},    # different amount → not
        {"date": "2026-06-09", "total": 114.98},   # too far → not
    ]
    assert flag_duplicates(conn, rows) == [True, False, False]
```

- [ ] **Step 2: Run** → FAIL

- [ ] **Step 3: Implement `api/app/services/dedup.py`**

```python
"""Duplicate detection for imports: same total, date within ±1 day."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta


def flag_duplicates(conn: sqlite3.Connection, rows: list[dict]) -> list[bool]:
    flags = []
    for row in rows:
        day = date.fromisoformat(row["date"])
        low = (day - timedelta(days=1)).isoformat()
        high = (day + timedelta(days=1)).isoformat()
        hit = conn.execute(
            "SELECT 1 FROM transactions WHERE total=? AND date BETWEEN ? AND ? LIMIT 1",
            (round(float(row["total"]), 2), low, high),
        ).fetchone()
        flags.append(hit is not None)
    return flags
```

- [ ] **Step 4: Run** → PASS
- [ ] **Step 5: Commit**

```bash
git add api/app/services/dedup.py api/tests/test_dedup.py
git commit -m "feat(dedup): import duplicate matcher (total + date ±1d)"
```

---

### Task 9: Recurring rules

**Files:**
- Create: `api/app/services/recurring.py`
- Create: `api/app/routes/recurring.py`
- Test: `api/tests/test_recurring.py`

- [ ] **Step 1: Failing tests**

`api/tests/test_recurring.py`:

```python
from datetime import date

from app.services import recurring as svc
from app.services import transactions as txn_svc


def test_next_run_monthly_clamps_short_months():
    assert svc.next_run_after(date(2026, 1, 31), "monthly") == date(2026, 2, 28)
    assert svc.next_run_after(date(2026, 6, 15), "monthly") == date(2026, 7, 15)
    assert svc.next_run_after(date(2026, 6, 1), "weekly") == date(2026, 6, 8)
    assert svc.next_run_after(date(2026, 6, 1), "biweekly") == date(2026, 6, 15)


def test_run_due_rules_records_and_advances(conn):
    rule = svc.create_rule(conn, template={
        "type": "expense", "category": "Rent", "total": 1500.0,
        "merchant": "Landlord", "description": "Monthly rent",
    }, frequency="monthly", next_run="2026-06-01")

    created = svc.run_due_rules(conn, today=date(2026, 6, 5))
    assert created == 1
    txns = txn_svc.list_transactions(conn)
    assert txns[0]["category"] == "Rent" and txns[0]["source"] == "recurring"

    rules = svc.list_rules(conn)
    assert rules[0]["next_run"] == "2026-07-01"

    # Running again same day: nothing due
    assert svc.run_due_rules(conn, today=date(2026, 6, 5)) == 0
```

- [ ] **Step 2: Run** → FAIL

- [ ] **Step 3: Implement `api/app/services/recurring.py`**

```python
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
```

- [ ] **Step 4: Run** → PASS

- [ ] **Step 5: Create `api/app/routes/recurring.py`**

```python
"""Recurring rule configuration."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..db import get_db
from ..services import recurring as svc

router = APIRouter()


class RuleIn(BaseModel):
    template: dict
    frequency: str = Field(pattern="^(weekly|biweekly|monthly)$")
    next_run: str


class RulePatch(BaseModel):
    template: dict | None = None
    frequency: str | None = None
    next_run: str | None = None
    active: bool | None = None


@router.get("/api/recurring")
async def list_rules():
    with get_db() as conn:
        return svc.list_rules(conn)


@router.post("/api/recurring")
async def create_rule(body: RuleIn):
    with get_db() as conn:
        return svc.create_rule(conn, body.template, body.frequency, body.next_run)


@router.patch("/api/recurring/{rule_id}")
async def update_rule(rule_id: int, body: RulePatch):
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    with get_db() as conn:
        return svc.update_rule(conn, rule_id, changes)


@router.delete("/api/recurring/{rule_id}")
async def delete_rule(rule_id: int):
    with get_db() as conn:
        svc.delete_rule(conn, rule_id)
    return {"ok": True}
```

- [ ] **Step 6: Commit**

```bash
git add api/app/services/recurring.py api/app/routes/recurring.py api/tests/test_recurring.py
git commit -m "feat(recurring): rules with catch-up scheduler logic"
```

---

### Task 10: Chat persistence + agent runtime rewire

**Files:**
- Create: `api/app/services/chat_store.py`
- Modify: `api/app/agent/runtime.py`
- Rewrite: `api/app/routes/chat.py`
- Test: `api/tests/test_chat_store.py`

- [ ] **Step 1: Failing tests**

`api/tests/test_chat_store.py`:

```python
from app.services import chat_store


def test_session_lifecycle(conn):
    session = chat_store.create_session(conn, channel="ui")
    assert session["title"] == "New chat"

    chat_store.add_message(conn, session["id"], "user", {"text": "hello"})
    chat_store.add_message(conn, session["id"], "assistant",
                           {"text": "hi", "ui_specs": []})
    messages = chat_store.list_messages(conn, session["id"])
    assert [m["role"] for m in messages] == ["user", "assistant"]

    # First user message becomes the title
    sessions = chat_store.list_sessions(conn)
    assert sessions[0]["title"] == "hello"

    chat_store.delete_session(conn, session["id"])
    assert chat_store.list_sessions(conn) == []
```

- [ ] **Step 2: Run** → FAIL

- [ ] **Step 3: Implement `api/app/services/chat_store.py`**

```python
"""Persistent chat sessions and messages (survive restarts)."""

from __future__ import annotations

import json
import sqlite3
import uuid

from ..errors import AppError


def create_session(conn, channel: str = "ui") -> dict:
    session_id = f"{channel}:{uuid.uuid4().hex[:12]}"
    conn.execute("INSERT INTO chat_sessions(id, channel) VALUES (?,?)",
                 (session_id, channel))
    return get_session(conn, session_id)


def get_session(conn, session_id: str) -> dict:
    row = conn.execute("SELECT * FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        raise AppError("session_not_found", "Chat session not found", 404)
    return dict(row)


def ensure_session(conn, session_id: str, channel: str) -> dict:
    row = conn.execute("SELECT * FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
    if row:
        return dict(row)
    conn.execute("INSERT INTO chat_sessions(id, channel) VALUES (?,?)",
                 (session_id, channel))
    return get_session(conn, session_id)


def list_sessions(conn, channel: str = "ui") -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM chat_sessions WHERE channel=? ORDER BY updated_at DESC",
        (channel,))]


def delete_session(conn, session_id: str) -> None:
    conn.execute("DELETE FROM chat_sessions WHERE id=?", (session_id,))


def add_message(conn, session_id: str, role: str, content: dict) -> None:
    conn.execute(
        "INSERT INTO chat_messages(session_id, role, content) VALUES (?,?,?)",
        (session_id, role, json.dumps(content)),
    )
    if role == "user":
        first = conn.execute(
            "SELECT COUNT(*) c FROM chat_messages WHERE session_id=? AND role='user'",
            (session_id,)).fetchone()["c"]
        if first == 1:
            title = (content.get("text") or "Receipt")[:60]
            conn.execute("UPDATE chat_sessions SET title=? WHERE id=?",
                         (title, session_id))
    conn.execute("UPDATE chat_sessions SET updated_at=datetime('now') WHERE id=?",
                 (session_id,))


def list_messages(conn, session_id: str) -> list[dict]:
    return [dict(r) | {"content": json.loads(r["content"])} for r in conn.execute(
        "SELECT * FROM chat_messages WHERE session_id=? ORDER BY id", (session_id,))]
```

- [ ] **Step 4: Run** → PASS

- [ ] **Step 5: Modify `api/app/agent/runtime.py`**

Changes (keep provider registration block and `_claude_model()` untouched):

1. `Session.__init__` gains history replay — after `set_tools`, load persisted turns:

```python
from pi_agent.agent_core import UserMessage  # add to existing import list

# inside Session.__init__, after self.agent.set_tools(...):
        from ..db import get_db
        from ..services import chat_store
        with get_db() as conn:
            chat_store.ensure_session(conn, session_id, channel)
            for message in chat_store.list_messages(conn, session_id):
                text = message["content"].get("text", "")
                if not text:
                    continue
                if message["role"] == "user":
                    self.agent.append_message(UserMessage(content=text))
                else:
                    self.agent.append_message(_replayed_assistant(text))
```

2. Module-level helper (place near `_claude_model`):

```python
def _replayed_assistant(text: str) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)],
        api="anthropic", provider="anthropic", model=config.claude_model,
    )
```

3. `Session.run` persists both sides — first line inside `async with self.lock:` add:

```python
            from ..db import get_db
            from ..services import chat_store
            with get_db() as conn:
                chat_store.add_message(conn, self.id, "user", {"text": text})
```

and just before the final `yield {"type": "done", ...}`:

```python
            with get_db() as conn:
                chat_store.add_message(conn, self.id, "assistant", {
                    "text": "\n".join(final_text_parts).strip(),
                    "ui_specs": self._ui_specs[:],
                })
```

- [ ] **Step 6: Rewrite `api/app/routes/chat.py`**

```python
"""Chat: session management + SSE message streaming."""

from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import StreamingResponse

from ..agent.runtime import sessions
from ..db import get_db
from ..services import chat_store
from ..services.receipts import build_receipt_prompt

router = APIRouter()


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@router.get("/api/chat/sessions")
async def list_sessions():
    with get_db() as conn:
        return chat_store.list_sessions(conn)


@router.post("/api/chat/sessions")
async def create_session():
    with get_db() as conn:
        return chat_store.create_session(conn)


@router.get("/api/chat/sessions/{session_id}")
async def session_history(session_id: str):
    with get_db() as conn:
        return {"session": chat_store.get_session(conn, session_id),
                "messages": chat_store.list_messages(conn, session_id)}


@router.delete("/api/chat/sessions/{session_id}")
async def delete_session(session_id: str):
    with get_db() as conn:
        chat_store.delete_session(conn, session_id)
    sessions.reset(session_id)
    return {"ok": True}


@router.post("/api/chat/sessions/{session_id}/messages")
async def send_message(session_id: str, message: str = Form(""),
                       image: UploadFile | None = File(None)):
    session = sessions.get(session_id, channel="ui")
    image_bytes = await image.read() if image is not None else None
    image_mime = (image.content_type or "image/jpeg") if image is not None else None

    async def stream():
        try:
            prompt = message
            if image_bytes:
                yield _sse({"type": "status", "text": "Reading receipt…"})
                prompt = await build_receipt_prompt(message, image_bytes, image_mime)
            if not prompt.strip():
                yield _sse({"type": "done", "text": "Send a message or receipt.", "error": None})
                return
            async for event in session.run(prompt):
                yield _sse(event)
        except Exception as exc:  # noqa: BLE001 — degrade, never 500 mid-stream
            yield _sse({"type": "done",
                        "text": "Sorry, something went wrong on my side. Try again.",
                        "error": str(exc)})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
```

- [ ] **Step 7: Run full suite + import check**

```bash
cd api && poetry run pytest -v && poetry run python -c "from app.routes import chat; print('OK')"
```

- [ ] **Step 8: Commit**

```bash
git add api/app/services/chat_store.py api/app/agent/runtime.py api/app/routes/chat.py api/tests/test_chat_store.py
git commit -m "feat(chat): persistent sessions with history replay"
```

---

### Task 11: Agent tools rewrite (SQLite + budgets + recurring)

**Files:**
- Rewrite: `api/app/agent/tools.py`
- Modify: `api/app/agent/prompts.py`
- Modify: `api/app/services/receipts.py`

- [ ] **Step 1: Rewrite `api/app/agent/tools.py`** — same factory shape (`build_tools(channel, ui_sink, source)`), same `_text_result` helper and `RENDER_UI_SCHEMA` (copy verbatim from current file). Replace the sheets-backed executors with SQLite and add two tools. Full executor section:

```python
from ..db import get_db
from ..services import categories as cat_svc
from ..services import recurring as rec_svc
from ..services import transactions as txn_svc
from ..services.periods import resolve_period


def build_tools(channel: str, ui_sink: UiSink, source: str) -> list[AgentTool]:
    async def record_transaction(tool_call_id, params, abort_event=None, on_update=None):
        def work():
            with get_db() as conn:
                return txn_svc.create_transaction(conn, {
                    "date": params["date"], "type": params["type"],
                    "category": params["category"],
                    "description": params.get("description", ""),
                    "merchant": params.get("merchant", ""),
                    "total": float(params["total"]),
                    "image_path": params.get("image_path"),
                    "source": params.get("source", source),
                })
        return _text_result(await asyncio.to_thread(work))

    async def query_transactions(tool_call_id, params, abort_event=None, on_update=None):
        def work():
            with get_db() as conn:
                return txn_svc.list_transactions(
                    conn, start=params.get("start_date"), end=params.get("end_date"),
                    type_=params.get("type"), category=params.get("category"))
        rows = await asyncio.to_thread(work)
        return _text_result({"count": len(rows), "transactions": rows[:200]})

    async def get_summary(tool_call_id, params, abort_event=None, on_update=None):
        def work():
            with get_db() as conn:
                period = params.get("period")
                if params.get("start_date") and params.get("end_date"):
                    period = f"{params['start_date']}:{params['end_date']}"
                return txn_svc.dashboard_data(conn, period)
        return _text_result(await asyncio.to_thread(work))

    async def manage_categories(tool_call_id, params, abort_event=None, on_update=None):
        def work():
            with get_db() as conn:
                action = params["action"]
                if action == "list":
                    return cat_svc.list_categories(conn)
                if action == "upsert":
                    return cat_svc.upsert_category(
                        conn, params["name"], params.get("type", "expense"),
                        float(params.get("percent", 100)),
                        bool(params.get("taxable", True)),
                        params.get("budget_monthly"))
                category = cat_svc.find_category_by_name(conn, params["name"])
                if category:
                    cat_svc.delete_category(conn, category["id"])
                return {"deleted": bool(category)}
        return _text_result(await asyncio.to_thread(work))

    async def manage_budgets(tool_call_id, params, abort_event=None, on_update=None):
        def work():
            with get_db() as conn:
                category = cat_svc.find_category_by_name(conn, params["category"])
                if category is None:
                    return {"error": f"Unknown category {params['category']}"}
                return cat_svc.upsert_category(
                    conn, category["name"], category["type"], category["percent"],
                    category["taxable"], params.get("budget_monthly"))
        return _text_result(await asyncio.to_thread(work))

    async def manage_recurring(tool_call_id, params, abort_event=None, on_update=None):
        def work():
            with get_db() as conn:
                action = params["action"]
                if action == "list":
                    return rec_svc.list_rules(conn)
                if action == "create":
                    return rec_svc.create_rule(conn, params["template"],
                                               params["frequency"], params["next_run"])
                rec_svc.delete_rule(conn, int(params["rule_id"]))
                return {"deleted": True}
        return _text_result(await asyncio.to_thread(work))
```

Schemas: keep `RECORD_TRANSACTION_SCHEMA` but replace `amount/gst/qst` props with required `total` (`"required": ["date", "type", "category", "total"]`, add optional `image_path`); `GET_SUMMARY_SCHEMA` gains `"period": {"type": "string"}`; add:

```python
MANAGE_BUDGETS_SCHEMA = {
    "type": "object",
    "properties": {"category": {"type": "string"},
                   "budget_monthly": {"type": ["number", "null"]}},
    "required": ["category"],
}
MANAGE_RECURRING_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["list", "create", "delete"]},
        "template": {"type": "object"}, "frequency": {"type": "string"},
        "next_run": {"type": "string"}, "rule_id": {"type": "number"},
    },
    "required": ["action"],
}
```

Register the two new `AgentTool` entries in the returned list (labels "Manage budgets", "Manage recurring"). Wrap every executor body in `try/except Exception as exc: return _text_result({"error": f"That didn't work: {exc}"})` — friendly degradation per spec §9.

- [ ] **Step 2: Modify `api/app/services/receipts.py`** — save image locally instead of Drive (Drive moves to sync):

Replace the upload_task block with:

```python
    receipts_dir = config.data_dir / "receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)
    image_path = receipts_dir / filename
    image_path.write_bytes(image_bytes)
```

and change the prompt assembly to pass `image_path` (string path) instead of Drive link:

```python
    parts = [
        "The user submitted a receipt image.",
        f"Saved receipt image path: {image_path}",
        ...
        "Extract the transaction details (date, merchant, total incl. taxes), "
        "choose a category, and call record_transaction with image_path set.",
    ]
```

Remove the `google_client` import.

- [ ] **Step 3: Modify `api/app/agent/prompts.py`** — update the recording instructions: user supplies total; taxes auto-derived server-side from category + active profile (the agent should NOT compute taxes); mention `manage_budgets` / `manage_recurring`; keep channel split. Replace the "## Recording transactions" + "## Category formulas" sections with:

```python
## Recording transactions
When the user provides a receipt (OCR text + saved image path) or describes a
purchase/income: extract date (default today), merchant, description, and the
TOTAL PAID. Pick the best category. Call record_transaction with the total —
GST/QST/HST are computed automatically from the category's taxable flag and
the active tax profile. Never compute taxes yourself.

## Budgets, categories, recurring
Categories have a percent counting formula and a taxable flag.
manage_budgets sets a monthly budget per category. manage_recurring creates
rules (rent, salary) that auto-record on schedule.
```

- [ ] **Step 4: Verify** — `poetry run pytest -v` and:

```bash
poetry run python -c "
from app.agent.runtime import sessions
s = sessions.get('t:1','ui')
print([t.name for t in s.agent.state.tools])"
```
Expected list includes `manage_budgets`, `manage_recurring`, `render_ui`.

- [ ] **Step 5: Commit**

```bash
git add api/app/agent/ api/app/services/receipts.py
git commit -m "feat(agent): SQLite-backed tools, budgets + recurring, local receipts"
```

---

### Task 12: Google one-way sync

**Files:**
- Create: `api/app/services/sync.py`
- Create: `api/app/routes/sync.py`
- Modify: `api/app/services/google_client.py`, `api/app/routes/google_auth.py`
- Test: `api/tests/test_sync.py`

- [ ] **Step 1: Modify `api/app/services/google_client.py`** — keep OAuth helpers (`build_auth_url`, `exchange_code`, `get_credentials`, `is_connected`, `drive_service`, `sheets_service`, `upload_receipt_image`, `ensure_drive_folder`) but store tokens in the settings table instead of `store.py`:

Replace `from ..store import read_settings, write_settings` with:

```python
from ..db import get_db, get_setting, set_setting


def _read(key):
    with get_db() as conn:
        return get_setting(conn, key)


def _write(key, value):
    with get_db() as conn:
        set_setting(conn, key, value)
```

and swap usages: `read_settings().get("google_tokens")` → `_read("google_tokens")`; `write_settings(google_tokens=…)` → `_write("google_tokens", …)`; same for `drive_folder_id` / `spreadsheet_id`. Keep `ensure_spreadsheet` but its header row becomes the sync format (Step 2's `SHEET_HEADERS` imported from sync — to avoid a cycle, move spreadsheet bootstrap INTO `sync.py` and delete `ensure_spreadsheet`/`_ensure_headers` from google_client).

- [ ] **Step 2: Implement `api/app/services/sync.py`**

```python
"""One-way sync: app SQLite → Google Sheet + Drive. Never reads data back.

Sheet gets an ID column (app transaction id) making reconcile idempotent.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from pathlib import Path

from ..db import get_db, get_setting, set_setting
from . import google_client as gc

logger = logging.getLogger(__name__)

SHEET_NAME = "Transactions"
SHEET_HEADERS = ["ID", "Date", "Type", "Category", "Description", "Merchant",
                 "Amount", "Taxes", "Total", "Counted", "Image Link", "Source"]


def sync_enabled() -> bool:
    return gc.is_connected()


def _ensure_spreadsheet(conn: sqlite3.Connection) -> str:
    spreadsheet_id = get_setting(conn, "spreadsheet_id")
    sheets = gc.sheets_service()
    if not spreadsheet_id:
        created = sheets.spreadsheets().create(
            body={"properties": {"title": "Expense Manager"},
                  "sheets": [{"properties": {"title": SHEET_NAME}}]},
            fields="spreadsheetId").execute()
        spreadsheet_id = created["spreadsheetId"]
        set_setting(conn, "spreadsheet_id", spreadsheet_id)
        sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=f"{SHEET_NAME}!A1",
            valueInputOption="RAW", body={"values": [SHEET_HEADERS]}).execute()
    return spreadsheet_id


def _txn_row(txn: dict, image_link: str) -> list:
    return [txn["id"], txn["date"], txn["type"], txn["category"],
            txn["description"], txn["merchant"], txn["amount"],
            json.dumps(txn["tax_breakdown"]), txn["total"], txn["counted"],
            image_link, txn["source"]]


def _sheet_ids(sheets, spreadsheet_id: str) -> dict[int, int]:
    """Map app txn id → sheet row number (2-based)."""
    values = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"{SHEET_NAME}!A2:A").execute().get("values", [])
    mapping = {}
    for index, row in enumerate(values):
        if row and str(row[0]).isdigit():
            mapping[int(row[0])] = index + 2
    return mapping


def reconcile() -> dict:
    """Push all pending/missing transactions. Safe to run repeatedly."""
    if not sync_enabled():
        return {"synced": 0, "skipped": "google_not_connected"}
    from .transactions import list_transactions

    with get_db() as conn:
        spreadsheet_id = _ensure_spreadsheet(conn)
        sheets = gc.sheets_service()
        existing = _sheet_ids(sheets, spreadsheet_id)
        txns = list_transactions(conn, limit=100000)
        pushed = 0
        for txn in txns:
            needs = txn["sync_status"] == "pending" or txn["id"] not in existing
            if not needs:
                continue
            image_link = _maybe_upload_receipt(conn, txn)
            row = _txn_row(txn, image_link)
            if txn["id"] in existing:
                sheets.spreadsheets().values().update(
                    spreadsheetId=spreadsheet_id,
                    range=f"{SHEET_NAME}!A{existing[txn['id']]}",
                    valueInputOption="USER_ENTERED", body={"values": [row]}).execute()
            else:
                sheets.spreadsheets().values().append(
                    spreadsheetId=spreadsheet_id, range=f"{SHEET_NAME}!A:L",
                    valueInputOption="USER_ENTERED", body={"values": [row]}).execute()
            conn.execute("UPDATE transactions SET sync_status='synced' WHERE id=?",
                         (txn["id"],))
            pushed += 1
        # Deletions: ids in sheet but not in app
        app_ids = {t["id"] for t in txns}
        for missing_id, row_number in sorted(existing.items(), reverse=True):
            if missing_id not in app_ids:
                sheets.spreadsheets().values().update(
                    spreadsheetId=spreadsheet_id,
                    range=f"{SHEET_NAME}!A{row_number}:L{row_number}",
                    valueInputOption="RAW",
                    body={"values": [["(deleted)"] + [""] * 11]}).execute()
        return {"synced": pushed}


def _maybe_upload_receipt(conn, txn: dict) -> str:
    link = get_setting(conn, f"receipt_link_{txn['id']}")
    if link:
        return link
    if not txn["image_path"] or not Path(txn["image_path"]).exists():
        return ""
    data = Path(txn["image_path"]).read_bytes()
    suffix = Path(txn["image_path"]).suffix.lstrip(".") or "jpg"
    link = gc.upload_receipt_image(Path(txn["image_path"]).name, data, f"image/{suffix}")
    set_setting(conn, f"receipt_link_{txn['id']}", link)
    return link


def schedule_push(txn_id: int) -> None:
    """Background push of one transaction (called after writes)."""
    if not sync_enabled():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(asyncio.to_thread(_safe_reconcile))


def _safe_reconcile() -> None:
    try:
        reconcile()
    except Exception:  # noqa: BLE001
        logger.exception("Sync push failed; will retry on next reconcile")


def status() -> dict:
    with get_db() as conn:
        pending = conn.execute(
            "SELECT COUNT(*) c FROM transactions WHERE sync_status='pending'"
        ).fetchone()["c"]
        spreadsheet_id = get_setting(conn, "spreadsheet_id")
    return {"enabled": sync_enabled(), "pending": pending,
            "sheet_url": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
                         if spreadsheet_id else None}
```

- [ ] **Step 3: Create `api/app/routes/sync.py`**

```python
from __future__ import annotations

import asyncio

from fastapi import APIRouter

from ..services import sync as svc

router = APIRouter()


@router.get("/api/sync/status")
async def sync_status():
    return svc.status()


@router.post("/api/sync/now")
async def sync_now():
    return await asyncio.to_thread(svc.reconcile)
```

- [ ] **Step 4: Modify `api/app/routes/google_auth.py`** — in `callback`, replace `ensure_spreadsheet`/`ensure_drive_folder` calls with a first reconcile:

```python
    await asyncio.to_thread(gc.exchange_code, code)
    from ..services import sync
    await asyncio.to_thread(sync._safe_reconcile)
    return RedirectResponse(f"{config.web_origin}/settings?google=connected")
```

In `status`, replace the `ensure_spreadsheet` readiness probe with `from ..services.sync import status as sync_status_fn` and return `{"configured": ..., "connected": gc.is_connected(), **sync_status_fn()}`.

- [ ] **Step 5: Test reconcile idempotency with a fake sheets client**

`api/tests/test_sync.py`:

```python
from unittest.mock import MagicMock, patch

from app.services import sync, transactions as txn_svc


def _fake_sheets(store):
    """Minimal in-memory fake for spreadsheets().values() get/update/append."""
    sheets = MagicMock()
    values = sheets.spreadsheets.return_value.values.return_value
    values.get.return_value.execute.side_effect = lambda: {
        "values": [[str(i)] for i in store]}
    def _append(**kwargs):
        call = MagicMock()
        call.execute.side_effect = lambda: store.append(
            kwargs["body"]["values"][0][0]) or {}
        return call
    values.append.side_effect = _append
    values.update.return_value.execute.return_value = {}
    sheets.spreadsheets.return_value.create.return_value.execute.return_value = {
        "spreadsheetId": "fake123"}
    return sheets


def test_reconcile_pushes_once(conn, db_path):
    txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 50.0})
    conn.commit()
    store: list = []
    with patch.object(sync.gc, "is_connected", return_value=True), \
         patch.object(sync.gc, "sheets_service", return_value=_fake_sheets(store)):
        first = sync.reconcile()
        second = sync.reconcile()
    assert first["synced"] == 1
    assert second["synced"] == 0          # idempotent
```

- [ ] **Step 6: Run** — `poetry run pytest tests/test_sync.py -v` → PASS
- [ ] **Step 7: Commit**

```bash
git add api/app/services/sync.py api/app/services/google_client.py api/app/routes/sync.py api/app/routes/google_auth.py api/tests/test_sync.py
git commit -m "feat(sync): one-way Google sheet/Drive sync with idempotent reconcile"
```

---

### Task 13: Imports (upload → agent parse → review → approve)

**Files:**
- Create: `api/app/services/imports.py`
- Create: `api/app/routes/imports.py`

- [ ] **Step 1: Implement `api/app/services/imports.py`**

```python
"""Statement/sheet imports: extract text, agent structures rows, dedup, approve."""

from __future__ import annotations

import csv
import io
import json
import logging
import re

from ..db import get_db
from ..errors import AppError
from . import dedup as dedup_svc
from . import transactions as txn_svc

logger = logging.getLogger(__name__)


def extract_text(filename: str, data: bytes) -> str:
    lower = filename.lower()
    if lower.endswith(".csv"):
        return data.decode("utf-8", errors="replace")
    if lower.endswith((".xlsx", ".xls")):
        import openpyxl
        workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows(values_only=True):
                writer.writerow(["" if v is None else v for v in row])
        return buffer.getvalue()
    if lower.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        if len(text.strip()) < 40:
            raise AppError("pdf_unreadable",
                           "Couldn't extract text from this PDF (scanned image?). "
                           "Try a CSV export instead.", 422)
        return text
    raise AppError("unsupported_format", "Upload CSV, XLSX or PDF", 415)


PARSE_PROMPT = """Below is the text of a bank statement or expense sheet.
Extract every transaction as a JSON array. Each item:
{{"date": "YYYY-MM-DD", "type": "income"|"expense", "category": "<best guess from: {categories}>",
  "merchant": "...", "description": "...", "total": <number, positive>}}
Rules: deposits/credits are income; withdrawals/debits are expense.
Respond with ONLY the JSON array, no prose.

TEXT:
{text}"""


async def parse_with_agent(text: str) -> list[dict]:
    from pi_agent.agent_core import LlmContext, UserMessage
    from pi_agent.pi_ai import complete

    from ..agent.runtime import _claude_model, _registry

    with get_db() as conn:
        category_names = [c["name"] for c in
                          conn.execute("SELECT name FROM categories")]
    prompt = PARSE_PROMPT.format(categories=", ".join(category_names),
                                 text=text[:60000])
    message = await complete(
        model=_claude_model(),
        context=LlmContext(messages=[UserMessage(content=prompt)]),
        registry=_registry)
    raw = "".join(getattr(block, "text", "") for block in message.content)
    match = re.search(r"\[.*\]", raw, re.S)
    if not match:
        raise AppError("parse_failed", "Couldn't structure the file contents", 422)
    return json.loads(match.group(0))


async def start_import(filename: str, data: bytes) -> dict:
    text = extract_text(filename, data)
    with get_db() as conn:
        cursor = conn.execute("INSERT INTO imports(filename) VALUES (?)", (filename,))
        import_id = cursor.lastrowid
    try:
        rows = await parse_with_agent(text)
        with get_db() as conn:
            flags = dedup_svc.flag_duplicates(conn, rows)
            for row, flag in zip(rows, flags):
                row["duplicate"] = flag
                row["skip"] = flag
            conn.execute("UPDATE imports SET status='review', rows=? WHERE id=?",
                         (json.dumps(rows), import_id))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Import parse failed")
        with get_db() as conn:
            conn.execute("UPDATE imports SET status='failed', error=? WHERE id=?",
                         (str(exc), import_id))
    return get_import(import_id)


def get_import(import_id: int) -> dict:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM imports WHERE id=?", (import_id,)).fetchone()
    if not row:
        raise AppError("import_not_found", "Import not found", 404)
    record = dict(row)
    record["rows"] = json.loads(record["rows"])
    return record


def approve_import(import_id: int, indexes: list[int] | None) -> dict:
    record = get_import(import_id)
    if record["status"] != "review":
        raise AppError("not_reviewable", f"Import status is {record['status']}", 409)
    created = 0
    with get_db() as conn:
        for index, row in enumerate(record["rows"]):
            wanted = indexes is None or index in indexes
            if not wanted or row.get("skip"):
                continue
            txn_svc.create_transaction(conn, row | {
                "source": "import",
                "external_ref": f"import:{import_id}:{index}"})
            created += 1
        conn.execute("UPDATE imports SET status='approved' WHERE id=?", (import_id,))
    return {"created": created}
```

- [ ] **Step 2: Create `api/app/routes/imports.py`**

```python
from __future__ import annotations

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel

from ..services import imports as svc

router = APIRouter()


class ApproveIn(BaseModel):
    indexes: list[int] | None = None   # None = all non-skipped


@router.post("/api/imports")
async def upload(file: UploadFile = File(...)):
    data = await file.read()
    return await svc.start_import(file.filename or "upload", data)


@router.get("/api/imports/{import_id}")
async def get_import(import_id: int):
    return svc.get_import(import_id)


@router.post("/api/imports/{import_id}/approve")
async def approve(import_id: int, body: ApproveIn):
    return svc.approve_import(import_id, body.indexes)
```

- [ ] **Step 3: Verify imports compile + extract_text works**

```bash
cd api && poetry run python -c "
from app.services.imports import extract_text
csv_text = extract_text('test.csv', b'date,desc,amount\n2026-06-01,METRO,42.50')
assert 'METRO' in csv_text
print('EXTRACT_OK')"
```

- [ ] **Step 4: Commit**

```bash
git add api/app/services/imports.py api/app/routes/imports.py
git commit -m "feat(imports): CSV/XLSX/PDF upload, agent parsing, dedup review, approve"
```

---

### Task 14: WhatsApp replies + weekly summary + schedulers

**Files:**
- Create: `api/app/services/summary_text.py`
- Modify: `api/app/channels/whatsapp.py` (handler-adjacent only), `api/app/agent/prompts.py` (WhatsApp section)

- [ ] **Step 1: Implement `api/app/services/summary_text.py`**

```python
"""Plain-text summaries for WhatsApp."""

from __future__ import annotations

from ..db import get_db
from .transactions import dashboard_data


def weekly_summary_text() -> str:
    with get_db() as conn:
        data = dashboard_data(conn, None)  # current month
    metrics = data["metrics"]
    lines = [
        "📊 Weekly summary (this month so far)",
        f"Income: ${metrics['income']:.2f}",
        f"Expenses: ${metrics['expenses']:.2f}",
        f"Net: ${metrics['net']:.2f}",
    ]
    top = sorted(data["by_category"].items(), key=lambda kv: -kv[1])[:3]
    if top:
        lines.append("Top spending:")
        lines += [f"  • {name}: ${value:.2f}" for name, value in top]
    return "\n".join(lines)
```

- [ ] **Step 2: WhatsApp prompt detail** — in `api/app/agent/prompts.py` WhatsApp section, append:

```
After record_transaction succeeds, reply with the full breakdown:
date, merchant, category, amount, each tax component, total, counted amount
(if percent != 100), one line each.
```

- [ ] **Step 3: Weekly scheduler + chat memory of summary target** — in `api/app/channels/whatsapp.py`, inside `_handle_message` after `chat_id` is computed, persist the chat as summary target:

```python
        from ..db import get_db, set_setting
        with get_db() as conn:
            set_setting(conn, "whatsapp_summary_chat", chat_id)
```

Add a method to `WhatsAppManager`:

```python
    async def send_weekly_summary(self) -> None:
        from ..db import get_db, get_setting
        from ..services.summary_text import weekly_summary_text
        with get_db() as conn:
            chat_id = get_setting(conn, "whatsapp_summary_chat")
        if chat_id and self.status == "connected" and chat_id in self._reply_jids:
            await self.send(chat_id, weekly_summary_text())
```

- [ ] **Step 4: Commit**

```bash
git add api/app/services/summary_text.py api/app/channels/whatsapp.py api/app/agent/prompts.py
git commit -m "feat(whatsapp): detailed confirmations and weekly summary"
```

---

### Task 15: main.py rewire, schedulers, legacy cleanup

**Files:**
- Rewrite: `api/app/main.py`
- Delete: `api/app/services/sheets.py`, `api/app/store.py`

- [ ] **Step 1: Rewrite `api/app/main.py`**

```python
"""FastAPI application — local-first backend for UI and WhatsApp channels."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date, datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .agent.runtime import run_to_completion, sessions
from .channels.whatsapp import whatsapp
from .config import config
from .db import init_db
from .errors import register_error_handler
from .routes import (categories, chat, dashboard, google_auth, imports,
                     recurring, sync, transactions)
from .routes import whatsapp as whatsapp_routes
from .services.receipts import build_receipt_prompt

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _handle_whatsapp_message(chat_id, text, image_bytes, image_mime):
    session = sessions.get(f"wa:{chat_id}", channel="whatsapp")
    try:
        prompt = text
        if image_bytes:
            prompt = await build_receipt_prompt(text, image_bytes,
                                                image_mime or "image/jpeg")
        if not prompt.strip():
            return 'Send a receipt photo or e.g. "spent $20 on groceries".'
        return await run_to_completion(session, prompt)
    except Exception as exc:  # noqa: BLE001
        logger.exception("WhatsApp pipeline failed")
        return f"Sorry, something went wrong: {exc}"


async def _scheduler_loop():
    """Hourly reconcile, daily recurring run, Sunday 18:00 weekly summary."""
    from .db import get_db
    from .services.recurring import run_due_rules
    from .services.sync import _safe_reconcile, sync_enabled

    last_summary_day: date | None = None
    while True:
        try:
            with get_db() as conn:
                run_due_rules(conn)
            if sync_enabled():
                await asyncio.to_thread(_safe_reconcile)
            now = datetime.now()
            if (now.weekday() == 6 and now.hour >= 18
                    and last_summary_day != now.date()):
                await whatsapp.send_weekly_summary()
                last_summary_day = now.date()
        except Exception:  # noqa: BLE001
            logger.exception("Scheduler tick failed")
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    whatsapp.set_handler(_handle_whatsapp_message)
    try:
        await whatsapp.start()
    except Exception:  # noqa: BLE001
        logger.exception("WhatsApp channel failed to start")
    scheduler = asyncio.create_task(_scheduler_loop())
    yield
    scheduler.cancel()


app = FastAPI(title="Expense Manager API", lifespan=lifespan)
register_error_handler(app)

app.add_middleware(CORSMiddleware, allow_origins=[config.web_origin],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

for module in (chat, dashboard, transactions, categories, recurring,
               imports, sync, whatsapp_routes, google_auth):
    app.include_router(module.router)


@app.get("/api/health")
async def health():
    return {"ok": True}
```

- [ ] **Step 2: Delete legacy modules and fix stragglers**

```bash
git rm api/app/services/sheets.py api/app/store.py
grep -rn "from ..store\|from .store\|services.sheets\|services import sheets" api/app/ || echo CLEAN
```
Fix any remaining importers (expected: none after Tasks 11-12).

- [ ] **Step 3: Full backend verification**

```bash
cd api && poetry run pytest -v                       # all green
poetry run python -c "from app.main import app; print('BOOT_OK')"
poetry run uvicorn app.main:app --port 8001 &        # transient boot
sleep 5
curl -s localhost:8001/api/health
curl -s localhost:8001/api/dashboard | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['metrics']['count']==0; print('DASHBOARD_ZEROS_OK')"
curl -s -X POST localhost:8001/api/transactions -H 'content-type: application/json' \
  -d '{"date":"2026-06-05","type":"expense","category":"Groceries","total":114.98,"merchant":"Metro"}' \
  | python3 -c "import json,sys; t=json.load(sys.stdin); assert t['tax_breakdown']['GST']==5.0; print('TAX_OK')"
kill %1
```
Expected: `BOOT_OK`, `{"ok":true}`, `DASHBOARD_ZEROS_OK`, `TAX_OK`.

- [ ] **Step 4: Commit**

```bash
git add -A api/
git commit -m "feat(api): rewire main, schedulers, remove sheets-as-storage"
```

---

# PART B — FRONTEND (full rewrite)

### Task 16: Scaffold — deps, theme, API client, shell

**Files:**
- Modify: `web/package.json`
- Delete: `web/src/*` (all v1 files)
- Create: `web/src/theme.css`, `web/src/api.ts`, `web/src/main.tsx`, `web/src/App.tsx`, `web/src/components/TopBar.tsx`

- [ ] **Step 1: Deps**

```bash
cd web && rm -rf src && mkdir -p src/components src/pages
npm install react-router-dom @tanstack/react-query
```

- [ ] **Step 2: `web/src/theme.css`** — warm design tokens (spec §8):

```css
:root {
  --bg: #f7f4ef; --card: #ffffff; --text: #2d2a24; --muted: #a08c6a;
  --green: #3a8f63; --green-soft: #e9f5ee; --amber: #c2742c;
  --amber-soft: #fdeede; --shadow: 0 2px 8px rgba(180,150,100,.12);
  --radius: 16px;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.5 -apple-system, "Segoe UI", Roboto, sans-serif; }
.card { background: var(--card); border-radius: var(--radius);
  box-shadow: var(--shadow); padding: 18px; }
button.primary { background: var(--green); color: #fff; border: 0;
  border-radius: 10px; padding: 9px 16px; font-weight: 600; cursor: pointer; }
button.ghost { background: transparent; color: var(--green); border: 0;
  cursor: pointer; font-weight: 600; }
input, select { background: #fff; border: 1px solid #e7e0d4; color: var(--text);
  border-radius: 10px; padding: 8px 12px; font-size: 14px; }
.tag { padding: 2px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }
.tag.income { background: var(--green-soft); color: var(--green); }
.tag.expense { background: var(--amber-soft); color: var(--amber); }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th { text-align: left; color: var(--muted); font-weight: 600; padding: 8px 10px;
  border-bottom: 1px solid #efe9de; }
td { padding: 8px 10px; border-bottom: 1px solid #f4efe7; }
.muted { color: var(--muted); font-size: 13px; }
.bar { height: 6px; border-radius: 3px; background: var(--green-soft); }
.bar > div { height: 6px; border-radius: 3px; background: var(--green); }
.bar.warn > div { background: var(--amber); }
```

- [ ] **Step 3: `web/src/api.ts`** — transport only:

```typescript
export interface Category { id: number; name: string; type: "income" | "expense";
  percent: number; taxable: boolean; budget_monthly: number | null; }
export interface Txn { id: number; date: string; type: string; category: string;
  description: string; merchant: string; amount: number;
  tax_breakdown: Record<string, number>; total: number; counted: number;
  image_path: string | null; source: string; sync_status: string; }
export interface Budget { name: string; budget: number; spent: number; pct: number; }
export interface Dashboard {
  period: { start: string; end: string };
  metrics: { income: number; expenses: number; net: number; count: number };
  by_category: Record<string, number>;
  trend: { month: string; income: number; expenses: number }[];
  budgets: Budget[]; recent: Txn[];
}
export interface ChatSession { id: string; title: string; updated_at: string; }
export interface TaxProfile { id: number; name: string; is_active: boolean;
  components: { name: string; rate: number }[]; }
export interface RecurringRule { id: number; template: Record<string, unknown>;
  frequency: string; next_run: string; active: boolean; }
export interface ImportRecord { id: number; filename: string; status: string;
  error: string | null;
  rows: { date: string; type: string; category: string; merchant: string;
          description: string; total: number; duplicate: boolean; skip: boolean }[]; }
export interface UiSpec { title?: string; components: Record<string, unknown>[]; }
export type ChatEvent =
  | { type: "status"; text: string } | { type: "delta"; text: string }
  | { type: "tool"; name: string; status: string }
  | { type: "ui"; spec: UiSpec }
  | { type: "done"; text: string; error: string | null };

async function handle<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.error?.message ?? `Request failed (${response.status})`);
  }
  return response.json();
}
export const get = <T,>(url: string) => fetch(url).then((r) => handle<T>(r));
export const post = <T,>(url: string, body?: unknown) =>
  fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body) }).then((r) => handle<T>(r));
export const patch = <T,>(url: string, body: unknown) =>
  fetch(url, { method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) }).then((r) => handle<T>(r));
export const del = <T,>(url: string) =>
  fetch(url, { method: "DELETE" }).then((r) => handle<T>(r));
export const upload = <T,>(url: string, form: FormData) =>
  fetch(url, { method: "POST", body: form }).then((r) => handle<T>(r));

export async function streamChat(sessionId: string, message: string,
    image: File | null, onEvent: (e: ChatEvent) => void): Promise<void> {
  const form = new FormData();
  form.set("message", message);
  if (image) form.set("image", image);
  const response = await fetch(`/api/chat/sessions/${sessionId}/messages`,
    { method: "POST", body: form });
  if (!response.ok || !response.body) throw new Error(`chat failed (${response.status})`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut: number;
    while ((cut = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      for (const line of frame.split("\n"))
        if (line.startsWith("data: ")) onEvent(JSON.parse(line.slice(6)));
    }
  }
}
```

- [ ] **Step 4: Shell** — `web/src/main.tsx`:

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import "./theme.css";

const queryClient = new QueryClient();
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter><App /></BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
```

`web/src/App.tsx`:

```tsx
import { Route, Routes } from "react-router-dom";
import { useState } from "react";
import { TopBar } from "./components/TopBar";
import { ChatBubble } from "./components/ChatBubble";
import Dashboard from "./pages/Dashboard";
import Transactions from "./pages/Transactions";
import Chat from "./pages/Chat";
import Settings from "./pages/Settings";

export default function App() {
  const [period, setPeriod] = useState<string>("");   // "" = current month
  return (
    <div style={{ maxWidth: 1180, margin: "0 auto", padding: "0 24px 48px" }}>
      <TopBar period={period} onPeriod={setPeriod} />
      <Routes>
        <Route path="/" element={<Dashboard period={period} />} />
        <Route path="/transactions" element={<Transactions period={period} />} />
        <Route path="/chat" element={<Chat />} />
        <Route path="/settings" element={<Settings />} />
      </Routes>
      <ChatBubble />
    </div>
  );
}
```

`web/src/components/TopBar.tsx`:

```tsx
import { Link, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { get } from "../api";

function periodOptions(): { value: string; label: string }[] {
  const now = new Date();
  const options = [{ value: "", label: "This month" }];
  for (let back = 1; back <= 3; back++) {
    const d = new Date(now.getFullYear(), now.getMonth() - back, 1);
    const value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
    options.push({ value, label: d.toLocaleString("en", { month: "long", year: "numeric" }) });
  }
  return [...options, { value: "last3", label: "Last 3 months" },
          { value: "last6", label: "Last 6 months" }, { value: "ytd", label: "Year to date" }];
}

export function TopBar({ period, onPeriod }:
    { period: string; onPeriod: (p: string) => void }) {
  const location = useLocation();
  const sync = useQuery({ queryKey: ["sync"], refetchInterval: 30000,
    queryFn: () => get<{ enabled: boolean; pending: number }>("/api/sync/status") });
  const links = [["/", "Dashboard"], ["/transactions", "Transactions"],
                 ["/chat", "Chat"], ["/settings", "Settings"]] as const;
  return (
    <header style={{ display: "flex", alignItems: "center", gap: 18, padding: "18px 0",
                     borderBottom: "1px solid #efe9de", marginBottom: 24 }}>
      <b style={{ fontSize: 19 }}>💰 Expense Manager</b>
      <nav style={{ display: "flex", gap: 4 }}>
        {links.map(([to, label]) => (
          <Link key={to} to={to} style={{ textDecoration: "none", padding: "6px 12px",
            borderRadius: 10, color: location.pathname === to ? "#fff" : "var(--text)",
            background: location.pathname === to ? "var(--green)" : "transparent" }}>
            {label}</Link>))}
      </nav>
      <span style={{ flex: 1 }} />
      <select value={period} onChange={(e) => onPeriod(e.target.value)}>
        {periodOptions().map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
      <span title={sync.data?.enabled ? `${sync.data.pending} pending` : "Google sync off"}
            style={{ width: 10, height: 10, borderRadius: 5,
                     background: !sync.data?.enabled ? "#cfc6b8"
                       : sync.data.pending ? "var(--amber)" : "var(--green)" }} />
    </header>
  );
}
```

- [ ] **Step 5: Stub remaining files so the build passes** — create minimal placeholder components that the next tasks replace with real implementations. Each stub must compile:

```tsx
// web/src/pages/Dashboard.tsx  (replaced in Task 17)
export default function Dashboard({ period }: { period: string }) {
  return <div className="card">Dashboard — coming in Task 17 ({period || "this month"})</div>;
}
```

```tsx
// web/src/pages/Transactions.tsx  (replaced in Task 18)
export default function Transactions({ period }: { period: string }) {
  return <div className="card">Transactions — Task 18 ({period})</div>;
}
```

```tsx
// web/src/pages/Chat.tsx  (replaced in Task 19)
export default function Chat() { return <div className="card">Chat — Task 19</div>; }
```

```tsx
// web/src/pages/Settings.tsx  (replaced in Task 20)
export default function Settings() { return <div className="card">Settings — Task 20</div>; }
```

```tsx
// web/src/components/ChatBubble.tsx  (replaced in Task 19)
export function ChatBubble() { return null; }
```

- [ ] **Step 6: Build** — `cd web && npm run build` → expect clean
- [ ] **Step 7: Commit**

```bash
git add web/
git commit -m "feat(web): scaffold warm-theme shell, router, query, API client"
```

---

### Task 17: Dashboard page (metrics, quick-add, budgets rail, charts, recent)

**Files:**
- Replace: `web/src/pages/Dashboard.tsx`
- Create: `web/src/components/QuickAdd.tsx`, `web/src/components/Charts.tsx`, `web/src/components/BudgetRail.tsx`, `web/src/components/RecentTable.tsx`, `web/src/components/Lightbox.tsx`

- [ ] **Step 1: `web/src/components/Charts.tsx`**

```tsx
import { CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart,
         ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const COLORS = ["#3a8f63", "#c2742c", "#7a9e7e", "#d9a85c", "#a08c6a",
                "#5e8ca7", "#b56a5d", "#8a7ba8"];

export function TrendChart({ data }:
    { data: { month: string; income: number; expenses: number }[] }) {
  return (
    <ResponsiveContainer width="100%" height={240}>
      <LineChart data={data}>
        <CartesianGrid strokeDasharray="3 3" stroke="#efe9de" />
        <XAxis dataKey="month" stroke="#a08c6a" /><YAxis stroke="#a08c6a" />
        <Tooltip /><Legend />
        <Line type="monotone" dataKey="income" stroke="#3a8f63" strokeWidth={2} dot={false} />
        <Line type="monotone" dataKey="expenses" stroke="#c2742c" strokeWidth={2} dot={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function CategoryPie({ data }: { data: Record<string, number> }) {
  const rows = Object.entries(data).map(([name, value]) => ({ name, value }));
  if (!rows.length) return <p className="muted">No expenses yet this period.</p>;
  return (
    <ResponsiveContainer width="100%" height={240}>
      <PieChart>
        <Pie data={rows} dataKey="value" nameKey="name" outerRadius={85} label>
          {rows.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
        </Pie>
        <Tooltip /><Legend />
      </PieChart>
    </ResponsiveContainer>
  );
}
```

- [ ] **Step 2: `web/src/components/QuickAdd.tsx`** — modal with live tax preview:

```tsx
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post, type Category, type TaxProfile } from "../api";

export function QuickAdd({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const categories = useQuery({ queryKey: ["categories"],
    queryFn: () => get<Category[]>("/api/categories") });
  const profiles = useQuery({ queryKey: ["tax-profiles"],
    queryFn: () => get<TaxProfile[]>("/api/tax-profiles") });
  const [form, setForm] = useState({ date: new Date().toISOString().slice(0, 10),
    type: "expense", category: "", total: "", merchant: "", description: "" });
  const [error, setError] = useState("");

  const selected = categories.data?.find((c) => c.name === form.category);
  const active = profiles.data?.find((p) => p.is_active);
  const total = parseFloat(form.total) || 0;
  const rateSum = (active?.components ?? []).reduce((s, c) => s + c.rate, 0);
  const taxPreview = selected?.taxable && total > 0 && active
    ? active.components.map((c) => ({ name: c.name,
        value: (total / (1 + rateSum / 100)) * (c.rate / 100) }))
    : [];

  const save = useMutation({
    mutationFn: () => post("/api/transactions",
      { ...form, total: parseFloat(form.total) }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["transactions"] });
      onClose();
    },
    onError: (e: Error) => setError(e.message),
  });

  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));
  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(45,42,36,.35)",
                  display: "flex", alignItems: "center", justifyContent: "center", zIndex: 50 }}
         onClick={onClose}>
      <div className="card" style={{ width: 420 }} onClick={(e) => e.stopPropagation()}>
        <h3 style={{ marginTop: 0 }}>Add transaction</h3>
        {error && <p style={{ color: "var(--amber)" }}>{error}</p>}
        <div style={{ display: "grid", gap: 10 }}>
          <input type="date" value={form.date} onChange={(e) => set("date", e.target.value)} />
          <select value={form.type} onChange={(e) => set("type", e.target.value)}>
            <option value="expense">Expense</option><option value="income">Income</option>
          </select>
          <select value={form.category} onChange={(e) => set("category", e.target.value)}>
            <option value="">Category…</option>
            {(categories.data ?? []).filter((c) => c.type === form.type)
              .map((c) => <option key={c.id}>{c.name}</option>)}
          </select>
          <input placeholder="Total paid ($)" inputMode="decimal" value={form.total}
                 onChange={(e) => set("total", e.target.value)} />
          {taxPreview.length > 0 && (
            <p className="muted">Includes {taxPreview.map((t) =>
              `${t.name} $${t.value.toFixed(2)}`).join(" + ")}</p>)}
          {selected && !selected.taxable && <p className="muted">No tax for {selected.name}.</p>}
          <input placeholder="Merchant" value={form.merchant}
                 onChange={(e) => set("merchant", e.target.value)} />
          <input placeholder="Note (optional)" value={form.description}
                 onChange={(e) => set("description", e.target.value)} />
          <button className="primary" disabled={!form.category || !total || save.isPending}
                  onClick={() => save.mutate()}>
            {save.isPending ? "Saving…" : "Save"}</button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: `web/src/components/BudgetRail.tsx`**

```tsx
import type { Budget } from "../api";

export function BudgetRail({ budgets }: { budgets: Budget[] }) {
  return (
    <div className="card">
      <b>Budgets</b>
      {budgets.length === 0 && (
        <p className="muted">Set monthly budgets per category in Settings.</p>)}
      {budgets.map((b) => (
        <div key={b.name} style={{ marginTop: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}>
            <span>{b.name}{b.pct >= 90 ? " ⚠️" : ""}</span>
            <span className="muted">${b.spent.toFixed(0)} / ${b.budget.toFixed(0)}</span>
          </div>
          <div className={`bar${b.pct >= 90 ? " warn" : ""}`}>
            <div style={{ width: `${Math.min(b.pct, 100)}%` }} />
          </div>
        </div>))}
    </div>
  );
}
```

- [ ] **Step 4: `web/src/components/Lightbox.tsx` + `RecentTable.tsx`**

```tsx
// Lightbox.tsx
export function Lightbox({ src, onClose }: { src: string; onClose: () => void }) {
  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(45,42,36,.7)",
                  display: "flex", alignItems: "center", justifyContent: "center", zIndex: 60 }}
         onClick={onClose}>
      <img src={src} style={{ maxWidth: "85vw", maxHeight: "85vh", borderRadius: 12 }} />
    </div>
  );
}
```

```tsx
// RecentTable.tsx
import { useState } from "react";
import { Link } from "react-router-dom";
import type { Txn } from "../api";
import { Lightbox } from "./Lightbox";

export function RecentTable({ rows, title = "Recent transactions" }:
    { rows: Txn[]; title?: string }) {
  const [lightbox, setLightbox] = useState<string | null>(null);
  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
        <b>{title}</b><Link to="/transactions" className="muted">View all →</Link>
      </div>
      <table>
        <thead><tr><th>Date</th><th>Type</th><th>Category</th><th>Merchant</th>
                   <th>Total</th><th>Counted</th><th>Receipt</th></tr></thead>
        <tbody>
          {rows.map((t) => (
            <tr key={t.id}>
              <td>{t.date}</td>
              <td><span className={`tag ${t.type}`}>{t.type}</span></td>
              <td>{t.category}</td><td>{t.merchant || t.description}</td>
              <td>${t.total.toFixed(2)}</td><td>${t.counted.toFixed(2)}</td>
              <td>{t.image_path
                ? <button className="ghost" onClick={() => setLightbox(`/api/receipts/${t.id}`)}>🧾</button>
                : <span className="muted">—</span>}</td>
            </tr>))}
          {rows.length === 0 && (
            <tr><td colSpan={7} className="muted">
              Nothing yet — add your first expense with the + button or via chat.
            </td></tr>)}
        </tbody>
      </table>
      {lightbox && <Lightbox src={lightbox} onClose={() => setLightbox(null)} />}
    </div>
  );
}
```

- [ ] **Step 5: Replace `web/src/pages/Dashboard.tsx`**

```tsx
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { get, type Dashboard as DashboardData } from "../api";
import { BudgetRail } from "../components/BudgetRail";
import { CategoryPie, TrendChart } from "../components/Charts";
import { QuickAdd } from "../components/QuickAdd";
import { RecentTable } from "../components/RecentTable";

function Metric({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="card" style={{ flex: 1, textAlign: "center" }}>
      <div className="muted" style={{ textTransform: "uppercase", fontSize: 11,
                                      letterSpacing: ".06em" }}>{label}</div>
      <div style={{ fontSize: 26, fontWeight: 700, color: tone }}>{value}</div>
    </div>
  );
}

export default function Dashboard({ period }: { period: string }) {
  const [adding, setAdding] = useState(false);
  const { data, isLoading } = useQuery({
    queryKey: ["dashboard", period],
    queryFn: () => get<DashboardData>(`/api/dashboard?period=${period}`),
  });
  if (isLoading || !data) return <div className="card">Loading…</div>;
  const { metrics } = data;
  return (
    <div style={{ display: "flex", gap: 16 }}>
      <div style={{ flex: 3, display: "flex", flexDirection: "column", gap: 16 }}>
        <div style={{ display: "flex", gap: 16 }}>
          <Metric label="Income" value={`$${metrics.income.toFixed(2)}`} tone="var(--green)" />
          <Metric label="Expenses" value={`$${metrics.expenses.toFixed(2)}`} tone="var(--amber)" />
          <Metric label="Net" value={`$${metrics.net.toFixed(2)}`} />
          <div className="card" style={{ flex: 1, display: "flex", alignItems: "center",
                                         justifyContent: "center" }}>
            <button className="primary" onClick={() => setAdding(true)}>＋ Quick add</button>
          </div>
        </div>
        <div style={{ display: "flex", gap: 16 }}>
          <div className="card" style={{ flex: 3 }}>
            <b>Income vs expenses</b><TrendChart data={data.trend} /></div>
          <div className="card" style={{ flex: 2 }}>
            <b>Expenses by category</b><CategoryPie data={data.by_category} /></div>
        </div>
        <RecentTable rows={data.recent} />
      </div>
      <div style={{ flex: 1 }}><BudgetRail budgets={data.budgets} /></div>
      {adding && <QuickAdd onClose={() => setAdding(false)} />}
    </div>
  );
}
```

- [ ] **Step 6: Build** — `npm run build` → clean
- [ ] **Step 7: Commit**

```bash
git add web/src/
git commit -m "feat(web): dashboard with metrics, quick-add, budgets rail, charts"
```

---

### Task 18: Transactions page (filters, edit, bulk, export, lightbox)

**Files:**
- Replace: `web/src/pages/Transactions.tsx`

- [ ] **Step 1: Implement**

```tsx
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { del, get, patch, post, type Category, type Txn } from "../api";
import { Lightbox } from "../components/Lightbox";

export default function Transactions({ period }: { period: string }) {
  const queryClient = useQueryClient();
  const [filters, setFilters] = useState({ type: "", category: "", q: "" });
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState<Partial<Txn>>({});
  const [lightbox, setLightbox] = useState<string | null>(null);
  const [error, setError] = useState("");

  const categories = useQuery({ queryKey: ["categories"],
    queryFn: () => get<Category[]>("/api/categories") });
  const query = new URLSearchParams({ period, ...filters, limit: "200" });
  const txns = useQuery({ queryKey: ["transactions", period, filters],
    queryFn: () => get<Txn[]>(`/api/transactions?${query}`) });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["transactions"] });
    queryClient.invalidateQueries({ queryKey: ["dashboard"] });
  };
  const saveEdit = useMutation({
    mutationFn: (id: number) => patch(`/api/transactions/${id}`, draft),
    onSuccess: () => { setEditing(null); refresh(); },
    onError: (e: Error) => setError(e.message) });
  const bulk = useMutation({
    mutationFn: (body: { ids: number[]; action: string; category?: string }) =>
      post("/api/transactions/bulk", body),
    onSuccess: () => { setSelected(new Set()); refresh(); } });
  const remove = useMutation({
    mutationFn: (id: number) => del(`/api/transactions/${id}`),
    onSuccess: refresh });

  const toggle = (id: number) => setSelected((s) => {
    const next = new Set(s); next.has(id) ? next.delete(id) : next.add(id); return next; });
  const setF = (k: string, v: string) => setFilters((f) => ({ ...f, [k]: v }));

  return (
    <div className="card">
      <div style={{ display: "flex", gap: 10, marginBottom: 14, alignItems: "center" }}>
        <select value={filters.type} onChange={(e) => setF("type", e.target.value)}>
          <option value="">All types</option>
          <option value="expense">Expense</option><option value="income">Income</option>
        </select>
        <select value={filters.category} onChange={(e) => setF("category", e.target.value)}>
          <option value="">All categories</option>
          {(categories.data ?? []).map((c) => <option key={c.id}>{c.name}</option>)}
        </select>
        <input placeholder="Search merchant/note…" value={filters.q}
               onChange={(e) => setF("q", e.target.value)} style={{ flex: 1 }} />
        <a href="/api/transactions/export.csv" download><button className="ghost">⬇ CSV</button></a>
      </div>

      {selected.size > 0 && (
        <div style={{ display: "flex", gap: 10, marginBottom: 10, alignItems: "center",
                      background: "var(--green-soft)", borderRadius: 10, padding: "8px 12px" }}>
          <b>{selected.size} selected</b>
          <select defaultValue="" onChange={(e) => e.target.value &&
              bulk.mutate({ ids: [...selected], action: "recategorize",
                            category: e.target.value })}>
            <option value="">Recategorize to…</option>
            {(categories.data ?? []).map((c) => <option key={c.id}>{c.name}</option>)}
          </select>
          <button className="ghost" style={{ color: "var(--amber)" }}
                  onClick={() => bulk.mutate({ ids: [...selected], action: "delete" })}>
            Delete</button>
        </div>)}
      {error && <p style={{ color: "var(--amber)" }}>{error}</p>}

      <table>
        <thead><tr><th></th><th>Date</th><th>Type</th><th>Category</th><th>Merchant</th>
                   <th>Total</th><th>Taxes</th><th>Counted</th><th>Receipt</th><th></th></tr></thead>
        <tbody>
          {(txns.data ?? []).map((t) => editing === t.id ? (
            <tr key={t.id}>
              <td></td>
              <td><input type="date" defaultValue={t.date}
                    onChange={(e) => setDraft((d) => ({ ...d, date: e.target.value }))} /></td>
              <td>{t.type}</td>
              <td><select defaultValue={t.category}
                    onChange={(e) => setDraft((d) => ({ ...d, category: e.target.value }))}>
                  {(categories.data ?? []).filter((c) => c.type === t.type)
                    .map((c) => <option key={c.id}>{c.name}</option>)}</select></td>
              <td><input defaultValue={t.merchant} style={{ width: 110 }}
                    onChange={(e) => setDraft((d) => ({ ...d, merchant: e.target.value }))} /></td>
              <td><input defaultValue={t.total} style={{ width: 80 }} inputMode="decimal"
                    onChange={(e) => setDraft((d) =>
                      ({ ...d, total: parseFloat(e.target.value) }))} /></td>
              <td className="muted">auto</td><td className="muted">auto</td><td></td>
              <td><button className="ghost" onClick={() => saveEdit.mutate(t.id)}>Save</button>
                  <button className="ghost" onClick={() => setEditing(null)}>✕</button></td>
            </tr>
          ) : (
            <tr key={t.id}>
              <td><input type="checkbox" checked={selected.has(t.id)}
                         onChange={() => toggle(t.id)} /></td>
              <td>{t.date}</td>
              <td><span className={`tag ${t.type}`}>{t.type}</span></td>
              <td>{t.category}</td><td>{t.merchant || t.description}</td>
              <td>${t.total.toFixed(2)}</td>
              <td className="muted">{Object.entries(t.tax_breakdown)
                .map(([k, v]) => `${k} $${v.toFixed(2)}`).join(", ") || "—"}</td>
              <td>${t.counted.toFixed(2)}</td>
              <td>{t.image_path
                ? <button className="ghost"
                    onClick={() => setLightbox(`/api/receipts/${t.id}`)}>🧾</button>
                : "—"}</td>
              <td><button className="ghost"
                    onClick={() => { setEditing(t.id); setDraft({}); }}>✎</button>
                  <button className="ghost" style={{ color: "var(--amber)" }}
                    onClick={() => remove.mutate(t.id)}>🗑</button></td>
            </tr>))}
          {(txns.data ?? []).length === 0 && (
            <tr><td colSpan={10} className="muted">No transactions match.</td></tr>)}
        </tbody>
      </table>
      {lightbox && <Lightbox src={lightbox} onClose={() => setLightbox(null)} />}
    </div>
  );
}
```

- [ ] **Step 2: Build** — clean
- [ ] **Step 3: Commit**

```bash
git add web/src/pages/Transactions.tsx
git commit -m "feat(web): transactions page with edit, bulk ops, CSV, lightbox"
```

---

### Task 19: Chat (sessions sidebar + thread + GenUI) and floating bubble

**Files:**
- Create: `web/src/components/GenUI.tsx` (port from v1 git history — `git show HEAD~N:web/src/components/GenUI.tsx` at the initial commit; adjust palette to warm COLORS from Charts.tsx)
- Create: `web/src/components/ChatThread.tsx`
- Replace: `web/src/pages/Chat.tsx`, `web/src/components/ChatBubble.tsx`

- [ ] **Step 1: Restore GenUI** — `git show $(git rev-list --max-parents=0 HEAD):web/src/components/GenUI.tsx > web/src/components/GenUI.tsx`, then update its `PALETTE` constant to `["#3a8f63", "#c2742c", "#7a9e7e", "#d9a85c", "#a08c6a", "#5e8ca7", "#b56a5d", "#8a7ba8"]` and the grid stroke colors `#2c3344`→`#efe9de`, axis `#8aa0b8`→`#a08c6a`. Fix the import line to `import type { UiComponentSpec, UiSpec } from "../api";` and add to `api.ts`:

```typescript
export interface UiComponentSpec { type: string; title?: string; label?: string;
  value?: number | string; unit?: string; data?: Record<string, unknown>[];
  xKey?: string; series?: string[]; columns?: string[]; rows?: unknown[][]; }
```
(and change `UiSpec.components` to `UiComponentSpec[]`).

- [ ] **Step 2: `web/src/components/ChatThread.tsx`** — reusable thread (page + bubble):

```tsx
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { get, streamChat, type ChatEvent, type UiSpec } from "../api";
import { GenUI } from "./GenUI";

interface Item { role: "user" | "assistant"; text: string;
  uiSpecs?: UiSpec[]; tools?: string[]; }

export function ChatThread({ sessionId, compact = false }:
    { sessionId: string; compact?: boolean }) {
  const queryClient = useQueryClient();
  const [items, setItems] = useState<Item[]>([]);
  const [input, setInput] = useState("");
  const [image, setImage] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);

  const history = useQuery({ queryKey: ["chat", sessionId],
    queryFn: () => get<{ messages: { role: string; content:
      { text: string; ui_specs?: UiSpec[] } }[] }>(`/api/chat/sessions/${sessionId}`) });

  useEffect(() => {
    if (history.data) setItems(history.data.messages.map((m) => ({
      role: m.role as "user" | "assistant", text: m.content.text,
      uiSpecs: m.content.ui_specs ?? [] })));
  }, [history.data]);
  useEffect(() => { scroller.current?.scrollTo(0, 1e9); }, [items]);

  async function send() {
    if (busy || (!input.trim() && !image)) return;
    const message = input.trim(); const attachment = image;
    setInput(""); setImage(null); setBusy(true);
    setItems((prev) => [...prev, { role: "user", text: message || "(receipt image)" },
                        { role: "assistant", text: "", uiSpecs: [], tools: [] }]);
    const applyLast = (fn: (i: Item) => Item) => setItems((prev) => {
      const next = [...prev]; next[next.length - 1] = fn(next[next.length - 1]); return next; });
    try {
      await streamChat(sessionId, message, attachment, (event: ChatEvent) => {
        if (event.type === "delta") applyLast((i) => ({ ...i, text: i.text + event.text }));
        else if (event.type === "tool" && event.status === "start")
          applyLast((i) => ({ ...i, tools: [...(i.tools ?? []), event.name] }));
        else if (event.type === "ui")
          applyLast((i) => ({ ...i, uiSpecs: [...(i.uiSpecs ?? []), event.spec] }));
        else if (event.type === "done") {
          applyLast((i) => ({ ...i, text: i.text || event.text }));
          queryClient.invalidateQueries({ queryKey: ["dashboard"] });
          queryClient.invalidateQueries({ queryKey: ["transactions"] });
          queryClient.invalidateQueries({ queryKey: ["chat-sessions"] });
        }
      });
    } catch (err) {
      applyLast((i) => ({ ...i, text: `${i.text}\n⚠ ${String(err)}` }));
    } finally { setBusy(false); }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div ref={scroller} style={{ flex: 1, overflowY: "auto", display: "flex",
                                   flexDirection: "column", gap: 10, padding: 4 }}>
        {items.length === 0 && (
          <p className="muted" style={{ margin: "auto", textAlign: "center" }}>
            Ask “what are my expenses this month?” or drop a receipt photo.</p>)}
        {items.map((item, index) => (
          <div key={index} style={{
            alignSelf: item.role === "user" ? "flex-end" : "flex-start",
            background: item.role === "user" ? "var(--green)" : "#fff",
            color: item.role === "user" ? "#fff" : "var(--text)",
            borderRadius: 14, padding: "10px 14px",
            maxWidth: compact ? "95%" : "80%", whiteSpace: "pre-wrap",
            boxShadow: "var(--shadow)" }}>
            {(item.tools ?? []).length > 0 && (
              <div className="muted" style={{ fontSize: 11, marginBottom: 6 }}>
                ⚙ {(item.tools ?? []).join(" · ")}</div>)}
            {item.text || (busy && index === items.length - 1 ? "…" : "")}
            {(item.uiSpecs ?? []).map((spec, i) => <GenUI key={i} spec={spec} />)}
          </div>))}
      </div>
      <div style={{ display: "flex", gap: 8, paddingTop: 10 }}>
        <label style={{ cursor: "pointer", fontSize: 20, alignSelf: "center" }}>📷
          <input type="file" accept="image/*" hidden
                 onChange={(e) => setImage(e.target.files?.[0] ?? null)} /></label>
        {image && <span className="muted" style={{ alignSelf: "center" }}>{image.name}</span>}
        <input style={{ flex: 1 }} value={input} disabled={busy}
               placeholder="Message or receipt details…"
               onChange={(e) => setInput(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && send()} />
        <button className="primary" disabled={busy || (!input.trim() && !image)}
                onClick={send}>{busy ? "…" : "Send"}</button>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: `web/src/pages/Chat.tsx`** — sessions sidebar:

```tsx
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { del, get, post, type ChatSession } from "../api";
import { ChatThread } from "../components/ChatThread";

export default function Chat() {
  const queryClient = useQueryClient();
  const [active, setActive] = useState<string | null>(null);
  const sessions = useQuery({ queryKey: ["chat-sessions"],
    queryFn: () => get<ChatSession[]>("/api/chat/sessions") });
  const create = useMutation({
    mutationFn: () => post<ChatSession>("/api/chat/sessions"),
    onSuccess: (s) => {
      queryClient.invalidateQueries({ queryKey: ["chat-sessions"] });
      setActive(s.id);
    } });
  const remove = useMutation({
    mutationFn: (id: string) => del(`/api/chat/sessions/${id}`),
    onSuccess: (_, id) => {
      queryClient.invalidateQueries({ queryKey: ["chat-sessions"] });
      if (active === id) setActive(null);
    } });

  return (
    <div style={{ display: "flex", gap: 16, height: "calc(100vh - 130px)" }}>
      <div className="card" style={{ width: 260, overflowY: "auto" }}>
        <button className="primary" style={{ width: "100%" }}
                onClick={() => create.mutate()}>＋ New chat</button>
        {(sessions.data ?? []).map((s) => (
          <div key={s.id} onClick={() => setActive(s.id)}
               style={{ padding: "10px 8px", borderRadius: 10, cursor: "pointer",
                        marginTop: 6, display: "flex", justifyContent: "space-between",
                        background: active === s.id ? "var(--green-soft)" : "transparent" }}>
            <span style={{ overflow: "hidden", textOverflow: "ellipsis",
                           whiteSpace: "nowrap" }}>{s.title}</span>
            <button className="ghost" style={{ color: "var(--amber)" }}
                    onClick={(e) => { e.stopPropagation(); remove.mutate(s.id); }}>✕</button>
          </div>))}
      </div>
      <div className="card" style={{ flex: 1 }}>
        {active ? <ChatThread sessionId={active} />
          : <p className="muted" style={{ textAlign: "center", marginTop: 80 }}>
              Pick a chat or start a new one.</p>}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: `web/src/components/ChatBubble.tsx`** — floating quick chat:

```tsx
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { post, type ChatSession } from "../api";
import { ChatThread } from "./ChatThread";

export function ChatBubble() {
  const [open, setOpen] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () => post<ChatSession>("/api/chat/sessions"),
    onSuccess: (s) => setSessionId(s.id) });

  const toggle = () => {
    if (!open && !sessionId) create.mutate();
    setOpen((o) => !o);
  };
  return (
    <>
      {open && sessionId && (
        <div className="card" style={{ position: "fixed", right: 24, bottom: 90,
             width: 400, height: 520, zIndex: 40, display: "flex" }}>
          <ChatThread sessionId={sessionId} compact /></div>)}
      <button onClick={toggle} style={{ position: "fixed", right: 24, bottom: 24,
          width: 54, height: 54, borderRadius: 27, border: 0, fontSize: 22,
          background: "var(--green)", color: "#fff", cursor: "pointer",
          boxShadow: "var(--shadow)", zIndex: 40 }}>
        {open ? "✕" : "💬"}</button>
    </>
  );
}
```

- [ ] **Step 5: Build** — clean
- [ ] **Step 6: Commit**

```bash
git add web/src/
git commit -m "feat(web): chat sessions, thread with GenUI, floating bubble"
```

---

### Task 20: Settings page (categories, tax, recurring, connections, import)

**Files:**
- Create: `web/src/components/ImportReview.tsx`
- Replace: `web/src/pages/Settings.tsx`

- [ ] **Step 1: `web/src/components/ImportReview.tsx`**

```tsx
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { post, upload, type ImportRecord } from "../api";

export function ImportReview() {
  const queryClient = useQueryClient();
  const [record, setRecord] = useState<ImportRecord | null>(null);
  const [skips, setSkips] = useState<Set<number>>(new Set());
  const [error, setError] = useState("");

  const uploadFile = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData(); form.set("file", file);
      return upload<ImportRecord>("/api/imports", form);
    },
    onSuccess: (r) => {
      setRecord(r);
      setSkips(new Set(r.rows.map((row, i) => row.skip ? i : -1).filter((i) => i >= 0)));
      if (r.status === "failed") setError(r.error ?? "Parse failed");
    },
    onError: (e: Error) => setError(e.message) });

  const approve = useMutation({
    mutationFn: () => post(`/api/imports/${record!.id}/approve`, {
      indexes: record!.rows.map((_, i) => i).filter((i) => !skips.has(i)) }),
    onSuccess: () => {
      setRecord(null);
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["transactions"] });
    } });

  const toggleSkip = (index: number) => setSkips((s) => {
    const next = new Set(s); next.has(index) ? next.delete(index) : next.add(index);
    return next; });

  return (
    <div>
      <p className="muted">Upload a bank statement or sheet (CSV, XLSX, PDF).
        The agent parses and categorizes; likely duplicates are pre-skipped.</p>
      <input type="file" accept=".csv,.xlsx,.xls,.pdf"
             onChange={(e) => e.target.files?.[0] && uploadFile.mutate(e.target.files[0])} />
      {uploadFile.isPending && <p>Parsing with the agent…</p>}
      {error && <p style={{ color: "var(--amber)" }}>{error}</p>}
      {record?.status === "review" && (
        <>
          <table style={{ marginTop: 12 }}>
            <thead><tr><th>Keep</th><th>Date</th><th>Type</th><th>Category</th>
                       <th>Merchant</th><th>Total</th><th></th></tr></thead>
            <tbody>
              {record.rows.map((row, index) => (
                <tr key={index} style={{ opacity: skips.has(index) ? 0.45 : 1 }}>
                  <td><input type="checkbox" checked={!skips.has(index)}
                             onChange={() => toggleSkip(index)} /></td>
                  <td>{row.date}</td>
                  <td><span className={`tag ${row.type}`}>{row.type}</span></td>
                  <td>{row.category}</td><td>{row.merchant}</td>
                  <td>${row.total.toFixed(2)}</td>
                  <td>{row.duplicate &&
                    <span style={{ color: "var(--amber)" }}>possible duplicate</span>}</td>
                </tr>))}
            </tbody>
          </table>
          <button className="primary" style={{ marginTop: 12 }}
                  disabled={approve.isPending} onClick={() => approve.mutate()}>
            Approve {record.rows.length - skips.size} rows</button>
        </>)}
    </div>
  );
}
```

- [ ] **Step 2: Replace `web/src/pages/Settings.tsx`** — sectioned page:

```tsx
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { del, get, post, type Category, type RecurringRule, type TaxProfile } from "../api";
import { ImportReview } from "../components/ImportReview";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <div className="card" style={{ marginBottom: 16 }}>
    <h3 style={{ marginTop: 0 }}>{title}</h3>{children}</div>;
}

export default function Settings() {
  const queryClient = useQueryClient();
  const invalidate = (key: string) => queryClient.invalidateQueries({ queryKey: [key] });

  // --- Categories ---
  const categories = useQuery({ queryKey: ["categories"],
    queryFn: () => get<Category[]>("/api/categories") });
  const saveCategory = useMutation({
    mutationFn: (c: Partial<Category>) => post("/api/categories", c),
    onSuccess: () => invalidate("categories") });
  const removeCategory = useMutation({
    mutationFn: (id: number) => del(`/api/categories/${id}`),
    onSuccess: () => invalidate("categories") });
  const [newCategory, setNewCategory] = useState({ name: "", type: "expense",
    percent: 100, taxable: true, budget_monthly: "" });

  // --- Tax profiles ---
  const profiles = useQuery({ queryKey: ["tax-profiles"],
    queryFn: () => get<TaxProfile[]>("/api/tax-profiles") });
  const activate = useMutation({
    mutationFn: (p: TaxProfile) => post("/api/tax-profiles",
      { name: p.name, components: p.components, activate: true }),
    onSuccess: () => invalidate("tax-profiles") });

  // --- Recurring ---
  const rules = useQuery({ queryKey: ["recurring"],
    queryFn: () => get<RecurringRule[]>("/api/recurring") });
  const removeRule = useMutation({ mutationFn: (id: number) => del(`/api/recurring/${id}`),
    onSuccess: () => invalidate("recurring") });

  // --- Connections ---
  const google = useQuery({ queryKey: ["google"],
    queryFn: () => get<{ configured: boolean; connected: boolean;
      sheet_url: string | null; pending: number }>("/api/google/status") });
  const whatsapp = useQuery({ queryKey: ["whatsapp"], refetchInterval: 4000,
    queryFn: () => get<{ status: string; qr: string | null }>("/api/whatsapp/qr") });
  const syncNow = useMutation({ mutationFn: () => post("/api/sync/now"),
    onSuccess: () => invalidate("google") });

  return (
    <div>
      <Section title="Categories & budgets">
        <table>
          <thead><tr><th>Name</th><th>Type</th><th>% counted</th><th>Taxable</th>
                     <th>Budget/mo</th><th></th></tr></thead>
          <tbody>
            {(categories.data ?? []).map((c) => (
              <tr key={c.id}>
                <td>{c.name}</td>
                <td><span className={`tag ${c.type}`}>{c.type}</span></td>
                <td><input type="number" defaultValue={c.percent} min={0} max={100}
                      style={{ width: 70 }}
                      onBlur={(e) => saveCategory.mutate(
                        { ...c, percent: Number(e.target.value) })} /></td>
                <td><input type="checkbox" defaultChecked={c.taxable}
                      onChange={(e) => saveCategory.mutate(
                        { ...c, taxable: e.target.checked })} /></td>
                <td><input type="number" defaultValue={c.budget_monthly ?? ""}
                      placeholder="—" style={{ width: 90 }}
                      onBlur={(e) => saveCategory.mutate({ ...c,
                        budget_monthly: e.target.value ? Number(e.target.value) : null })} /></td>
                <td><button className="ghost" style={{ color: "var(--amber)" }}
                      onClick={() => removeCategory.mutate(c.id)}>✕</button></td>
              </tr>))}
            <tr>
              <td><input placeholder="New category" value={newCategory.name}
                    onChange={(e) => setNewCategory({ ...newCategory, name: e.target.value })} /></td>
              <td><select value={newCategory.type}
                    onChange={(e) => setNewCategory({ ...newCategory, type: e.target.value })}>
                  <option value="expense">expense</option>
                  <option value="income">income</option></select></td>
              <td colSpan={3}></td>
              <td><button className="ghost" disabled={!newCategory.name}
                    onClick={() => { saveCategory.mutate({ ...newCategory,
                      budget_monthly: newCategory.budget_monthly
                        ? Number(newCategory.budget_monthly) : null } as Partial<Category>);
                      setNewCategory({ ...newCategory, name: "" }); }}>＋ Add</button></td>
            </tr>
          </tbody>
        </table>
      </Section>

      <Section title="Tax profile">
        <p className="muted">Active profile drives tax back-calculation for taxable categories.</p>
        {(profiles.data ?? []).map((p) => (
          <label key={p.id} style={{ display: "block", marginTop: 8 }}>
            <input type="radio" name="tax" checked={p.is_active}
                   onChange={() => activate.mutate(p)} />{" "}
            <b>{p.name}</b>{" "}
            <span className="muted">
              {p.components.map((c) => `${c.name} ${c.rate}%`).join(" + ")}</span>
          </label>))}
      </Section>

      <Section title="Recurring rules">
        {(rules.data ?? []).length === 0 &&
          <p className="muted">None yet — ask the agent: “add recurring rent $1500 on the 1st”.</p>}
        {(rules.data ?? []).map((r) => (
          <p key={r.id}>{String(r.template.category)} ${String(r.template.total)} ·
            {r.frequency} · next {r.next_run}
            <button className="ghost" style={{ color: "var(--amber)" }}
                    onClick={() => removeRule.mutate(r.id)}>✕</button></p>))}
      </Section>

      <Section title="Google sync">
        {!google.data ? <p>Loading…</p>
          : !google.data.configured ? <p className="muted">
              Set GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET in .env to enable.</p>
          : google.data.connected ? (
            <p>✅ Connected — {google.data.pending} pending{" "}
              <button className="ghost" onClick={() => syncNow.mutate()}>Sync now</button>
              {google.data.sheet_url &&
                <a href={google.data.sheet_url} target="_blank" rel="noreferrer"> Open sheet ↗</a>}
            </p>)
          : <a href="/api/google/auth"><button className="primary">Connect Google</button></a>}
      </Section>

      <Section title="WhatsApp">
        {whatsapp.data?.status === "connected"
          ? <p>✅ Connected — message the linked account to chat with the agent.</p>
          : whatsapp.data?.qr
            ? <><p>Scan: WhatsApp → Settings → Linked devices → Link a device</p>
                <img src={whatsapp.data.qr} style={{ width: 220, background: "#fff",
                     padding: 8, borderRadius: 10 }} /></>
            : <p className="muted">Waiting for QR… (status: {whatsapp.data?.status})</p>}
      </Section>

      <Section title="Import statements & sheets"><ImportReview /></Section>
    </div>
  );
}
```

- [ ] **Step 3: Build** — `npm run build` → clean
- [ ] **Step 4: Commit**

```bash
git add web/src/
git commit -m "feat(web): settings — categories, tax, recurring, connections, imports"
```

---

### Task 21: End-to-end verification & docs

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Full local verification**

```bash
cd api && poetry run pytest -v                                  # all green
cd ../web && npm run build                                      # clean
cd .. && make start                                             # docker stack
sleep 20
curl -s localhost:8000/api/health                               # {"ok":true}
curl -s localhost:8000/api/dashboard | python3 -m json.tool | head -20   # zeros, no error
curl -s -X POST localhost:8000/api/transactions -H 'content-type: application/json' \
  -d '{"date":"2026-06-05","type":"expense","category":"Groceries","total":114.98,"merchant":"Metro"}'
curl -s localhost:8000/api/whatsapp/qr | head -c 60             # QR png data-uri
curl -s localhost:5173/ -o /dev/null -w '%{http_code}\n'        # 200
```

- [ ] **Step 2: Live chat smoke (needs CLAUDE_CODE_OAUTH_TOKEN in .env)**

```bash
SESSION=$(curl -s -X POST localhost:8000/api/chat/sessions | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
curl -sN -X POST localhost:8000/api/chat/sessions/$SESSION/messages \
  -F "message=I spent 45.99 total at Costco on groceries today" --max-time 120 | tail -5
curl -s "localhost:8000/api/transactions?period=" | python3 -m json.tool | head -30
# expect: Costco row with back-calculated GST/QST
```

- [ ] **Step 3: Update `README.md`** — replace the Architecture section: SQLite is the source of truth (`api/data/expense.db`); Google = optional one-way sync; new features list (quick-add, budgets, recurring, imports, tax profiles, chat sessions); same Makefile usage. Keep setup-token instructions.

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "docs: README for local-first architecture"
```

---

## Self-Review (completed)

1. **Spec coverage:** §4 decisions → Q1/2 (Tasks 1,6,7,15), Q3 (no auth — n/a), Q4/28 (Task 17), Q5 (Task 19), Q6 (Task 17 QuickAdd), Q7 (Tasks 1,5,20), Q8/8b (Tasks 3,6), Q9 (Tasks 4,16), Q10 (Task 16 theme), Q11 (Task 16 TopBar), Q12 (Task 18), Q13 (Tasks 6,17,18 Lightbox), Q14 (Task 12), Q15/16 (Task 14), Q17 (Tasks 5,7,17), Q18 (Task 9), Q19 (CAD only — no currency code anywhere), Q20 (desktop-only CSS), Q21 (Task 19 GenUI), Q22-25 (Task 13/20), Q26 (Tasks 2,10,15), Q27 (Tasks 10,19). No gaps.
2. **Placeholders:** Task 16 Step 5 stubs are explicitly replaced by Tasks 17-20 — intentional compile-bridge, not a gap. No TBDs.
3. **Type consistency:** `create_transaction(conn, data)` dict shape used identically in Tasks 6, 9, 11, 13; `dashboard_data(conn, period)` in 7, 11, 14; `ChatEvent`/`UiSpec` shared via api.ts; `_safe_reconcile`/`schedule_push` defined Task 12, referenced Tasks 6 (guarded import) and 15.
