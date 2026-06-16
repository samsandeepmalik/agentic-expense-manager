# Chat Statement Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a web-chat user drop a CSV/XLSX/PDF statement; the agent parses it, proposes/organizes categories (confirm gate 1), maps rows, and records the batch (confirm gate 2) — reusing the existing import pipeline with rows kept server-side.

**Architecture:** New service functions on `services/imports.py` (`_persist_import`, `import_summary`, `remap_import`, `classify_and_start`) wrap the existing parse/dedup/approve machinery. Three new agent tools (`get_import_summary`, `remap_import`, `approve_import`) operate by `import_id` only. The chat route accepts a generic file and routes statements to the agent with the `import_id` injected. WhatsApp is out of scope (v1).

**Tech Stack:** FastAPI, SQLite, pi-agent (Claude tool-calling), pytest, React/Vite/TypeScript.

**Spec:** `docs/design/2026-06-15-chat-statement-import.md`

---

## File Structure

- `api/app/services/imports.py` — **modify**: extract `_persist_import`; add `import_summary`, `remap_import`, `classify_and_start`.
- `api/app/db.py` — **modify**: idempotent migration for `imports.channel`; add `channel` to `imports` in `SCHEMA`.
- `api/app/agent/tools.py` — **modify**: 3 schemas + 3 executors + registration.
- `api/app/agent/prompts.py` — **modify**: statement-import instructions + two gates.
- `api/app/routes/chat.py` — **modify**: generic `file` param + statement routing.
- `web/src/api.ts` — **modify**: `streamChat` takes a generic `file`.
- `web/src/components/ChatThread.tsx` — **modify**: widen `accept`, generic file state.
- `api/tests/test_imports.py` — **modify/create**: service tests.
- `api/tests/test_chat_import.py` — **create**: chat-route routing tests.
- `api/tests/test_legacy_migration.py` — **modify**: `imports.channel` upgrade case.
- `api/tests/test_agent_tools.py` — **modify/create**: tool registration + behavior.

---

## Phase 1 — Service spine: persist + summary

### Task 1: Extract `_persist_import` from `start_import`

**Files:**
- Modify: `api/app/services/imports.py`
- Test: `api/tests/test_imports.py`

- [ ] **Step 1: Write the failing test**

```python
# api/tests/test_imports.py
import json
from app.services import imports as svc


def test_persist_import_stores_rows_and_flags_duplicates(conn):
    rows = [
        {"date": "2026-05-01", "type": "expense", "category": "Groceries",
         "merchant": "Metro", "total": 50.0},
        {"date": "2026-05-02", "type": "expense", "category": "Groceries",
         "merchant": "Costco", "total": 20.0},
    ]
    import_id = svc._persist_import(conn, "statement.csv", rows, profile_id=1,
                                    channel="chat")
    record = svc.get_import(import_id)
    assert record["status"] == "review"
    assert len(record["rows"]) == 2
    assert record["channel"] == "chat"
    assert all("duplicate" in r and "skip" in r for r in record["rows"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd api && poetry run pytest tests/test_imports.py::test_persist_import_stores_rows_and_flags_duplicates -v`
Expected: FAIL — `AttributeError: module 'app.services.imports' has no attribute '_persist_import'` (and `channel` column missing — that is Task 9; for now the test will fail on the attribute first).

> NOTE: This task depends on the `imports.channel` column (Task 9). If running strictly in order, do Task 9 first. The subagent runner may reorder; the plan lists migration as Phase 4 to keep DB tasks together, but `_persist_import` writes `channel`. **Do Task 9 before this task.**

- [ ] **Step 3: Refactor `start_import` to use a new `_persist_import` helper**

Replace the body between parsing and the audit call in `start_import` so the persistence is shared. New helper + updated `start_import`:

```python
def _persist_import(conn, filename: str, rows: list[dict], profile_id: int,
                    channel: str = "import") -> int:
    """Persist parsed rows as a review-ready import. Flags duplicates and
    initialises per-row skip. Returns the new import id."""
    flags = dedup_svc.flag_duplicates(conn, rows, profile_id=profile_id)
    for row, flag in zip(rows, flags):
        row["duplicate"] = flag
        row["skip"] = flag
    cursor = conn.execute(
        "INSERT INTO imports(filename, profile_id, channel, status, rows) "
        "VALUES (?,?,?,'review',?)",
        (filename, profile_id, channel, json.dumps(rows)))
    import_id = cursor.lastrowid
    audit.record(conn, "import_uploaded", channel=channel, ref=str(import_id),
                 detail=f"{filename}: {len(rows)} rows parsed",
                 profile_id=profile_id)
    return import_id
```

Then rewrite `start_import`'s success branch to call it (keep the upfront INSERT only for the failed-state bookkeeping, or simplify — see Step 3b).

- [ ] **Step 3b: Simplify `start_import` to use the helper**

```python
async def start_import(filename: str, data: bytes,
                       profile_id: int | None = None,
                       channel: str = "import") -> dict:
    text = extract_text(filename, data)
    with get_db() as conn:
        if profile_id is None:
            profile_id = prof_svc.active_id(conn)
        else:
            prof_svc.get_profile(conn, profile_id)
    try:
        rows = await parse_with_agent(text, profile_id)
        with get_db() as conn:
            import_id = _persist_import(conn, filename, rows, profile_id, channel)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Import parse failed")
        with get_db() as conn:
            cursor = conn.execute(
                "INSERT INTO imports(filename, profile_id, channel, status, error, rows) "
                "VALUES (?,?,?,'failed',?,?)",
                (filename, profile_id, channel, str(exc), json.dumps([])))
            import_id = cursor.lastrowid
    return get_import(import_id)
```

(Adjust `get_import` to JSON-decode `rows` safely when empty — it already does `json.loads`; ensure the failed branch stores `"[]"`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd api && poetry run pytest tests/test_imports.py::test_persist_import_stores_rows_and_flags_duplicates -v`
Expected: PASS

- [ ] **Step 5: Run the existing import suite to confirm no regression**

Run: `cd api && poetry run pytest tests/test_imports.py -v` (and `tests/test_legacy_migration.py`)
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add api/app/services/imports.py api/tests/test_imports.py
git commit -m "refactor(api): extract _persist_import; carry import channel"
```

---

### Task 2: `import_summary` service

**Files:**
- Modify: `api/app/services/imports.py`
- Test: `api/tests/test_imports.py`

- [ ] **Step 1: Write the failing test**

```python
def test_import_summary_buckets_and_unresolved(conn):
    rows = [
        {"date": "2026-05-01", "type": "expense", "category": "Groceries",
         "merchant": "Metro", "total": 50.0},
        {"date": "2026-05-02", "type": "expense", "category": "Nonsense Cat",
         "merchant": "UBER *EATS", "total": 24.1},
    ]
    import_id = svc._persist_import(conn, "s.csv", rows, 1, "chat")
    summary = svc.import_summary(conn, import_id)
    assert summary["total_rows"] == 2
    assert summary["to_record"] == 2  # no dups in a fresh db
    labels = {c["label"]: c for c in summary["parsed_categories"]}
    assert labels["Groceries"]["resolved_category_id"] is not None
    assert any(u["merchant"] == "UBER *EATS" for u in summary["unresolved"])
    assert len(summary["sample"]) <= 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd api && poetry run pytest tests/test_imports.py::test_import_summary_buckets_and_unresolved -v`
Expected: FAIL — `AttributeError: ... 'import_summary'`

- [ ] **Step 3: Implement `import_summary`**

```python
from . import categories as cat_svc  # add to imports at top of file

def _resolve_label(conn, row: dict, pid: int):
    """Best-effort category id for a parsed row; None if unresolved/ambiguous."""
    if row.get("category_id"):
        return int(row["category_id"])
    name = (row.get("category") or "").strip()
    if not name:
        return None
    try:
        cat = cat_svc.find_category_by_name(conn, name, profile_id=pid)
    except AppError:        # ambiguous_category
        return None
    return cat["id"] if cat else None


def import_summary(conn, import_id: int, *, sample_cap: int = 10,
                   unresolved_cap: int = 15) -> dict:
    record = get_import(import_id)
    pid = record["profile_id"]
    rows = record["rows"]
    buckets: dict[str, dict] = {}
    unresolved: list[dict] = []
    duplicates = 0
    for index, row in enumerate(rows):
        if row.get("duplicate"):
            duplicates += 1
        label = (row.get("category") or "(none)").strip() or "(none)"
        resolved = _resolve_label(conn, row, pid)
        bucket = buckets.setdefault(
            label, {"label": label, "count": 0, "resolved_category_id": resolved})
        bucket["count"] += 1
        if resolved is None and len(unresolved) < unresolved_cap:
            unresolved.append({"index": index,
                               "merchant": row.get("merchant", ""),
                               "total": row.get("total"),
                               "guessed": label})
    return {
        "import_id": import_id,
        "profile": prof_svc.get_profile(conn, pid)["name"],
        "total_rows": len(rows),
        "duplicates": duplicates,
        "to_record": sum(1 for r in rows if not r.get("skip")),
        "parsed_categories": list(buckets.values()),
        "unresolved": unresolved,
        "sample": [{"date": r.get("date"), "merchant": r.get("merchant"),
                    "total": r.get("total"), "category": r.get("category")}
                   for r in rows[:sample_cap]],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd api && poetry run pytest tests/test_imports.py::test_import_summary_buckets_and_unresolved -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/app/services/imports.py api/tests/test_imports.py
git commit -m "feat(api): import_summary — counts, category buckets, unresolved rows"
```

---

## Phase 2 — Remapping

### Task 3: `remap_import` service

**Files:**
- Modify: `api/app/services/imports.py`
- Test: `api/tests/test_imports.py`

- [ ] **Step 1: Write the failing test**

```python
from app.services import categories as cat_svc


def test_remap_import_applies_mapping_redups_idempotent(conn):
    rideshare = cat_svc.upsert_category(conn, "Rideshare", "expense", 100, True, None)
    rows = [
        {"date": "2026-05-02", "type": "expense", "category": "Nonsense",
         "merchant": "UBER *EATS 800", "total": 24.1},
        {"date": "2026-05-03", "type": "expense", "category": "Nonsense",
         "merchant": "LYFT RIDE", "total": 12.0},
    ]
    import_id = svc._persist_import(conn, "s.csv", rows, 1, "chat")
    result = svc.remap_import(conn, import_id, [
        {"match": {"contains": "UBER"}, "category_id": rideshare["id"]},
        {"match": {"index": 1}, "category_id": rideshare["id"]},
    ])
    assert result["unresolved"] == []
    stored = svc.get_import(import_id)["rows"]
    assert all(r["category_id"] == rideshare["id"] for r in stored)
    # idempotent: re-applying the same mapping changes nothing
    again = svc.remap_import(conn, import_id, [
        {"match": {"contains": "UBER"}, "category_id": rideshare["id"]},
    ])
    assert again["total_rows"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd api && poetry run pytest tests/test_imports.py::test_remap_import_applies_mapping_redups_idempotent -v`
Expected: FAIL — `AttributeError: ... 'remap_import'`

- [ ] **Step 3: Implement `remap_import`**

```python
def _matches(rule_match: dict, index: int, row: dict) -> bool:
    if "index" in rule_match:
        return index == int(rule_match["index"])
    merchant = (row.get("merchant") or "")
    if "merchant" in rule_match:
        return merchant.strip().lower() == str(rule_match["merchant"]).strip().lower()
    if "contains" in rule_match:
        return str(rule_match["contains"]).lower() in merchant.lower()
    return False


def remap_import(conn, import_id: int, mapping: list[dict]) -> dict:
    record = get_import(import_id)
    pid = record["profile_id"]
    rows = record["rows"]
    for index, row in enumerate(rows):
        for rule in mapping:
            if _matches(rule.get("match", {}), index, row):
                cat = cat_svc.get_category(conn, int(rule["category_id"]))
                if cat["profile_id"] != pid:
                    raise AppError("category_not_found", "Unknown category", 404)
                row["category_id"] = cat["id"]
                row["category"] = cat["name"]
                break
    flags = dedup_svc.flag_duplicates(conn, rows, profile_id=pid)
    for row, flag in zip(rows, flags):
        row["duplicate"] = flag
        row["skip"] = flag
    conn.execute("UPDATE imports SET rows=? WHERE id=?",
                 (json.dumps(rows), import_id))
    return import_summary(conn, import_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd api && poetry run pytest tests/test_imports.py::test_remap_import_applies_mapping_redups_idempotent -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/app/services/imports.py api/tests/test_imports.py
git commit -m "feat(api): remap_import — deterministic row->category mapping + re-dedup"
```

---

## Phase 3 — File classification

### Task 4: `classify_and_start`

**Files:**
- Modify: `api/app/services/imports.py`
- Test: `api/tests/test_imports.py`

- [ ] **Step 1: Write the failing test**

```python
import asyncio


def test_classify_and_start_csv_is_statement(conn, monkeypatch):
    async def fake_parse(text, profile_id):
        return [{"date": "2026-05-01", "type": "expense", "category": "Groceries",
                 "merchant": "Metro", "total": 5.0}]
    monkeypatch.setattr(svc, "parse_with_agent", fake_parse)
    result = asyncio.run(svc.classify_and_start("bank.csv", b"a,b\n1,2\n", 1))
    assert result["kind"] == "statement"
    assert result["import_id"] is not None


def test_classify_and_start_scanned_pdf_is_receipt(conn, monkeypatch):
    monkeypatch.setattr(svc, "extract_text", lambda f, d: "x")  # < 40 chars
    result = asyncio.run(svc.classify_and_start("scan.pdf", b"%PDF", 1))
    assert result["kind"] == "receipt"
```

> The CSV test monkeypatches `parse_with_agent` (no live LLM). The PDF test monkeypatches `extract_text` to simulate an unreadable scan.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd api && poetry run pytest tests/test_imports.py::test_classify_and_start_csv_is_statement tests/test_imports.py::test_classify_and_start_scanned_pdf_is_receipt -v`
Expected: FAIL — `AttributeError: ... 'classify_and_start'`

- [ ] **Step 3: Implement `classify_and_start`**

```python
def _is_spreadsheet(filename: str) -> bool:
    return filename.lower().endswith((".csv", ".xlsx", ".xls"))


async def classify_and_start(filename: str, data: bytes,
                             profile_id: int | None = None) -> dict:
    """Decide receipt vs statement for a chat upload.

    Returns {"kind": "statement", "import_id": int}
         or {"kind": "receipt", "import_id": None}
         or {"kind": "failed", "import_id": int, "error": str}.
    CSV/XLSX are always statements. PDFs: scanned (unreadable text) -> receipt;
    text PDFs that parse to >=2 rows -> statement, else receipt.
    """
    with get_db() as conn:
        pid = profile_id if profile_id is not None else prof_svc.active_id(conn)

    if _is_spreadsheet(filename):
        record = await start_import(filename, data, pid, channel="chat")
        kind = "failed" if record["status"] == "failed" else "statement"
        return {"kind": kind, "import_id": record["id"],
                "error": record.get("error")}

    if filename.lower().endswith(".pdf"):
        try:
            text = extract_text(filename, data)        # raises pdf_unreadable on scans
        except AppError:
            return {"kind": "receipt", "import_id": None}
        rows = await parse_with_agent(text, pid)
        if len(rows) >= 2:
            with get_db() as conn:
                import_id = _persist_import(conn, filename, rows, pid, "chat")
            return {"kind": "statement", "import_id": import_id}
        return {"kind": "receipt", "import_id": None}

    return {"kind": "receipt", "import_id": None}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd api && poetry run pytest tests/test_imports.py -k classify -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/app/services/imports.py api/tests/test_imports.py
git commit -m "feat(api): classify_and_start — route chat uploads to receipt vs statement"
```

---

## Phase 4 — Schema migration

### Task 9: `imports.channel` column + migration

> Numbered 9 for clarity but **must run before Task 1** (Task 1 writes `channel`).

**Files:**
- Modify: `api/app/db.py`
- Test: `api/tests/test_legacy_migration.py`

- [ ] **Step 1: Write the failing test**

```python
# api/tests/test_legacy_migration.py  (add)
def test_imports_channel_migration(tmp_path):
    import sqlite3
    from app import db
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE imports(id INTEGER PRIMARY KEY, filename TEXT, "
        "profile_id INTEGER NOT NULL DEFAULT 1, status TEXT DEFAULT 'review', "
        "error TEXT, rows TEXT DEFAULT '[]');")
    conn.commit(); conn.close()
    db.init_db(str(path))   # runs idempotent migrations
    conn = sqlite3.connect(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(imports)")}
    assert "channel" in cols
```

(Match the real `init_db` signature/usage in this repo; if `init_db` takes no path arg, set `DATA_DIR`/use the existing legacy-test pattern in this file.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd api && poetry run pytest tests/test_legacy_migration.py::test_imports_channel_migration -v`
Expected: FAIL — `assert 'channel' in cols`

- [ ] **Step 3: Add column to `SCHEMA` and an idempotent migration**

In `db.py` `SCHEMA`, add to the `imports` table definition: `channel TEXT DEFAULT 'import'`.

In the `init_db` migration block:

```python
cols = {r[1] for r in conn.execute("PRAGMA table_info(imports)")}
if "channel" not in cols:
    conn.execute("ALTER TABLE imports ADD COLUMN channel TEXT DEFAULT 'import'")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd api && poetry run pytest tests/test_legacy_migration.py::test_imports_channel_migration -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/app/db.py api/tests/test_legacy_migration.py
git commit -m "feat(api): imports.channel column + idempotent migration"
```

---

## Phase 5 — Agent tools

### Task 5: Tool schemas + executors + registration

**Files:**
- Modify: `api/app/agent/tools.py`
- Test: `api/tests/test_agent_tools.py`

- [ ] **Step 1: Write the failing test**

```python
# api/tests/test_agent_tools.py
from app.agent.tools import build_tools


def test_import_tools_registered():
    names = {t.name for t in build_tools("ui", lambda spec: None, "ui")}
    assert {"get_import_summary", "remap_import", "approve_import"} <= names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd api && poetry run pytest tests/test_agent_tools.py::test_import_tools_registered -v`
Expected: FAIL — set is not a subset

- [ ] **Step 3: Add schemas (near the other *_SCHEMA constants)**

```python
GET_IMPORT_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {"import_id": {"type": "integer"}},
    "required": ["import_id"],
}

REMAP_IMPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "import_id": {"type": "integer"},
        "mapping": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "match": {
                        "type": "object",
                        "properties": {
                            "merchant": {"type": "string"},
                            "contains": {"type": "string"},
                            "index": {"type": "integer"},
                        },
                    },
                    "category_id": {"type": "integer"},
                },
                "required": ["match", "category_id"],
            },
        },
    },
    "required": ["import_id", "mapping"],
}

APPROVE_IMPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "import_id": {"type": "integer"},
        "indexes": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["import_id"],
}
```

- [ ] **Step 4: Add executors inside `build_tools` (alongside the others)**

```python
    async def get_import_summary(tool_call_id, params, abort_event=None, on_update=None):
        try:
            def work():
                with get_db() as conn:
                    return imp_svc.import_summary(conn, int(params["import_id"]))
            return _text_result(await asyncio.to_thread(work))
        except Exception as exc:  # noqa: BLE001
            return _text_result({"error": f"That didn't work: {exc}"})

    async def remap_import(tool_call_id, params, abort_event=None, on_update=None):
        try:
            def work():
                with get_db() as conn:
                    return imp_svc.remap_import(conn, int(params["import_id"]),
                                                params.get("mapping", []))
            return _text_result(await asyncio.to_thread(work))
        except Exception as exc:  # noqa: BLE001
            return _text_result({"error": f"That didn't work: {exc}"})

    async def approve_import(tool_call_id, params, abort_event=None, on_update=None):
        try:
            def work():
                return imp_svc.approve_import(int(params["import_id"]),
                                              params.get("indexes"))
            return _text_result(await asyncio.to_thread(work))
        except Exception as exc:  # noqa: BLE001
            return _text_result({"error": f"That didn't work: {exc}"})
```

Add the import at the top of `tools.py`:

```python
from ..services import imports as imp_svc
```

- [ ] **Step 5: Register the three tools (append to the shared `tools` list, before the `if channel == "ui":` block)**

```python
        AgentTool(
            name="get_import_summary",
            label="Import summary",
            description=("Summarise a parsed statement import by id: row count, "
                         "duplicates, category buckets, unresolved rows, a sample."),
            parameters=GET_IMPORT_SUMMARY_SCHEMA,
            execute=get_import_summary,
        ),
        AgentTool(
            name="remap_import",
            label="Remap import",
            description=("Assign categories to statement rows by id. mapping is a "
                         "list of {match:{merchant|contains|index}, category_id}. "
                         "Re-checks duplicates. Call after organising categories."),
            parameters=REMAP_IMPORT_SCHEMA,
            execute=remap_import,
        ),
        AgentTool(
            name="approve_import",
            label="Approve import",
            description=("Record a reviewed statement import (optionally only the "
                         "given row indexes). ALWAYS confirm with the user first."),
            parameters=APPROVE_IMPORT_SCHEMA,
            execute=approve_import,
        ),
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd api && poetry run pytest tests/test_agent_tools.py::test_import_tools_registered -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add api/app/agent/tools.py api/tests/test_agent_tools.py
git commit -m "feat(api): agent tools get_import_summary/remap_import/approve_import"
```

---

### Task 6: Prompt — statement flow + two gates

**Files:**
- Modify: `api/app/agent/prompts.py`
- Test: `api/tests/test_agent_tools.py` (string assertion on the built prompt)

- [ ] **Step 1: Write the failing test**

```python
def test_prompt_mentions_import_gates():
    from app.agent.prompts import system_prompt   # match the real export name
    text = system_prompt("ui")
    assert "get_import_summary" in text
    assert "confirm" in text.lower()
```

(If the prompt builder has a different name/signature, adjust the import to match `prompts.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd api && poetry run pytest tests/test_agent_tools.py::test_prompt_mentions_import_gates -v`
Expected: FAIL — substring missing

- [ ] **Step 3: Add a statement-import section to the system prompt**

Insert into the prompt body (UI channel section):

```
Statement imports (web chat): when the user uploads a bank statement or expense
sheet, it is parsed into a pending import and you are told its import_id.
1. Call get_import_summary(import_id) and tell the user the counts, duplicates,
   and any categories you could not place.
2. Propose category/sub-category changes. Do NOT create, rename, re-parent or
   delete categories until the user explicitly agrees (gate 1). Use
   manage_categories once they do.
3. Call remap_import(import_id, mapping) to assign categories to the unresolved
   rows.
4. Show the user what you will record (counts + mapping). Do NOT call
   approve_import until the user explicitly confirms (gate 2).
5. Call approve_import(import_id) and report created/skipped/failed counts.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd api && poetry run pytest tests/test_agent_tools.py::test_prompt_mentions_import_gates -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/app/agent/prompts.py api/tests/test_agent_tools.py
git commit -m "feat(api): agent prompt — statement import flow with two confirm gates"
```

---

## Phase 6 — Chat route wiring

### Task 7: Generic file param + statement routing

**Files:**
- Modify: `api/app/routes/chat.py`
- Test: `api/tests/test_chat_import.py`

- [ ] **Step 1: Write the failing test**

```python
# api/tests/test_chat_import.py
from fastapi import FastAPI
from fastapi.testclient import TestClient
import app.routes.chat as chat_routes


def _client(monkeypatch):
    # Stub classify_and_start so no LLM runs; assert the route starts an import
    async def fake_classify(filename, data, profile_id=None):
        return {"kind": "statement", "import_id": 42}
    monkeypatch.setattr(chat_routes.imports_svc, "classify_and_start", fake_classify)

    captured = {}

    class FakeSession:
        async def run(self, prompt):
            captured["prompt"] = prompt
            yield {"type": "done", "text": "ok", "error": None}

    monkeypatch.setattr(chat_routes.sessions, "get", lambda sid, channel="ui": FakeSession())
    app = FastAPI(); app.include_router(chat_routes.router)
    return TestClient(app), captured


def test_csv_upload_starts_import_and_injects_id(monkeypatch):
    client, captured = _client(monkeypatch)
    resp = client.post("/api/chat/sessions/s1/messages",
                       data={"message": ""},
                       files={"file": ("bank.csv", b"a,b\n1,2\n", "text/csv")})
    assert resp.status_code == 200
    assert "42" in captured["prompt"]          # import_id injected into the agent prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd api && poetry run pytest tests/test_chat_import.py::test_csv_upload_starts_import_and_injects_id -v`
Expected: FAIL — route still uses `image` field / no `imports_svc` attribute

- [ ] **Step 3: Rewrite the `send_message` route**

```python
from ..services import imports as imports_svc   # add at top

_STATEMENT_EXT = (".csv", ".xlsx", ".xls")


def _is_statement(filename: str, content_type: str | None) -> bool:
    name = (filename or "").lower()
    if name.endswith(_STATEMENT_EXT):
        return True
    return name.endswith(".pdf")  # PDFs are classified downstream


@router.post("/api/chat/sessions/{session_id}/messages")
async def send_message(session_id: str, message: str = Form(""),
                       file: UploadFile | None = File(None)):
    session = sessions.get(session_id, channel="ui")
    data = await file.read() if file is not None else None
    filename = file.filename if file is not None else None
    content_type = file.content_type if file is not None else None
    is_image = bool(content_type and content_type.startswith("image/"))

    async def stream():
        try:
            prompt = message
            if data and not is_image and _is_statement(filename, content_type):
                yield _sse({"type": "status", "text": "Reading statement…"})
                result = await imports_svc.classify_and_start(filename, data)
                if result["kind"] == "statement":
                    prompt = (f"{message}\n\n[The user uploaded the statement "
                              f"'{filename}'. It was parsed as import "
                              f"#{result['import_id']}. Review it with "
                              f"get_import_summary and follow the import flow.]")
                elif result["kind"] == "failed":
                    yield _sse({"type": "done",
                                "text": "I couldn't read that statement. "
                                        "Try a CSV export.",
                                "error": result.get("error")})
                    return
                else:   # receipt (e.g. single-row PDF)
                    prompt = await build_receipt_prompt(message, data, content_type)
            elif data:   # image -> receipt
                yield _sse({"type": "status", "text": "Reading receipt…"})
                prompt = await build_receipt_prompt(message, data, content_type)
            if not prompt.strip():
                yield _sse({"type": "done", "text": "Send a message or file.", "error": None})
                return
            async for event in session.run(prompt):
                yield _sse(event)
        except Exception as exc:  # noqa: BLE001
            yield _sse({"type": "done",
                        "text": "Sorry, something went wrong on my side. Try again.",
                        "error": str(exc)})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
```

> Note: the single-row PDF "receipt" branch re-reads bytes via `build_receipt_prompt` — acceptable; the PDF was already classified once. If double OCR cost matters, a later optimisation can pass the parsed single row straight to `record_transaction`; out of scope for v1.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd api && poetry run pytest tests/test_chat_import.py -v`
Expected: PASS

- [ ] **Step 5: Run the full backend suite**

Run: `cd api && poetry run pytest -q`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add api/app/routes/chat.py api/tests/test_chat_import.py
git commit -m "feat(api): chat route accepts statements; routes to import flow"
```

---

## Phase 7 — Frontend

### Task 8: Widen chat upload to files

**Files:**
- Modify: `web/src/api.ts:87-94`
- Modify: `web/src/components/ChatThread.tsx`

- [ ] **Step 1: Update `streamChat` to send a generic `file`**

In `web/src/api.ts`, change the signature and field name:

```typescript
export async function streamChat(sessionId: string, message: string,
    file: File | null, onEvent: (e: ChatEvent) => void): Promise<void> {
  const form = new FormData();
  form.set("message", message);
  if (file) form.set("file", file);
  const response = await fetch(`/api/chat/sessions/${sessionId}/messages`,
    { method: "POST", body: form });
  // ... rest unchanged
```

- [ ] **Step 2: Update `ChatThread.tsx`**

- Rename state `image`→`file` (keep it simple: `const [file, setFile] = useState<File | null>(null);`).
- Widen the input: `accept="image/*,application/pdf,.csv,.xlsx,.xls"`.
- Update the call site `streamChat(session, message, file, onEvent)`.
- Update the placeholder label from `"(receipt image)"` to `file ? \`(file: ${file.name})\` : "(file)"`.
- Update the send-disabled guard and the attachment label to use `file`.

```tsx
const [file, setFile] = useState<File | null>(null);
// ...
if (busy || (!input.trim() && !file)) return;
const message = input.trim(); const attachment = file;
setItems((prev) => [...prev,
  { role: "user", text: message || (attachment ? `(file: ${attachment.name})` : "") }, /* ... */]);
// ...
<input type="file" accept="image/*,application/pdf,.csv,.xlsx,.xls" hidden
       onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
{file && <span className="muted">{file.name}</span>}
// send button:
<button className="primary" disabled={busy || (!input.trim() && !file)} ...>
```

- [ ] **Step 3: Build to verify types**

Run: `cd web && npm run build`
Expected: `tsc` + vite succeed, no type errors.

- [ ] **Step 4: Commit**

```bash
git add web/src/api.ts web/src/components/ChatThread.tsx
git commit -m "feat(web): chat accepts CSV/XLSX/PDF uploads (generic file field)"
```

---

## Phase 8 — Verify end-to-end

### Task 10: Manual + suite verification

- [ ] **Step 1: Full backend suite**

Run: `cd api && poetry run pytest -q`
Expected: all green.

- [ ] **Step 2: Web build**

Run: `cd web && npm run build`
Expected: green.

- [ ] **Step 3: Rebuild Docker web/api and smoke-test in browser**

```bash
docker compose up -d --build
```
Then in web chat: upload a small CSV (3-4 rows), confirm the agent calls `get_import_summary`, proposes categories, and only records after you confirm. Verify rows appear in Transactions and an `import_uploaded` (channel=chat) + `import_approved` audit row exist (Settings → Activity).

- [ ] **Step 4: Final commit (if any docs/CLAUDE.md updates)**

Update `CLAUDE.md` import-flow note + `docs/architecture.md` if needed, then:

```bash
git add -A && git commit -m "docs: note chat statement import flow"
```

---

## Self-Review Notes

- **Spec coverage:** §3 routing → Task 4/7; §5 migration → Task 9; §6 tools → Task 5; §7 flow → Tasks 5–7; §8 gates → Task 6; §9 errors → Tasks 4/7; §10 idempotency/dedup → Tasks 1/3 (reuse `external_ref`, `flag_duplicates`); §12 testing → every task is TDD; §13 WhatsApp explicitly out of scope (no task). Covered.
- **Ordering caveat:** Task 9 (migration) must precede Task 1 — flagged at both tasks.
- **Signature checks to confirm against the live code during execution:** `db.init_db` arg shape (Task 9 test), prompt builder export name in `prompts.py` (Task 6 test), `cat_svc.get_category` raising on cross-profile (used in Task 3). Adjust the test harness lines to match; the production code is exact.
- **No new types referenced that aren't defined here.** `imp_svc`/`imports_svc` import aliases are introduced where used.
```
