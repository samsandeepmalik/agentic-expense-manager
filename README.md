# Expense Manager

Local-first income & expense management with a chat agent, receipt OCR, a warm
web dashboard with generative UI, a WhatsApp channel, and optional one-way
Google Sheets/Drive sync.

## Architecture

```
web/  React + Vite (no business logic — renders backend data + generative UI specs)
  │  /api/*
api/  Python 3.13 + FastAPI (all business logic)
  ├─ SQLite (api/data/expense.db)             — source of truth: transactions,
  │                                             categories, tax profiles, budgets,
  │                                             recurring rules, chat history
  ├─ Pi agent (pi-agent Python SDK) + Claude  — chat, extraction, tool calling
  ├─ neonize                                  — WhatsApp (QR pairing, messages)
  ├─ NVIDIA build.nvidia.com (PaddleOCR)      — receipt OCR
  └─ Google Drive + Sheets (optional)         — one-way sync mirror, never read back
```

- **Local-first**: SQLite is the single source of truth. Everything works
  without Google or NVIDIA keys (only OCR and sync need them).
- **Agent framework**: [Pi agent](https://pi.dev) via the Python SDK
  ([`pi-agent`](https://pypi.org/project/pi-agent/)). Claude is wired through
  Anthropic's native Messages API with a Claude Max OAuth token
  (`claude setup-token`) or an `ANTHROPIC_API_KEY`.
- **Channels**: web UI chat (SSE streaming, persistent sessions) and WhatsApp
  (scan QR in Settings). Both go through the same agent pipeline.
- **Tax back-calculation**: you enter the total paid; GST/QST/HST components
  are derived server-side from the category's taxable flag and the active tax
  profile (Quebec / Ontario / Alberta presets, editable).
- **Generative UI**: the agent's `render_ui` tool emits declarative component
  specs (metric / line / bar / pie / table); the frontend just renders them.
  On WhatsApp the agent answers with plain text instead.
- **Google sync**: optional, one-way (app → Sheet + Drive). Idempotent
  reconcile pushes pending rows hourly and after each write; deletions blank
  the mirrored row. The sheet is never read back.

## Features

- Dashboard: income/expenses/net metrics, 6-month trend, category pie,
  budgets rail with alerts at 90%, recent transactions, quick-add modal with
  live tax preview.
- Transactions: period/type/category/text filters, inline edit (taxes
  recomputed server-side), bulk delete/recategorize, CSV export, receipt
  lightbox.
- Chat: persistent sessions with history replay, generative UI, floating
  quick-chat bubble on every page.
- Settings: categories (percent counting formula, taxable flag, monthly
  budget), tax profiles, recurring rules, Google + WhatsApp connections,
  statement imports (CSV/XLSX/PDF) with agent parsing and duplicate
  pre-skipping.
- Recurring rules: weekly/biweekly/monthly templates auto-record on schedule
  (with catch-up for missed periods).
- WhatsApp: record by text or receipt photo, detailed confirmations, weekly
  summary every Sunday evening.

## Setup

### 1. Environment

```bash
cp .env.example .env
```

| Variable | Where to get it |
|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | from your Claude Max subscription: `claude setup-token` (or set `ANTHROPIC_API_KEY` from console.anthropic.com) |
| `NVIDIA_API_KEY` (optional) | build.nvidia.com → any model → Get API Key — only needed for receipt OCR |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` (optional) | Google Cloud Console → OAuth client (Web application). Enable the **Drive** and **Sheets** APIs and add `http://localhost:8000/api/google/callback` as an authorized redirect URI — only needed for sync |

### 2. Run with Docker (Makefile)

```bash
make start     # build + start backend (API) and console (web UI)
make logs      # follow logs (also: logs-api, logs-web)
make status    # container status
make stop      # stop containers, keep state
make cleanup   # remove containers, volumes (sessions!) and images
```

Web UI: http://localhost:5173 · API: http://localhost:8000

### 3. Run locally (dev)

```bash
# backend (needs libmagic: brew install libmagic / apt install libmagic1)
cd api && poetry install --no-root
poetry run uvicorn app.main:app --reload --port 8000

# frontend
cd web && npm install && npm run dev
```

### 4. Connect accounts (optional)

Open **Settings**:

1. **Google** — click *Connect Google*, approve. The app creates the
   "Expense Manager" spreadsheet and "Expense Receipts" Drive folder and
   starts mirroring transactions one-way.
2. **WhatsApp** — scan the QR with WhatsApp → Settings → Linked devices →
   Link a device. Then message yourself / the linked account to talk to the
   agent.

## Using it

- **Quick add**: + button on the dashboard — enter the total paid; the tax
  breakdown previews live and is computed server-side on save.
- **Chat**: type "spent $42.50 at Metro on groceries" or attach a receipt
  photo. The agent OCRs it (NVIDIA), saves the image locally, and records the
  transaction with back-calculated taxes.
- **Questions**: "what are my expenses this month?", "net income and the
  6-month trend" — answers stream back; on the web the agent renders
  charts/tables inline (generative UI).
- **Imports**: Settings → upload a bank statement (CSV/XLSX/PDF); the agent
  structures rows, duplicates are pre-skipped, you review and approve.
- **WhatsApp**: send text or a receipt photo; same agent, plain-text replies
  with the full breakdown, plus a weekly summary.

## API surface

| Endpoint | Purpose |
|---|---|
| `GET /api/dashboard?period=` | metrics, trend, by-category, budgets, recent |
| `GET/POST/PATCH/DELETE /api/transactions` (+`/bulk`, `/export.csv`) | transaction CRUD |
| `GET /api/receipts/{id}` | receipt image |
| `GET/POST/DELETE /api/categories` | categories + budgets + taxable |
| `GET/POST /api/tax-profiles` | tax profiles (activate) |
| `GET/POST/PATCH/DELETE /api/recurring` | recurring rules |
| `POST /api/imports` · `GET /{id}` · `POST /{id}/approve` | statement imports |
| `GET/POST/DELETE /api/chat/sessions` · `POST .../messages` (SSE) | chat sessions |
| `GET /api/sync/status` · `POST /api/sync/now` | Google sync |
| `GET/POST /api/whatsapp/accounts` · `DELETE /{id}` · `POST /{id}/refresh` | multi-account pairing, unpair, QR refresh |
| `GET /api/whatsapp/qr` · `/status` | legacy first-account QR + state |
| `GET /api/google/auth` · `/callback` · `/status` | Google OAuth |
| `GET /api/health` | liveness |

## Notes

- All state lives in `api/data/` (or the `api-data` volume in Docker):
  `expense.db` (SQLite), `receipts/` (images), WhatsApp session.
- Periods: `2026-06`, `last3`, `last6`, `ytd`, `YYYY-MM-DD:YYYY-MM-DD`;
  default is the current month.
- The OCR model defaults to `baidu/paddleocr`; switch with `NVIDIA_OCR_MODEL`.
- If OCR fails the receipt image is still saved and the agent asks you for
  the missing details.
- API errors always follow `{"error": {"code", "message"}}`.
