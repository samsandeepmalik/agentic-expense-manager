# Development

## Prerequisites

- Python 3.13 + [Poetry](https://python-poetry.org)
- Node 22
- `libmagic` (neonize needs it): `brew install libmagic` / `apt install libmagic1`
- Docker (only for the `make start` path)
- `.env` at repo root (`cp .env.example .env`) with at least
  `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`) or `ANTHROPIC_API_KEY`

## Run

```bash
# Docker (production-like): web :5173 (nginx, proxies /api), api :8000
make start        # build + up
make logs-api     # follow backend logs
make cleanup      # nuke containers + volumes (wipes DB + WhatsApp pairing!)

# Local dev (hot reload)
cd api && poetry install --no-root
poetry run uvicorn app.main:app --reload --port 8000
cd web && npm install && npm run dev          # :5173, proxies /api → :8000
```

State lives in `api/data/` locally, `api-data` volume in Docker. Delete
`api/data/expense.db*` for a fresh DB (schema + seeds recreate on boot).

## Test & build

```bash
cd api && poetry run pytest -v        # backend suite (~52 tests, <2s)
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

## How to add things

**An endpoint** — service function in `app/services/` (with tests) → router in
`app/routes/` → register in `main.py`'s router loop → types + query in
`web/src/api.ts` / page.

**An agent tool** — executor + JSON schema in `app/agent/tools.py` (wrap body
in try/except returning `{"error": ...}` — friendly degradation), append an
`AgentTool` to the list, mention it in `app/agent/prompts.py`.

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
dirty-flag fire automatically. Never INSERT into `transactions` directly.

## Debugging

- `make logs-api` — timestamped; WhatsApp gate logs every message decision:
  `WhatsApp[id] message chat=... sender=... from_me=... -> PROCESS|ignore`.
- `GET /api/audit` (or Settings → Activity) — who/which channel wrote which
  transaction; sync outcomes incl. failures.
- `GET /api/sync/status` — `pending` count + `last_error`.
- WhatsApp QR expired? Settings → Refresh QR (codes die ~20s after issue).
- Agent misbehaving? The full prompt is in `app/agent/prompts.py`; tool
  results are JSON-serialized service returns.

## Protected code — do not change behavior

| File | Why |
|---|---|
| `app/agent/anthropic_provider.py` | Claude Max OAuth quirk (Bearer + beta header + Claude Code system block), verified live |
| `app/services/ocr.py` | NVIDIA NIM client incl. large-image asset upload, verified live |
| neonize event wiring in `app/channels/whatsapp.py` | QR arrives via `client.event.qr(callback)` — **not** `QREv`; handler bodies are live-verified |
| `channels/whatsapp.should_process` | encodes WhatsApp `@lid` self-chat + loop-prevention semantics, fully unit-tested |
| `services/transactions._compute` | single source of money math |
