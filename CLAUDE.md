# Expense Manager — repo map

Local-first expense tracker: FastAPI + SQLite + pi-agent (Claude) backend,
React/Vite frontend, WhatsApp channel via neonize, optional one-way Google
sync. Frontend has ZERO business logic; all money math is server-side.

Deep dives: `docs/architecture.md` (diagrams) · `docs/development.md`.

## Commands

```bash
cd api && poetry run pytest -v     # backend tests — run after every backend change
cd web && npm run build            # tsc + vite — run after every frontend change

# Makefile (Docker stack: web :5173, api :8000)
make start      # build + start containers (the default way to run the app)
make stop       # stop containers, keep state (DB, WhatsApp session)
make restart    # stop + start
make status     # container status
make logs       # follow all logs   (also: logs-api, logs-web)
make cleanup    # DESTRUCTIVE: remove containers + volumes (wipes DB + WhatsApp pairing)
make dev-api    # local api with hot reload (no Docker)
make dev-web    # local web dev server (no Docker; proxies /api → :8000)

# Local dev without make
cd api && poetry install --no-root && poetry run uvicorn app.main:app --reload --port 8000
cd web && npm install && npm run dev
```

Never commit on red. TDD: failing test first.

## Layout

```
api/app/
  main.py                  app wiring: lifespan, CHANNELS list, scheduler loop,
                           sync_worker task, router registration
  db.py                    get_db() ctx mgr, SCHEMA, seeds, idempotent migrations,
                           settings KV (get_setting/set_setting)
  settings_keys.py         ALL settings-table key constants — never inline strings
  config.py                env vars (.env at repo root)
  errors.py                AppError(code, message, status) + handler → {"error":{...}}
  routes/                  thin HTTP: pydantic in, `with get_db()`, service call
    transactions dashboard categories recurring imports chat sync settings
    whatsapp google_auth audit
  services/                ALL business logic + SQL; conn is always first arg
    transactions.py        CRUD/bulk/CSV/dashboard_data; _compute = ONLY money math;
                           every write → audit row + sync dirty flag
    tax.py periods.py categories.py dedup.py recurring.py chat_store.py
    imports.py             statement upload → agent parses rows → review/approve
    sync.py                one-way Google push; request_sync()/sync_worker() debounce
    vision.py              OCR dispatch: nvidia | claude | openai (setting-driven)
    ocr.py                 NVIDIA NIM client — PROTECTED
    audit.py               append-only audit_log (channel/event/ref/detail)
    google_client.py       OAuth + Drive/Sheets clients; ids live in settings table
    summary_text.py        WhatsApp weekly summary text
  agent/
    runtime.py             Session per chat id; history replay from chat_store; SSE events
    anthropic_provider.py  Claude Max OAuth provider — PROTECTED
    tools.py               record_transaction/query/get_summary/manage_*/render_ui
    prompts.py             system prompt (channel-aware: ui vs whatsapp)
  channels/
    base.py                BaseChannelRegistry contract (main.py codes against this)
    whatsapp.py            WhatsAppManager (1 account) + WhatsAppRegistry;
                           should_process gate; event wiring PROTECTED
api/tests/                 pytest; conftest gives db_path + conn fixtures
web/src/
  api.ts                   transport + types only (incl. SSE chat parser)
  pages/                   Dashboard Transactions Chat Settings
  components/              TopBar QuickAdd BudgetRail Charts RecentTable Lightbox
                           ChatBubble ChatThread GenUI ImportReview
  theme.css                warm design tokens (greens/ambers, --bg #f7f4ef)
docs/superpowers/          specs + implementation plans (checkbox-tracked)
```

## Core flows

- **Record txn**: any path → `transactions.create_transaction(conn, data)` —
  takes `total` paid; derives amount/taxes from category.taxable + active
  tax_profile; writes audit row; fires `sync.request_sync()`. NEVER insert
  into transactions directly; never compute taxes elsewhere.
- **Chat**: routes/chat SSE → runtime.Session → tools → services. UI specs
  from `render_ui` rendered verbatim by GenUI.tsx.
- **WhatsApp**: neonize MessageEv → `should_process` gate (self-chat =
  chat==sender, covers hidden `@lid` JIDs; allowlist for others; outbound
  ids tracked → no loops) → same handler/agent as web.
- **Sync**: write → dirty flag → debounced `sync_worker` → idempotent
  `reconcile()` (sheet ID column). One-way; failures → audit + `last_error`.

## PROTECTED — verified live, do not change behavior

- `agent/anthropic_provider.py` — OAuth Bearer + `anthropic-beta:
  oauth-2025-04-20` + Claude Code system block. Fragile.
- `services/ocr.py` — NVIDIA NIM quirks (inline vs asset upload).
- neonize wiring in `channels/whatsapp.py` — QR comes via
  `client.event.qr(callback)`, NOT the QREv decorator. Handler bodies fixed.
- `should_process` + `transactions._compute` — fully tested semantics.

## Gotchas

- WhatsApp QR codes die ~20s after issue; server refuses to serve stale ones
  (`qr_expired`) — use the Refresh button / `POST .../refresh`.
- Self-chat JIDs arrive as `NUMBER@lid`, not `NUMBER@s.whatsapp.net` —
  that's why self-chat detection is `chat == sender`, not `chat == own`.
- Claude Max OAuth tokens only work when the FIRST system block is the
  Claude Code identity string (see provider + `vision._claude_extract`).
- `db.get_db()` commits on clean exit, rolls back on exception; WAL set once
  in `init_db`, `busy_timeout=5000` per connection.
- Schema changes = idempotent migration block in `init_db()` + test.
- Tests: inject, don't patch internals — `WhatsAppManager(client_factory=)`,
  `vision._*_extract`, fake Sheets client (see `api/tests/test_sync.py`).
- `.env` lives at repo root (loaded by `config.py`); `DATA_DIR` defaults to
  `api/data/` (gitignored).
