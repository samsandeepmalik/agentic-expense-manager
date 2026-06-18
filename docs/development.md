# Development

**Dev setup, test conventions, API surface, and how-to guides.** For system design and diagrams see [Architecture](architecture.md); for PR guidelines see [Contributing](../CONTRIBUTING.md).

*Read this when:* setting up a local dev environment · adding an endpoint, agent tool, or OCR provider · writing or debugging tests

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.13 + [Poetry](https://python-poetry.org) | Backend runtime and dependency management |
| Node 22 | Frontend toolchain |
| `libmagic` | neonize dependency — `brew install libmagic` / `apt install libmagic1` |
| Docker | Only for the `make start` path |
| `.env` at repo root | `cp .env.example .env`; set at least `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` |

---

## Run

```bash
# Docker (production-like): web :5173 (nginx, proxies /api), api :8000
make start        # build + up
make logs-api     # follow backend logs
make cleanup      # remove containers + images; data in ./data is PRESERVED
make cleanup-data # DESTRUCTIVE: also deletes ./data (DB + receipts + WhatsApp pairing)

# Local dev (hot reload) — or use the make shortcuts: make dev-api / make dev-web
cd api && poetry install --no-root
poetry run uvicorn app.main:app --reload --port 8000
cd web && npm install && npm run dev          # :5173, proxies /api → :8000
```

State lives in `./data/` at the repo root (both Docker and `make dev-api` use
this path; `DATA_DIR` defaults there). Delete `./data/expense.db*` for a fresh
DB (schema + seeds recreate on boot).

---

## Test & build

```bash
cd api && poetry run pytest -v        # backend suite (~268 tests, ~3s)
cd web && npm run build               # tsc --noEmit + vite build
```

Run both after every change. **Never commit on red.**

### Test conventions

- Tests live in `api/tests/`; `conftest.py` provides `db_path` (temp SQLite,
  schema + seeds) and `conn` fixtures.
- TDD: write the failing test first, watch it fail, implement, watch it pass.
- Pure logic gets direct unit tests (`should_process`, `tax.back_calculate`,
  `resolve_period`). HTTP behavior is tested through `TestClient` with only
  the router under test mounted.
- External boundaries have injection seams — use them instead of patching
  internals: `WhatsAppManager(client_factory=...)` (fake neonize client),
  `vision._{nvidia,claude,openai}_extract` (monkeypatch the strategy fns),
  fake Sheets client in `test_sync.py`.
- `conftest.py` builds a **fresh** DB straight from `SCHEMA`, so
  `ALTER TABLE` migration branches are never exercised by the normal suite.
  The upgrade-existing-DB path is covered exclusively by
  `tests/test_legacy_migration.py` — add cases there when adding idempotent
  migrations.

---

## Conventions

- Service functions take `conn: sqlite3.Connection` first; routes use
  `with get_db() as conn:` (commit/rollback handled by the context manager).
- Raise `AppError(code, message, status)` for client errors — never leak raw
  exceptions (generic handler returns opaque 500).
- Settings-table keys: add to `app/settings_keys.py`, import the constant.
- Money: `round(x, 2)` at service boundaries; all derivation in
  `transactions._compute` — don't duplicate tax math.
- Frontend: `web/src/api.ts` is transport + types only. Components fetch via
  TanStack Query; mutations invalidate the affected query keys. No business
  logic, no money math (the QuickAdd preview is cosmetic).
- Schema changes: idempotent migration block in `db.init_db()`
  (`PRAGMA table_info` check → `ALTER TABLE`), plus a migration test.
  SQLite gotcha: `ADD COLUMN` cannot combine a `REFERENCES` FK with a
  non-NULL default — add the column without the FK clause (e.g. the
  `imports.profile_id` migration uses `INTEGER NOT NULL DEFAULT 1` with no
  `REFERENCES`); the FK is enforced only in `SCHEMA` for fresh databases.
- Profile scoping: service functions scope all queries to the active profile
  via `profiles.active_id(conn)`. By-id lookups 404 across profiles. Service
  functions that mutate by id accept an optional `profile_id` parameter
  (defaults to `profiles.active_id(conn)`); routes pass the active profile.
  Exception: `recurring.run_due_rules` is global — it fires rules for all
  profiles regardless of which is active.
- Sub-categories: `categories` has a `parent_id INTEGER NOT NULL DEFAULT 0`
  column; `0` means top-level. The unique constraint is
  `UNIQUE(name, profile_id, parent_id)` (one level deep only).

---

## How to add things

**An endpoint** — service function in `app/services/` (with tests) → router in
`app/routes/` → register in `main.py`'s router loop → types + query in
`web/src/api.ts` / page.

**An agent tool** — add the executor + JSON schema in `app/agent/tools.py`
(wrap the body in `try/except` returning `{"error": ...}` for friendly
degradation), append an `AgentTool` to the list `build_tools` returns, and
mention it in `app/agent/prompts.py`. Web-only tools (not exposed on WhatsApp)
go inside the `if channel == "ui":` block — currently just `render_ui`. The
existing tools are listed in
[architecture.md → Agent runtime](architecture.md#agent-runtime).

**A channel (Telegram etc.)** — implement `channels/base.BaseChannelRegistry`
(`set_handler / start / list_accounts / send_weekly_summary`), normalize
messages to `(chat_id, text, image_bytes, image_mime)`, append the instance
to `CHANNELS` in `main.py`. Gating/allowlist patterns: see
`channels/whatsapp.should_process`.

**An OCR provider** — `_xxx_extract(image_bytes, mime) -> str` in
`app/services/vision.py`, add to `PROVIDERS` + `available_providers()` +
dispatch in `extract_text`, radio entry in `web/src/pages/Settings.tsx`.

**A transaction write path** — call through
`services/transactions.create_transaction` and friends; audit row + sync
dirty-flag fire automatically. Never INSERT into `transactions` directly. For a
human-facing path that should warn on duplicates, pass `check_duplicate=True` and
handle the `duplicate_suspected` `AppError` (let `confirm_duplicate` in the data
bypass it); leave it off for batch/automated paths (recurring, import).

---

## API surface

| Endpoint | Purpose |
|---|---|
| `GET /api/dashboard?period=` | metrics, trend, by-category, budgets, recent |
| `GET/POST/PATCH/DELETE /api/transactions` (+`/bulk`, `/export.csv`) | transaction CRUD. `POST` warns on a likely duplicate → `409 duplicate_suspected` with the matched txn in `error.details`; resend with `confirm_duplicate: true` to save anyway. Accepts `receipt_link` |
| `GET /api/receipts/{id}` | receipt image (original file) |
| `GET /api/receipts/{id}/preview` | rendered PNG preview (first page for PDFs; original for non-PDFs) |
| `GET/POST/PATCH/DELETE /api/categories` | categories + budgets + taxable; PATCH re-parents (sub-categories). `GET ?profile_id=` lists a non-active book's categories (used by the import grid) |
| `GET/POST /api/tax-profiles` | tax profiles (activate) |
| `GET/POST/PATCH/DELETE /api/recurring` | recurring rules |
| `POST /api/imports` (optional `profile_id`) · `GET /{id}` · `POST /{id}/approve` | statement imports; `approve` accepts the edited review-grid `rows` (category/sub, type, total, loan, notes, receipt_link, skip). Chat-originated imports carry `channel='chat'` |
| `GET/POST/DELETE /api/chat/sessions` · `POST .../messages` (SSE) | chat sessions; `messages` accepts a generic `file` (image → receipt OCR; CSV/XLSX/PDF → statement import, driven by the agent via the `get_import_summary`/`remap_import`/`approve_import` tools) |
| `GET/POST /api/settings/ocr` | OCR provider selection |
| `GET /api/sync/status` · `POST /api/sync/now` | Google sync |
| `GET/POST /api/profiles` · `PATCH /{id}` · `POST /{id}/activate` · `DELETE /{id}` | profiles (separate books); `PATCH` updates `prompt_loan` flag |
| `GET/POST /api/whatsapp/accounts` · `DELETE /{id}` · `POST /{id}/refresh` · `GET /{id}/qr` | account pairing, unpair, QR refresh/fetch |
| `GET /api/whatsapp/status` | overall WhatsApp channel status |
| `GET/POST/DELETE /api/whatsapp/allowed` | sender allowlist |
| `GET /api/google/auth` · `/callback` | Google OAuth redirect + callback |
| `GET /api/google/status` | connection state, pending count, per-profile sheet URLs |
| `POST /api/google/credentials` | save client_id + client_secret |
| `POST /api/google/folder-name` | set Drive folder base name |
| `GET/PUT /api/google/columns?profile_id=` | per-profile sheet column set + order |
| `POST /api/google/profiles/{id}/reset-sheet` | drop a profile's sheet/folder link → next sync recreates |
| `GET /api/audit` | activity feed (writes + sync outcomes) |
| `GET /api/health` | liveness |

Errors always follow `{"error": {"code", "message"}}`, with an optional
`"details"` object when the error carries structured data (e.g.
`duplicate_suspected` includes the matched transaction).

Receipt PDF rendering uses `PyMuPDF` (`fitz`) — already in the Poetry
lockfile. Drive receipt uploads derive MIME type via `mimetypes` (not
hardcoded), so any file type stored as a receipt uploads correctly.

---

## Periods

`?period=` accepts `2026-06`, `last3`, `last6`, `ytd`,
`YYYY-MM-DD:YYYY-MM-DD`; default = current month.

---

## Debugging

- `make logs-api` — timestamped; WhatsApp gate logs every message decision:
  `WhatsApp[id] message chat=... sender=... from_me=... -> PROCESS|ignore`.
- `GET /api/audit` (or Settings → Activity) — who/which channel wrote which
  transaction; sync outcomes incl. failures.
- `GET /api/sync/status` — `pending` count + `last_error`.
- WhatsApp QR expired? Settings → Refresh QR (codes die ~20s after issue).
- Agent misbehaving? The full prompt is in `app/agent/prompts.py`; tool
  results are JSON-serialized service returns.

---

## Protected code — do not change behavior

| File | Why |
|---|---|
| `app/agent/anthropic_provider.py` | Claude Max OAuth quirk (Bearer + beta header + Claude Code system block), verified live |
| `app/services/ocr.py` | NVIDIA NIM client incl. large-image asset upload, verified live |
| neonize event wiring in `app/channels/whatsapp.py` | QR arrives via `client.event.qr(callback)` — **not** `QREv`; handler bodies are live-verified |
| `channels/whatsapp.should_process` | encodes WhatsApp `@lid` self-chat + loop-prevention semantics, fully unit-tested |
| `services/transactions._compute` | single source of money math |
