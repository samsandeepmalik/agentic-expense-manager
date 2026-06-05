# Architecture Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the four moves from `docs/arch-review-2026-06-05.md`: close the sync-trigger loop, promote sync state out of the settings table, add an audit trail + better logging, and extract a channel protocol with an honest test seam.

**Architecture:** Sync becomes event-driven — services mark data dirty, a single debounced worker reconciles (thread-safe via `loop.call_soon_threadsafe`). Receipt links move from `settings` junk-drawer keys to a `transactions.receipt_link` column (with migration). A new `audit_log` table records every transaction write with its origin channel plus sync outcomes; surfaced via `GET /api/audit` and a Settings "Activity" section. Channels get a `BaseChannel`/`BaseChannelRegistry` contract in `channels/base.py`; `WhatsAppManager` gains an injectable `client_factory` so tests exercise the real `start()` path. Protected code (anthropic_provider, ocr.py, neonize event wiring bodies, `should_process`, money math) is untouched.

**Tech Stack:** Python 3.13 / FastAPI / stdlib sqlite3 / asyncio; React + TanStack Query.

**Order:** Move 1 (correctness bug) → Move 3 (sync state) → Move 4 (audit) → Move 2 (channel protocol, structural last).

---

## File Structure

```
api/app/
  db.py                    modify: busy_timeout pragma, WAL once in init_db,
                                   receipt_link migration, audit_log table
  settings_keys.py         new:    settings-table key constants
  services/
    sync.py                modify: request_sync()/sync_worker() debounced trigger,
                                   receipt_link column, last-error surfacing
    transactions.py        modify: call sync.request_sync() on every write,
                                   audit hook on create
    audit.py               new:    append-only audit log service
    google_client.py       modify: settings-only ids (constants), drop env dual-source
  routes/
    transactions.py        modify: drop _schedule_sync_push (service owns it)
    audit.py               new:    GET /api/audit
  channels/
    base.py                new:    BaseChannel + BaseChannelRegistry contract
    whatsapp.py            modify: implement base, client_factory injection
  main.py                  modify: sync worker task, channel list iteration,
                                   logging format
  config.py                modify: drop google_spreadsheet_id/google_drive_folder_id
api/tests/
  test_db.py               extend: pragma + migration tests
  test_sync.py             extend: request_sync triggers, worker coalesce, receipt column
  test_audit.py            new
  test_whatsapp_qr.py      extend: real start() via FakeClient factory
web/src/
  pages/Dashboard.tsx      modify: refetchInterval (WhatsApp-write staleness)
  pages/Transactions.tsx   modify: refetchInterval
  pages/Settings.tsx       modify: sync last-error + Activity section
  api.ts                   modify: AuditRow type
```

---

# MOVE 1 — Close the sync loop

### Task 1: SQLite pragmas (busy_timeout; WAL once)

**Files:**
- Modify: `api/app/db.py`
- Test: `api/tests/test_db.py`

- [x] **Step 1: Write failing test** (append to `api/tests/test_db.py`)

```python
def test_connection_pragmas(conn):
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
```

- [x] **Step 2: Run** — `cd api && poetry run pytest tests/test_db.py -v` → FAIL (busy_timeout 0)

- [x] **Step 3: Implement.** In `api/app/db.py` `get_db()`, replace the pragma lines:

```python
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
```

and in `init_db()`, first line inside `with get_db() as conn:` add the persistent setting:

```python
        conn.execute("PRAGMA journal_mode=WAL")
```

(WAL is a persistent DB-file property — set once at init, not per connection.)

- [x] **Step 4: Run** — `poetry run pytest tests/test_db.py -v` → PASS
- [x] **Step 5: Commit**

```bash
git add api/app/db.py api/tests/test_db.py
git commit -m "fix(db): busy_timeout for write contention; WAL set once at init"
```

### Task 2: Event-driven debounced sync trigger

**Files:**
- Modify: `api/app/services/sync.py` (replace `schedule_push`), `api/app/services/transactions.py`, `api/app/routes/transactions.py`, `api/app/main.py`
- Test: `api/tests/test_sync.py`

- [x] **Step 1: Write failing tests** (append to `api/tests/test_sync.py`)

```python
import asyncio

import pytest


def test_every_write_path_requests_sync(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(sync, "request_sync", lambda: calls.append(1))
    txn = txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 10.0})
    txn_svc.update_transaction(conn, txn["id"], {"total": 20.0})
    txn_svc.bulk_action(conn, [txn["id"]], "delete")
    extra = txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Dining",
        "total": 5.0})
    txn_svc.delete_transaction(conn, extra["id"])
    assert len(calls) == 5  # create, update, bulk, create, delete


@pytest.mark.asyncio
async def test_sync_worker_coalesces_bursts(db_path, monkeypatch):
    ran = []
    monkeypatch.setattr(sync, "_safe_reconcile", lambda: ran.append(1))
    monkeypatch.setattr(sync, "sync_enabled", lambda: True)
    worker = asyncio.create_task(sync.sync_worker(debounce=0.05))
    await asyncio.sleep(0.01)          # let worker install loop + event
    for _ in range(10):
        sync.request_sync()            # burst of writes
    await asyncio.sleep(0.2)
    worker.cancel()
    assert ran == [1]                  # one reconcile, not ten
```

- [x] **Step 2: Run** — `poetry run pytest tests/test_sync.py -v` → FAIL (`request_sync` missing)

- [x] **Step 3: Implement trigger in `api/app/services/sync.py`.** Replace the whole `schedule_push` function with:

```python
_loop: asyncio.AbstractEventLoop | None = None
_dirty: asyncio.Event | None = None


def request_sync() -> None:
    """Thread-safe dirty flag. Callable from any thread (agent tools run in
    worker threads); the worker coalesces bursts into one reconcile."""
    if _loop is None or _dirty is None:
        return  # worker not running (tests, scripts) — hourly reconcile covers it
    _loop.call_soon_threadsafe(_dirty.set)


async def sync_worker(debounce: float = 2.0) -> None:
    """Long-lived task: wait for dirty flag, debounce, reconcile once."""
    global _loop, _dirty
    _loop = asyncio.get_running_loop()
    _dirty = asyncio.Event()
    while True:
        await _dirty.wait()
        await asyncio.sleep(debounce)
        _dirty.clear()
        if sync_enabled():
            await asyncio.to_thread(_safe_reconcile)
```

- [x] **Step 4: Trigger from the service layer.** In `api/app/services/transactions.py` add at top with the other relative imports: `from . import sync as sync_svc` is **wrong** (circular at import time is safe — sync imports transactions lazily — but keep it lazy anyway for clarity). Use a tiny helper at module level:

```python
def _request_sync() -> None:
    from . import sync
    sync.request_sync()
```

Then add `_request_sync()` as the last line before `return`/end of: `create_transaction` (before `return _row_to_dict(...)` — capture row first), `update_transaction` (before final `return get_transaction(...)`), `delete_transaction`, and `bulk_action` (once, before each `return len(ids)`). Exact placements:

```python
# create_transaction — replace the tail:
    row = conn.execute("SELECT * FROM transactions WHERE id=?", (cursor.lastrowid,)).fetchone()
    result = _row_to_dict(conn, row)
    _request_sync()
    return result

# update_transaction — replace the tail:
    result = get_transaction(conn, txn_id)
    _request_sync()
    return result

# delete_transaction:
def delete_transaction(conn, txn_id: int) -> None:
    conn.execute("DELETE FROM transactions WHERE id=?", (txn_id,))
    _request_sync()

# bulk_action — add `_request_sync()` immediately before BOTH `return len(ids)` lines.
```

(Note: `bulk_action`'s recategorize path calls `update_transaction` per id, which also fires — the worker coalesces, so over-firing is harmless. The test counts 5 because bulk delete fires once.)

- [x] **Step 5: Route cleanup.** In `api/app/routes/transactions.py` delete the `_schedule_sync_push` function and its two call sites (`create_transaction`, `update_transaction` routes) — the service owns triggering now.

- [x] **Step 6: Start the worker.** In `api/app/main.py` lifespan, after `scheduler = asyncio.create_task(_scheduler_loop())` add:

```python
    from .services.sync import sync_worker
    sync_task = asyncio.create_task(sync_worker())
```

and change the teardown to:

```python
    yield
    scheduler.cancel()
    sync_task.cancel()
```

- [x] **Step 7: Run all** — `poetry run pytest -v` → PASS (existing `test_reconcile_pushes_once` unaffected)
- [x] **Step 8: Commit**

```bash
git add api/app/services/sync.py api/app/services/transactions.py api/app/routes/transactions.py api/app/main.py api/tests/test_sync.py
git commit -m "fix(sync): service-layer dirty flag + debounced worker closes agent/recurring gap"
```

### Task 3: Frontend staleness (WhatsApp writes never reach an open dashboard)

**Files:**
- Modify: `web/src/pages/Dashboard.tsx`, `web/src/pages/Transactions.tsx`

- [x] **Step 1: Dashboard.** In `web/src/pages/Dashboard.tsx`, change the query:

```tsx
  const { data, isLoading } = useQuery({
    queryKey: ["dashboard", period],
    refetchInterval: 15000,
    queryFn: () => get<DashboardData>(`/api/dashboard?period=${period}`),
  });
```

- [x] **Step 2: Transactions.** In `web/src/pages/Transactions.tsx`, change the list query:

```tsx
  const txns = useQuery({ queryKey: ["transactions", period, filters],
    refetchInterval: 15000,
    queryFn: () => get<Txn[]>(`/api/transactions?${query}`) });
```

- [x] **Step 3: Build** — `cd web && npm run build` → clean
- [x] **Step 4: Commit**

```bash
git add web/src/pages/Dashboard.tsx web/src/pages/Transactions.tsx
git commit -m "fix(web): poll dashboard/transactions so channel-originated writes appear"
```

---

# MOVE 3 — Promote sync state out of the settings junk drawer

### Task 4: Settings key constants

**Files:**
- Create: `api/app/settings_keys.py`
- Modify: `api/app/services/google_client.py`, `api/app/services/sync.py`, `api/app/routes/whatsapp.py`, `api/app/channels/whatsapp.py`, `api/app/services/vision.py`, `api/app/routes/settings.py`

- [x] **Step 1: Create `api/app/settings_keys.py`**

```python
"""Single registry of settings-table keys. Never inline these strings."""

GOOGLE_TOKENS = "google_tokens"
DRIVE_FOLDER_ID = "drive_folder_id"
SPREADSHEET_ID = "spreadsheet_id"
OCR_PROVIDER = "ocr_provider"
WHATSAPP_ALLOWED_SENDERS = "whatsapp_allowed_senders"
WHATSAPP_SUMMARY_CHAT = "whatsapp_summary_chat"
LAST_SYNC_ERROR = "last_sync_error"
```

- [x] **Step 2: Replace literals.** In each file import `from ..settings_keys import ...` (or `from .settings_keys import ...` relative to location) and swap:
  - `google_client.py`: `"google_tokens"` → `GOOGLE_TOKENS` (3 sites), `"drive_folder_id"` → `DRIVE_FOLDER_ID` (2 sites)
  - `sync.py`: `"spreadsheet_id"` → `SPREADSHEET_ID` (2 sites)
  - `routes/whatsapp.py`: `_ALLOWED_KEY = WHATSAPP_ALLOWED_SENDERS`
  - `channels/whatsapp.py`: `"whatsapp_allowed_senders"` → `WHATSAPP_ALLOWED_SENDERS`, `"whatsapp_summary_chat"` → `WHATSAPP_SUMMARY_CHAT` (2 sites — handler + registry summary)
  - `vision.py` + `routes/settings.py`: `"ocr_provider"` → `OCR_PROVIDER`

- [x] **Step 3: Run** — `poetry run pytest -v` → all PASS (pure rename)
- [x] **Step 4: Commit**

```bash
git add api/app/settings_keys.py api/app/services/ api/app/routes/ api/app/channels/
git commit -m "refactor(settings): central key constants, no scattered literals"
```

### Task 5: `receipt_link` becomes a transactions column (with migration)

**Files:**
- Modify: `api/app/db.py` (migration), `api/app/services/sync.py`
- Test: `api/tests/test_db.py`, `api/tests/test_sync.py`

- [x] **Step 1: Write failing tests.** Append to `api/tests/test_db.py`:

```python
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
```

Append to `api/tests/test_sync.py`:

```python
def test_receipt_upload_uses_column(conn, monkeypatch):
    txn = txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 9.0})
    conn.execute("UPDATE transactions SET receipt_link='https://drive/y' WHERE id=?",
                 (txn["id"],))
    fresh = dict(conn.execute("SELECT * FROM transactions WHERE id=?",
                              (txn["id"],)).fetchone())
    fresh["category"] = "Groceries"
    assert sync._maybe_upload_receipt(conn, fresh) == "https://drive/y"
```

- [x] **Step 2: Run** → FAIL (no column)

- [x] **Step 3: Implement migration in `api/app/db.py` `init_db()`** (after the seed blocks, inside the same `with get_db() as conn:`):

```python
        # Migration: receipt_link column (was settings junk-drawer keys)
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(transactions)")}
        if "receipt_link" not in columns:
            conn.execute("ALTER TABLE transactions ADD COLUMN receipt_link TEXT")
        legacy = conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE 'receipt_link_%'"
        ).fetchall()
        for row in legacy:
            txn_id = row["key"].removeprefix("receipt_link_")
            if txn_id.isdigit():
                conn.execute("UPDATE transactions SET receipt_link=? WHERE id=?",
                             (json.loads(row["value"]), int(txn_id)))
            conn.execute("DELETE FROM settings WHERE key=?", (row["key"],))
```

- [x] **Step 4: Use the column in `api/app/services/sync.py`.** Replace `_maybe_upload_receipt`:

```python
def _maybe_upload_receipt(conn, txn: dict) -> str:
    if txn.get("receipt_link"):
        return txn["receipt_link"]
    if not txn["image_path"] or not Path(txn["image_path"]).exists():
        return ""
    data = Path(txn["image_path"]).read_bytes()
    suffix = Path(txn["image_path"]).suffix.lstrip(".") or "jpg"
    link = gc.upload_receipt_image(Path(txn["image_path"]).name, data, f"image/{suffix}")
    conn.execute("UPDATE transactions SET receipt_link=? WHERE id=?",
                 (link, txn["id"]))
    return link
```

- [x] **Step 5: Run all** — `poetry run pytest -v` → PASS
- [x] **Step 6: Commit**

```bash
git add api/app/db.py api/app/services/sync.py api/tests/
git commit -m "refactor(sync): receipt_link is a transactions column; migrate junk-drawer keys"
```

### Task 6: One source of truth for Google ids

**Files:**
- Modify: `api/app/config.py`, `api/app/services/google_client.py`

- [x] **Step 1: Drop the env duplicates.** In `api/app/config.py` delete:

```python
    google_spreadsheet_id: str = os.getenv("GOOGLE_SPREADSHEET_ID", "")
    google_drive_folder_id: str = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
```

- [x] **Step 2: DB-authoritative folder id.** In `api/app/services/google_client.py` `ensure_drive_folder()` change the first lookup to:

```python
    folder_id = _read(DRIVE_FOLDER_ID)
```

- [x] **Step 3: Verify** — `poetry run pytest -v` and `grep -rn "google_spreadsheet_id\|google_drive_folder_id" api/app/ || echo CLEAN` → CLEAN
- [x] **Step 4: Commit**

```bash
git add api/app/config.py api/app/services/google_client.py
git commit -m "refactor(google): settings table is the only source for sheet/folder ids"
```

---

# MOVE 4 — Audit trail + logging

### Task 7: Audit service + table + transaction hook + sync outcomes

**Files:**
- Modify: `api/app/db.py` (table), `api/app/services/transactions.py`, `api/app/services/sync.py`
- Create: `api/app/services/audit.py`
- Test: `api/tests/test_audit.py`

- [x] **Step 1: Write failing tests** — `api/tests/test_audit.py`:

```python
from app.services import audit, transactions as txn_svc


def test_transaction_writes_are_audited(conn):
    txn = txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 11.0, "source": "whatsapp"})
    rows = audit.recent(conn)
    assert rows[0]["event"] == "transaction_created"
    assert rows[0]["channel"] == "whatsapp"
    assert rows[0]["ref"] == str(txn["id"])

    txn_svc.delete_transaction(conn, txn["id"])
    assert audit.recent(conn)[0]["event"] == "transaction_deleted"


def test_sync_failure_recorded(conn, db_path, monkeypatch):
    from app.services import sync

    def boom():
        raise RuntimeError("sheet quota")
    monkeypatch.setattr(sync, "reconcile", boom)
    sync._safe_reconcile()
    rows = audit.recent(conn)
    assert rows[0]["event"] == "sync_failed"
    assert "sheet quota" in rows[0]["detail"]
    assert "sheet quota" in (sync.status().get("last_error") or "")
```

- [x] **Step 2: Run** → FAIL (no `app.services.audit`)

- [x] **Step 3: Table.** In `api/app/db.py` `SCHEMA`, append before the closing `"""`:

```sql
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL DEFAULT (datetime('now')),
  channel TEXT NOT NULL DEFAULT '',
  event TEXT NOT NULL,
  ref TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT ''
);
```

- [x] **Step 4: Create `api/app/services/audit.py`**

```python
"""Append-only audit log: which channel did what to which record."""

from __future__ import annotations

import sqlite3


def record(conn: sqlite3.Connection, event: str, *, channel: str = "",
           ref: str = "", detail: str = "") -> None:
    conn.execute(
        "INSERT INTO audit_log(channel, event, ref, detail) VALUES (?,?,?,?)",
        (channel, event, ref, detail[:1000]),
    )


def recent(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))]
```

- [x] **Step 5: Hook transaction writes.** In `api/app/services/transactions.py` add import `from . import audit` and:

```python
# create_transaction tail becomes:
    row = conn.execute("SELECT * FROM transactions WHERE id=?", (cursor.lastrowid,)).fetchone()
    result = _row_to_dict(conn, row)
    audit.record(conn, "transaction_created", channel=result["source"],
                 ref=str(result["id"]),
                 detail=f"{result['date']} {result['merchant']} ${result['total']}")
    _request_sync()
    return result

# update_transaction tail becomes:
    result = get_transaction(conn, txn_id)
    audit.record(conn, "transaction_updated", channel=result["source"],
                 ref=str(txn_id), detail=f"total ${result['total']}")
    _request_sync()
    return result

# delete_transaction becomes:
def delete_transaction(conn, txn_id: int) -> None:
    conn.execute("DELETE FROM transactions WHERE id=?", (txn_id,))
    audit.record(conn, "transaction_deleted", ref=str(txn_id))
    _request_sync()
```

- [x] **Step 6: Sync outcomes.** In `api/app/services/sync.py` replace `_safe_reconcile` and extend `status()`:

```python
def _safe_reconcile() -> None:
    from .audit import record
    try:
        result = reconcile()
        with get_db() as conn:
            set_setting(conn, LAST_SYNC_ERROR, None)
            if result.get("synced"):
                record(conn, "sync_pushed", channel="sync",
                       detail=f"{result['synced']} rows")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Sync push failed; will retry on next reconcile")
        with get_db() as conn:
            set_setting(conn, LAST_SYNC_ERROR, str(exc))
            record(conn, "sync_failed", channel="sync", detail=str(exc))
```

```python
# status() return becomes:
    return {"enabled": sync_enabled(), "pending": pending,
            "last_error": last_error,
            "sheet_url": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
                         if spreadsheet_id else None}
# with, inside the `with get_db() as conn:` block above it:
        last_error = get_setting(conn, LAST_SYNC_ERROR)
```

(import `LAST_SYNC_ERROR` from `..settings_keys`.)

- [x] **Step 7: Run all** — `poetry run pytest -v` → PASS
- [x] **Step 8: Commit**

```bash
git add api/app/db.py api/app/services/audit.py api/app/services/transactions.py api/app/services/sync.py api/tests/test_audit.py
git commit -m "feat(audit): append-only audit log for writes and sync outcomes"
```

### Task 8: Audit API + Settings Activity section + log format

**Files:**
- Create: `api/app/routes/audit.py`
- Modify: `api/app/main.py`, `web/src/api.ts`, `web/src/pages/Settings.tsx`
- Test: `api/tests/test_audit.py` (extend)

- [x] **Step 1: Failing route test** (append to `api/tests/test_audit.py`):

```python
def test_audit_api(conn, db_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.errors import register_error_handler
    from app.routes import audit as audit_routes

    audit.record(conn, "transaction_created", channel="ui", ref="1")
    conn.commit()
    app = FastAPI()
    register_error_handler(app)
    app.include_router(audit_routes.router)
    client = TestClient(app, raise_server_exceptions=False)
    rows = client.get("/api/audit").json()
    assert rows[0]["event"] == "transaction_created"
```

- [x] **Step 2: Run** → FAIL

- [x] **Step 3: Create `api/app/routes/audit.py`**

```python
"""Read-only activity feed."""

from __future__ import annotations

from fastapi import APIRouter

from ..db import get_db
from ..services import audit as svc

router = APIRouter()


@router.get("/api/audit")
async def recent(limit: int = 100):
    with get_db() as conn:
        return svc.recent(conn, min(limit, 500))
```

- [x] **Step 4: Register + logging format.** In `api/app/main.py`: add `audit` to BOTH the `from .routes import (...)` list and the `for module in (...)` router loop. Replace the logging setup line with:

```python
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
```

- [x] **Step 5: Frontend.** In `web/src/api.ts` add:

```typescript
export interface AuditRow { id: number; ts: string; channel: string;
  event: string; ref: string; detail: string; }
```

In `web/src/pages/Settings.tsx`:
- import `type AuditRow` in the existing api import;
- with the other queries add:

```tsx
  const activity = useQuery({ queryKey: ["audit"], refetchInterval: 10000,
    queryFn: () => get<AuditRow[]>("/api/audit?limit=50") });
```

- in the Google sync section, after the connected `<p>…</p>` line add error surfacing (inside the connected branch, after the `</p>`):

```tsx
            {google.data.connected && (google.data as { last_error?: string }).last_error && (
              <p style={{ color: "var(--amber)" }}>
                Last sync error: {(google.data as { last_error?: string }).last_error}</p>)}
```

- before the final `</div>` of the page add:

```tsx
      <Section title="Activity">
        {(activity.data ?? []).length === 0 &&
          <p className="muted">No activity yet.</p>}
        <table>
          <tbody>
            {(activity.data ?? []).map((a) => (
              <tr key={a.id}>
                <td className="muted" style={{ whiteSpace: "nowrap" }}>{a.ts}</td>
                <td><span className="tag income">{a.channel || "—"}</span></td>
                <td>{a.event}</td>
                <td className="muted">{a.detail}{a.ref ? ` (#${a.ref})` : ""}</td>
              </tr>))}
          </tbody>
        </table>
      </Section>
```

- [x] **Step 6: Verify** — `poetry run pytest -v` PASS; `cd web && npm run build` clean
- [x] **Step 7: Commit**

```bash
git add api/app/routes/audit.py api/app/main.py api/tests/test_audit.py web/src/
git commit -m "feat(audit): /api/audit + Settings activity feed + sync error surfacing"
```

---

# MOVE 2 — Channel protocol + honest test seam

### Task 9: `channels/base.py` + client_factory injection

**Files:**
- Create: `api/app/channels/base.py`
- Modify: `api/app/channels/whatsapp.py`
- Test: `api/tests/test_whatsapp_qr.py` (extend)

- [ ] **Step 1: Failing tests** (append to `api/tests/test_whatsapp_qr.py`):

```python
class FakeEventBus:
    def __init__(self):
        self.qr_callback = None
        self.handlers = {}

    def qr(self, callback):
        self.qr_callback = callback

    def __call__(self, event_type):
        def decorate(fn):
            self.handlers[event_type] = fn
            return fn
        return decorate


class FakeClient:
    def __init__(self):
        self.event = FakeEventBus()
        self.disconnected = False

    async def connect(self):
        return None

    async def disconnect(self):
        self.disconnected = True

    async def logout(self):
        return None

    async def get_me(self):
        raise RuntimeError("no identity in fake")


@pytest.mark.asyncio
async def test_real_start_path_with_injected_client(db_path):
    fake = FakeClient()
    manager = WhatsAppManager("t1", client_factory=lambda db: fake)
    await manager.start()
    assert fake.event.qr_callback is not None          # QR wiring registered
    await fake.event.qr_callback(fake, b"qr-code-1")   # simulate neonize event
    assert manager.current_qr()["status"] == "qr"

    from neonize.events import ConnectedEv
    await fake.event.handlers[ConnectedEv](fake, None)
    assert manager.status == "connected"

    await manager.stop()
    assert fake.disconnected and manager.status == "disconnected"


def test_whatsapp_registry_is_a_channel_registry():
    from app.channels.base import BaseChannelRegistry
    from app.channels.whatsapp import WhatsAppRegistry
    assert issubclass(WhatsAppRegistry, BaseChannelRegistry)
```

- [ ] **Step 2: Run** → FAIL (`client_factory` unexpected kwarg; no `channels.base`)

- [ ] **Step 3: Create `api/app/channels/base.py`**

```python
"""Channel contract: what main.py and the scheduler may rely on.

A channel registry owns N account connections for one transport (WhatsApp,
Telegram, ...). main.py must never import transport-specific names — only
this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

# handler(chat_id, text, image_bytes, image_mime) -> reply text
MessageHandler = Callable[[str, str, bytes | None, str | None], Awaitable[str]]


class BaseChannelRegistry(ABC):
    """All paired accounts for one transport."""

    name: str = "channel"

    @abstractmethod
    def set_handler(self, handler: MessageHandler) -> None: ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    def list_accounts(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def send_weekly_summary(self) -> None: ...
```

- [ ] **Step 4: Implement in `api/app/channels/whatsapp.py`:**
  - Replace the local `MessageHandler` definition with `from .base import BaseChannelRegistry, MessageHandler` (delete the local `MessageHandler = ...` line and the now-unused `Awaitable, Callable` names from the `collections.abc` import).
  - `WhatsAppManager.__init__` signature becomes:

```python
    def __init__(self, account_id: str = "default",
                 db_path: Path | None = None,
                 client_factory: Callable[[str], Any] | None = None) -> None:
```

(keep `Callable` in the `collections.abc` import for this), store `self._client_factory = client_factory`, and in `start()` replace `client = NewAClient(str(self._db_path))` with:

```python
        if self._client_factory is not None:
            client = self._client_factory(str(self._db_path))
        else:
            client = NewAClient(str(self._db_path))
```

(the neonize imports stay exactly where they are; event-wiring bodies untouched).
  - Class header: `class WhatsAppRegistry(BaseChannelRegistry):` and add `name = "whatsapp"` as first class attribute.
  - `WhatsAppRegistry._add` passes nothing new (managers it creates use the real client).

- [ ] **Step 5: Run** — `poetry run pytest tests/test_whatsapp_qr.py -v` → PASS (all old tests + 2 new)
- [ ] **Step 6: Commit**

```bash
git add api/app/channels/base.py api/app/channels/whatsapp.py api/tests/test_whatsapp_qr.py
git commit -m "feat(channels): BaseChannelRegistry contract + injectable client factory"
```

### Task 10: main.py speaks to channels, not WhatsApp

**Files:**
- Modify: `api/app/main.py`

- [ ] **Step 1: Generalize.** In `api/app/main.py`:
  - rename `_handle_whatsapp_message` → `_handle_channel_message` (same body; the `wa:` session prefix becomes channel-aware):

```python
async def _handle_channel_message(chat_id, text, image_bytes, image_mime):
    session = sessions.get(f"wa:{chat_id}", channel="whatsapp")
    ...
```

  stays as-is internally for now (WhatsApp is the only transport; the session
  key scheme is revisited when a second transport lands).
  - introduce the channel list and iterate it everywhere WhatsApp was named:

```python
from .channels.base import BaseChannelRegistry
from .channels.whatsapp import whatsapp

CHANNELS: list[BaseChannelRegistry] = [whatsapp]
```

  - lifespan:

```python
    for channel in CHANNELS:
        channel.set_handler(_handle_channel_message)
        try:
            await channel.start()
        except Exception:  # noqa: BLE001
            logger.exception("%s channel failed to start", channel.name)
```

  - scheduler summary line becomes:

```python
                for channel in CHANNELS:
                    await channel.send_weekly_summary()
```

- [ ] **Step 2: Verify** — `poetry run pytest -v` PASS; `poetry run python -c "from app.main import app; print('BOOT_OK')"` → BOOT_OK; `grep -n "whatsapp\." api/app/main.py | grep -v whatsapp_routes || echo NO_DIRECT_CALLS` → NO_DIRECT_CALLS
- [ ] **Step 3: Commit**

```bash
git add api/app/main.py
git commit -m "refactor(main): channel-list wiring; scheduler iterates all channels"
```

---

### Task 11: End-to-end verification & docs

**Files:**
- Modify: `README.md`, `docs/arch-review-2026-06-05.md`

- [ ] **Step 1: Full verification**

```bash
cd api && poetry run pytest -v            # all green (expect ~50)
cd ../web && npm run build                # clean
cd .. && make start && sleep 15
curl -s localhost:8000/api/health                          # {"ok":true}
curl -s localhost:8000/api/audit | python3 -m json.tool | head   # rows or []
curl -s -X POST localhost:8000/api/transactions -H 'content-type: application/json' \
  -d '{"date":"2026-06-05","type":"expense","category":"Other","total":1.15}' >/dev/null
curl -s localhost:8000/api/audit | python3 -c "import json,sys; rows=json.load(sys.stdin); assert rows[0]['event']=='transaction_created'; print('AUDIT_OK')"
curl -s localhost:8000/api/sync/status | python3 -m json.tool    # has last_error field
```

- [ ] **Step 2: README.** In the API surface table add:

```markdown
| `GET /api/audit` | activity feed (writes + sync outcomes) |
```

and in Notes add: `- Every transaction write and sync outcome lands in the audit log (Settings → Activity).`

- [ ] **Step 3: Mark review items done.** Append to `docs/arch-review-2026-06-05.md`:

```markdown

---
## Status (2026-06-05, post-hardening)
All four roadmap moves implemented: sync trigger closed (service-layer dirty flag + debounced worker, busy_timeout), receipt_link promoted to transactions column with settings migration, audit_log + /api/audit + Settings activity feed + sync error surfacing, BaseChannelRegistry contract with injectable client factory and channel-agnostic main.py.
```

- [ ] **Step 4: Final commit**

```bash
git add README.md docs/arch-review-2026-06-05.md
git commit -m "docs: architecture hardening complete"
```

---

## Self-Review (completed)

1. **Coverage vs review roadmap:** Move 1 → Tasks 1–3 (busy_timeout, request_sync on all four write paths incl. bulk, debounced worker, route cleanup, frontend polling). Move 3 → Tasks 4–6 (constants, receipt_link column + migration + sync usage, single-source Google ids). Move 4 → Tasks 7–8 (audit table/service/hooks, sync failure surfacing via LAST_SYNC_ERROR + status().last_error, /api/audit, Activity UI, log format). Move 2 → Tasks 9–10 (base contract, client_factory seam exercising real start(), channel-agnostic main). No gaps.
2. **Placeholders:** none — every step has full code/commands.
3. **Type consistency:** `request_sync()`/`sync_worker(debounce)` used identically in Tasks 2 and 11; `audit.record(conn, event, channel=, ref=, detail=)` matches Tasks 7/8 usage; `BaseChannelRegistry` methods match the four call sites in main.py (Task 10); `client_factory(db_path:str)` matches FakeClient test (Task 9). `LAST_SYNC_ERROR` defined Task 4, used Task 7.
4. **Protected code:** neonize event-wiring bodies, anthropic_provider, ocr.py, should_process, money math — no task touches their behavior (Task 9 only wraps client construction above the wiring).
