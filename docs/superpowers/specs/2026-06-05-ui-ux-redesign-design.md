# Expense Manager — UI/UX Redesign & Local-First Backend

**Date:** 2026-06-05
**Status:** Approved by user (brainstorming session, 28 questions)
**Approach:** Hybrid — frontend rewritten from scratch; backend refactored in place around a local SQLite store.

## 1. Problem

The current app is Google-first: every dashboard load and agent query hits the
Google Sheets API. With Google not connected, the dashboard shows errors
instead of stats. The 4-tab UI is generic, dark, and structureless. Chat
sessions die on backend restart. Raw exceptions leak to the UI.

## 2. Goals

- Opening the app always shows a stats dashboard — zeros and empty states,
  never errors, regardless of Google connection.
- App owns all data locally (transactions, categories, receipt images, chat
  history). Google Sheets/Drive is an **optional, user-enabled sync target**.
- Warm, friendly visual design (YNAB-like). Desktop-only.
- Faster: all reads from SQLite; Google I/O is background-only.
- Persistent chat sessions (ChatGPT-style list).
- New capabilities: quick-add form, budgets, recurring transactions,
  configurable tax profiles, taxable-flag per category, statement/sheet
  imports with agent parsing and dedup, CSV export, receipt lightbox.

## 3. Non-Goals (explicit)

- No auth/login (single local user).
- No mobile/responsive layout (WhatsApp covers mobile capture).
- No multi-currency — CAD only.
- No sheet→app reverse sync (one-way app→Google).
- No pin-to-dashboard for generative UI (chat-only, as today).
- No WhatsApp budget alerts (weekly summary only).
- No data migration (fresh start).

## 4. Decisions Record (Q&A)

| # | Topic | Decision |
|---|-------|----------|
| 1 | Core complaint | Dashboard must show stats on open, not errors; local storage; Google optional, reconciles when connected |
| 2 | Source of truth | App DB always; Google sync optional, not required |
| 3 | Login | None — open straight to dashboard |
| 4 | Dashboard layout | A: classic analytics (metric cards / charts / recent table) |
| 5 | Chat placement | C: floating bubble (slide-over) + full chat page |
| 6 | Manual entry | A: quick-add form, no LLM |
| 7 | Tax config | C: province presets (QC GST 5 + QST 9.975, ON HST 13, …) + custom components |
| 8 | Tax entry direction | B: enter total paid → back-calculate components |
| 8b | Taxability | Per-category `taxable` flag (Groceries ✓, Rent ✗) |
| 9 | Dashboard period | A: current month default + selector (last month, 3mo, 6mo, YTD, custom) |
| 10 | Visual style | D: warm & friendly (cream, rounded, soft pastels — YNAB feel) |
| 11 | Navigation | C: minimal top bar + drill-in from dashboard tiles |
| 12 | Transactions page | C: full CRUD + bulk ops + CSV export |
| 13 | Receipts | A: thumbnail in row → lightbox overlay |
| 14 | Sync mode | C: auto background push + manual "Sync now" + hourly reconcile |
| 15 | WhatsApp record reply | B: detailed breakdown (amount, taxes, total, counted, category, link) |
| 16 | WhatsApp proactive | B: weekly summary |
| 17 | Budgets | A: core feature, per-category monthly limits, dashboard progress |
| 18 | Recurring | A: rules auto-record on schedule |
| 19 | Currency | CAD only |
| 20 | Mobile | Desktop only |
| 21 | Generative UI | A: chat-only inline charts (as today) |
| 22 | Existing data | Fresh start; ongoing import is a feature |
| 23 | Import flow | A: agent parses + auto-categorizes → review screen → approve |
| 24 | Import dedup | A: flag likely duplicates (date ±1 day, same amount) in review |
| 25 | Statement formats | CSV/Excel + PDF (best effort) |
| 26 | Backend pains | A+B+C: persist sessions, local-DB speed, friendly errors |
| 27 | Chat history | B: sessions list, new/browse/resume |
| 28 | Dashboard arrangement | A: budgets as right rail |

## 5. Architecture

```
web/   React + Vite + TS — rewritten. No business logic.
       react-router, TanStack Query, recharts.
  │ /api/*
api/   Python 3.13 FastAPI — refactored in place.
  ├─ SQLite (source of truth)  api/data/expense.db, WAL mode
  ├─ Receipt files             api/data/receipts/
  ├─ Pi agent runtime          KEPT (pi-agent SDK, AnthropicMessagesProvider,
  │                            Claude Max OAuth) — tools rewired to SQLite
  ├─ NVIDIA OCR client         KEPT
  ├─ WhatsApp (neonize)        KEPT + weekly summary scheduler
  ├─ Google OAuth flow         KEPT — demoted to sync module
  └─ Schedulers (asyncio)      recurring runner · hourly reconcile · weekly summary
```

### 5.1 Data model (SQLite)

```
transactions    id PK, date, type (income|expense), category_id FK, description,
                merchant, amount, tax_breakdown JSON ({"GST": 2.13, "QST": 4.24}),
                total, counted, image_path NULL, source (ui|whatsapp|import|recurring),
                external_ref NULL (import dedup key), sync_status (synced|pending|n/a),
                created_at, updated_at
categories      id PK, name UNIQUE, type (income|expense), percent (0-100, default 100),
                taxable BOOL default true, budget_monthly NULL
tax_profiles    id PK, name, components JSON ([{"name":"GST","rate":5.0}, …]),
                is_active BOOL (exactly one active)
recurring_rules id PK, template JSON (txn fields), frequency (monthly|weekly|biweekly),
                next_run DATE, active BOOL
chat_sessions   id PK, title, channel (ui|whatsapp), created_at, updated_at
chat_messages   id PK, session_id FK, role, content JSON, created_at
settings        key PK, value JSON (google tokens, spreadsheet id, sync cursor,
                whatsapp prefs, active period default)
imports         id PK, filename, status (parsing|review|approved|failed),
                rows JSON (parsed + suggested category + dup flags), created_at
```

Seeded on first run: default categories (with sensible `taxable` flags:
Rent ✗, Salary ✗, others ✓) and tax profile presets (Quebec active default:
GST 5% + QST 9.975%; Ontario HST 13%; Alberta GST 5%; custom template).

### 5.2 Tax engine

- Active profile components + category `taxable` flag drive everything.
- Entry direction: user supplies **total paid**; components back-calculated:
  `component_i = total × rate_i / (1 + Σ rates)`; `amount = total − Σ components`.
- Non-taxable category: `amount = total`, empty breakdown.
- `counted = total × category.percent / 100` (existing formula, unchanged).
- Same engine used by quick-add API, agent `record_transaction` tool, and
  import parsing.

### 5.3 Sync engine (app → Google, one-way)

- Off until user connects Google in Settings.
- On write: background task pushes row to sheet (app `id` stamped in a column);
  Drive upload of receipt image. Failure → `sync_status=pending`.
- Hourly reconcile: diff app ids vs sheet ids, push missing, update changed
  rows, delete removed ones. Idempotent. Never reads sheet data back.
- Manual `POST /api/sync/now` triggers the same reconcile.
- Top bar shows sync state (idle/pending-count/error); failures never block UX.

## 6. API Surface

```
GET    /api/dashboard?period=…       metrics, trend, by_category, budgets, recent
GET    /api/transactions             filters: period, type, category, q, page
POST   /api/transactions             quick-add (server computes taxes/counted)
PATCH  /api/transactions/{id}
DELETE /api/transactions/{id}
POST   /api/transactions/bulk        {ids, action: delete|recategorize, …}
GET    /api/transactions/export.csv
GET    /api/receipts/{id}            serves local image (lightbox)

GET/POST/PATCH/DELETE /api/categories       (+ taxable, budget_monthly)
GET/POST/PATCH        /api/tax-profiles     (activate, edit components)
GET/POST/PATCH/DELETE /api/recurring

POST   /api/imports                  multipart CSV/XLSX/PDF → parse job
GET    /api/imports/{id}             status, parsed rows, dup flags
POST   /api/imports/{id}/approve     {row_indexes | all}

GET/POST /api/chat/sessions          list / create
GET    /api/chat/sessions/{id}       message history
POST   /api/chat/sessions/{id}/messages   SSE stream; optional image
DELETE /api/chat/sessions/{id}

GET    /api/sync/status              POST /api/sync/now
GET    /api/whatsapp/qr|status       (unchanged)
GET    /api/google/auth|callback|status   (unchanged flow; now enables sync)
GET    /api/health
```

Error contract: all errors → `{"error": {"code": "...", "message": "human text"}}`
with proper HTTP status. No raw tracebacks in responses.

## 7. Agent changes

- Tools rewired to SQLite services. New tools: `manage_budgets`,
  `manage_recurring`. Existing: record/query/summary/categories/render_ui.
- Import parsing job: file text (CSV/XLSX direct; PDF via text extraction,
  NVIDIA OCR fallback for scanned) → agent structures rows + suggests
  categories + dedup candidates (same amount, date ±1 day vs existing).
- Chat persistence: sessions + messages stored; UI session list backed by DB;
  agent state rebuilt from history on resume.
- Friendly degradation: tool errors → human text in-channel
  ("Receipt saved; OCR unavailable — tell me the amount").
- WhatsApp: detailed record confirmations; Sunday 18:00 weekly summary
  (income, expenses, net, top categories) to the linked chat.

## 8. Frontend

**Theme:** cream `#f7f4ef` background; white cards radius 16, soft shadows
`rgba(180,150,100,.12)`; green `#3a8f63` income/positive; amber `#c2742c`
expense/warn; charcoal `#2d2a24` text; muted `#a08c6a` labels.

**Top bar:** logo · period selector · sync dot · settings gear.
**Floating chat bubble** on all routes → slide-over quick chat; recording via
chat revalidates dashboard queries live.

| Route | Content |
|---|---|
| `/` | Metric cards (Income, Expenses, Net, + Quick-add button) · budgets right rail w/ progress bars · 6-period trend line · category pie · recent transactions (rows click → `/transactions`) |
| `/transactions` | Filter bar (period, type, category, search) · table w/ inline edit · multi-select bulk recategorize/delete · CSV export · receipt thumbnail → lightbox |
| `/chat` | Sessions sidebar (new/rename/delete/resume) · message thread · streaming + inline gen-UI charts · image attach |
| `/settings` | Categories editor (type, percent, taxable, budget) · Tax profiles (preset picker + component editor) · Recurring rules · Google connect + sync status/now · WhatsApp QR · Imports (upload → review screen) |

**Quick-add modal:** date (default today), total paid, category (taxable →
live tax breakdown display), merchant, note → optimistic update.

**Import review screen:** parsed table, category dropdowns pre-filled by
agent, duplicate rows amber-flagged with keep/skip toggle, "Approve all" /
"Approve selected".

**Empty states:** zero-data dashboards show friendly prompts, never errors.
Google-not-connected appears only as a Settings banner.

## 9. Error handling

- Services raise typed exceptions → route layer maps to error contract →
  frontend toasts/inline messages.
- Agent tool layer catches all, returns friendly text, logs raw.
- Sync failures: mark pending, retry hourly, surface count in top bar.

## 10. Testing

- pytest units: tax back-calculation per profile, counted formula, dedup
  matcher, recurring next-run logic, reconcile idempotency.
- API tests: FastAPI TestClient + temp SQLite per test.
- Agent e2e with mock provider (existing pattern).
- Frontend: `tsc` + vite build gate; manual smoke via `make start`.

## 11. Migration / rollout

- Fresh DB on first boot; seeds applied. No data import from the old sheet.
- Old `web/src` deleted and rebuilt. Old sheets-as-storage services replaced
  by SQLite services + sync module. Routes/Make targets keep their shape.
- Existing verified components (Claude OAuth provider, neonize pairing, NIM
  OCR, Google OAuth) carried over unchanged.
