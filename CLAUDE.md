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
make cleanup    # remove containers + images; data in ./data is PRESERVED
make cleanup-data # DESTRUCTIVE: also deletes ./data (wipes DB + WhatsApp pairing)
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
  errors.py                AppError(code, message, status, details?) + handler →
                           {"error":{code,message,details?}} (details = optional dict)
  routes/                  thin HTTP: pydantic in, `with get_db()`, service call
    transactions dashboard categories recurring imports chat sync settings
    whatsapp google_auth audit profiles
  services/                ALL business logic + SQL; conn is always first arg
    transactions.py        CRUD/bulk/CSV/dashboard_data; _compute = ONLY money math;
                           every write → audit row + sync dirty flag. Categories
                           resolved by id (_resolve_category, accepts category_id);
                           dashboard rolls sub-cats up to parent (pie + budgets).
                           create_transaction(check_duplicate=) opt-in dup gate →
                           raises duplicate_suspected(409) unless confirm_duplicate
    dedup.py               THE one duplicate rule (find_duplicate): exact
                           receipt_link match OR total+merchant+date+profile.
                           Used by import grid (flag_duplicates) + the create gate
    tax.py periods.py categories.py recurring.py chat_store.py
                           categories: one-level nesting via parent_id (0=top);
                           UNIQUE(name, profile_id, parent_id)
    profiles.py            CRUD + active_id(conn) + update_profile(prompt_loan); all services scope to active
    receipts.py            OCR intake (image/PDF→prompt; PyMuPDF renders PDF pages to PNG) AND lazy Drive download
    imports.py             statement upload → agent parses rows → review/approve.
                           classify_and_start routes a CHAT upload (receipt vs
                           statement); import_summary + remap_import drive the
                           in-chat flow; _persist_import(source_link=) shared;
                           set_source_link() stores the Drive URL after async
                           upload; `channel` column marks chat-originated imports
    sync.py                one-way Google push; request_sync()/sync_worker() debounce.
                           Per-profile configurable columns (COLUMN_REGISTRY +
                           SHEET_COLUMN_CONFIG); frozen TOTALS row at top (row 2,
                           open-ended SUM), data from row 3; Receipt name+link +
                           Counted % columns; cross-year Summary tab
    vision.py              OCR dispatch: nvidia | claude | openai (setting-driven)
    ocr.py                 NVIDIA NIM client — PROTECTED
    audit.py               append-only audit_log (channel/event/ref/detail)
    google_client.py       OAuth + Drive/Sheets clients; client creds + tokens in
                           settings table (.env fallback for creds)
    summary_text.py        WhatsApp weekly summary text
  agent/
    runtime.py             Session per chat id; last 30 messages (15 pairs) replayed
                           from chat_store on construction; SSE events
    anthropic_provider.py  Claude Max OAuth provider — PROTECTED
    tools.py               record/update/delete_transaction, query, get_summary,
                           manage_* (recurring has update), list_profiles,
                           set_active_profile (all channels),
                           get_import_summary/remap_import/approve_import
                           (chat statement import), render_ui (ui only).
                           EVERY data tool takes optional `profile` name → act on
                           another book WITHOUT switching active. record_transaction
                           takes `confirm_duplicate` (override the dup warning)
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
```

## Core flows

- **Record txn**: any path → `transactions.create_transaction(conn, data)` —
  takes `total` paid; derives amount/taxes from category.taxable + active
  tax_profile; writes audit row; fires `sync.request_sync()`. NEVER insert
  into transactions directly; never compute taxes elsewhere. Txns also carry
  `loan` (bool) + `receipt_link` (external Drive/doc URL).
- **Duplicate warning**: human-facing add paths (web QuickAdd, agent record)
  pass `check_duplicate=True`; on a `dedup.find_duplicate` hit the gate raises
  `duplicate_suspected`(409) carrying the matched txn — the surface warns and
  re-submits with `confirm_duplicate` to override. Recurring + import do NOT
  opt in (rent monthly isn't a dup; import has its own grid flagging).
- **Profiles**: full data partition — each profile owns its transactions,
  categories, tax_profile and its OWN Google sheet + Drive folder, plus a
  `prompt_loan` flag that tells the chat/WhatsApp agent to ask "Was this paid
  from your personal pocket?" before recording (sets `loan=true` if yes — for
  incorporation profiles where employees claim reimbursement). Active profile is
  a settings key; services scope every query via `profiles.active_id(conn)`.
  Recurring rules fire under their own rule.profile_id, not the active one.
- **Chat**: routes/chat SSE → runtime.Session → tools → services. UI specs
  from `render_ui` rendered verbatim by GenUI.tsx. Before recording a txn
  the agent confirms the target profile (when 2+ profiles exist), the
  category/sub-category, the type, and — when the chosen profile's
  `prompt_loan` is true — whether the expense was paid from personal pocket
  (sets `loan=true` if yes); tools: `list_profiles`, `set_active_profile`. To read/
  write a NON-active book the agent passes `profile` per call (never switches
  active). On a duplicate the tool returns `{duplicate:true,…}` → agent warns,
  re-calls with `confirm_duplicate=true` only after the user agrees. Chat also
  accepts a CSV/XLSX/PDF upload (web only): `classify_and_start` decides receipt
  vs statement; a statement starts an import and the agent reviews it via
  `get_import_summary`, helps organize categories, `remap_import`s rows, then
  `approve_import`s — TWO confirm gates (create/organize categories; record).
  Rows stay server-side; the agent works by import_id. After `classify_and_start`
  returns, the original source file is uploaded to Drive asynchronously
  (`_try_upload_import_source`, silent on failure) and the Drive URL stored as
  `import.source_link`; at `approve_import` time, any row without its own
  `receipt_link` inherits `source_link` as its `receipt_link`. Agent replies
  render as markdown (react-markdown + GFM).
- **Import**: upload → agent structures rows → review grid (per-profile chooser,
  inline-edit category/sub/type/total/loan/notes/receipt_link, dup flag) →
  approve sends the edited rows. Grid fetches the target book's categories via
  `/api/categories?profile_id=`; approval is per-row fault-tolerant. The same
  pipeline (parse → dedup → `approve_import`) backs the in-chat import above.
- **WhatsApp**: neonize MessageEv → `should_process` gate (self-chat =
  chat==sender, covers hidden `@lid` JIDs; allowlist for others; outbound
  ids tracked → no loops) → same handler/agent as web.
- **Sync**: write → dirty flag → debounced `sync_worker` → idempotent
  `reconcile()` (sheet ID column maps txn id → row; missing-id rows REMOVED via
  `deleteDimension`, not blanked). One-way; failures → audit + `last_error`.
  Per-profile: own sheet + Drive folder + own configurable column layout;
  frozen `TOTALS` row at row 2 (data from row 3); reconcile loops all profiles.
  Auto-sync handles the happy path ~2s after each write; `Sync Now` is a manual
  force/retry/catch-up.

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
- Chat statement import is WEB ONLY. WhatsApp `documentMessage` handling only
  downloads pdf/image mimes (CSV/XLSX dropped) and routes any doc as a receipt;
  statement parity needs the `MessageHandler` contract to carry a generic file
  (filename+bytes+mime) — deferred. See `docs/design/2026-06-15-chat-statement-import.md`.
- Claude Max OAuth tokens only work when the FIRST system block is the
  Claude Code identity string (see provider + `vision._claude_extract`).
- `db.get_db()` commits on clean exit, rolls back on exception; WAL set once
  in `init_db`, `busy_timeout=5000` per connection.
- Schema changes = idempotent migration block in `init_db()` + test.
- Profiles migration rebuilds categories/tax_profiles to `UNIQUE(name,
  profile_id)` on a dedicated AUTOCOMMIT connection — `PRAGMA
  foreign_keys=OFF` is a no-op inside a transaction (see `_migrate_profiles`).
- Sandboxed `drive.file` scope now (app only sees files it creates); users
  connected under the old full-drive scope must reconnect Google.
- Tests: inject, don't patch internals — `WhatsAppManager(client_factory=)`,
  `vision._*_extract`, fake Sheets client (see `api/tests/test_sync.py`).
- `.env` lives at repo root (loaded by `config.py`). `DATA_DIR` in Docker and
  `make dev-api` resolves to repo-root `./data/` (bind-mounted; survives
  volume prune). Raw `uvicorn` without `DATA_DIR` set still defaults to
  `api/data/` (gitignored).
- `make cleanup` is non-destructive (containers/images only). Use
  `make cleanup-data` to actually wipe `./data/`.
- A category name can exist as BOTH a top-level and a sub-category
  (`UNIQUE(name,profile_id,parent_id)`); `find_category_by_name` raises
  `ambiguous_category` rather than guess. Prefer `category_id` on writes. Name-based
  batch paths (recurring templates, imports) tolerate this: recurring isolates +
  deactivates a broken rule; import approval is per-row fault-tolerant.
- Docker bakes the source into the image (only `./data` is bind-mounted) — code/
  migration changes need `make restart` (rebuild); a bare `docker restart` runs
  the STALE image. Symptom: behaviour unchanged after a "restart".
- Sheet TOTALS row is FROZEN at row 2 (header row 1, data row 3+); Sheets can only
  freeze from the top, so totals live at the top, not the bottom.
- Duplicate rule lives ONLY in `dedup.find_duplicate` — change it there, nowhere
  else. It is a soft WARN, never a hard block (legit dups exist, e.g. two coffees);
  `confirm_duplicate` bypasses it. Receipt match needs a stored `receipt_link`, so
  it only fires where one is set (agent, QuickAdd receipt field, import).
- Statement source-file Drive upload (`_try_upload_import_source`) is async and
  silent-on-failure — it runs after `classify_and_start` returns, inside the SSE
  stream generator. If the user approves the import before the upload completes
  (fast approval, slow Drive) or Google Drive is not configured, `import.source_link`
  will be NULL and no `receipt_link` is stamped on the resulting transactions.
  This is intentional degradation; a missing link does not fail the import.
