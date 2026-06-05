# Expense Manager

Local-first income & expense management with an AI chat agent, receipt OCR,
a warm web dashboard with generative UI, a WhatsApp channel, and optional
one-way Google Sheets/Drive sync.

SQLite is the single source of truth — everything works offline with just a
Claude token. OCR and Google sync are optional add-ons.

**Docs:** [Architecture](docs/architecture.md) · [Development](docs/development.md) · [Design review](docs/arch-review-2026-06-05.md)

## Features

- **Dashboard** — income/expenses/net metrics, 6-month trend, category pie,
  per-category budgets with 90% alerts, quick-add modal with live tax preview.
- **Tax back-calculation** — enter the total paid; GST/QST/HST are derived
  server-side from the category's taxable flag and the active tax profile
  (Quebec / Ontario / Alberta presets).
- **Chat agent** — "spent $42.50 at Metro on groceries" or a receipt photo;
  persistent sessions, streaming replies, inline generative charts/tables.
- **WhatsApp** — pair your account (QR), then talk to the agent in your own
  *"Message yourself"* chat. Multi-account, sender allowlist, weekly summary.
  Strangers are ignored.
- **Receipt OCR** — selectable provider: NVIDIA PaddleOCR, Claude vision, or
  OpenAI vision (Settings → Receipt OCR).
- **Transactions** — filters, inline edit (taxes recomputed), bulk
  delete/recategorize, CSV export, receipt lightbox.
- **Imports** — upload bank statements (CSV/XLSX/PDF); the agent structures
  rows, duplicates pre-skipped, you review and approve.
- **Recurring rules** — rent/salary templates auto-record on schedule.
- **Audit trail** — every write and sync outcome logged (Settings → Activity).
- **Google sync** — optional one-way mirror (app → Sheet + Drive), debounced,
  idempotent, never read back.

## Quickstart

```bash
cp .env.example .env          # set CLAUDE_CODE_OAUTH_TOKEN (claude setup-token)
make start                    # Docker: web http://localhost:5173 · api :8000
```

| Env var | Needed for | Where to get it |
|---|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | agent + Claude OCR | `claude setup-token` (Claude Max) — or `ANTHROPIC_API_KEY` |
| `NVIDIA_API_KEY` | PaddleOCR provider | build.nvidia.com (optional) |
| `OPENAI_API_KEY` | OpenAI OCR provider | platform.openai.com (optional) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Sheets/Drive sync | Google Cloud Console OAuth client; redirect URI `http://localhost:8000/api/google/callback` (optional) |

```bash
make logs / logs-api / logs-web   # follow logs
make stop                         # stop, keep state
make cleanup                      # remove containers + volumes (sessions!)
```

Local dev without Docker: see [docs/development.md](docs/development.md).

## Connect WhatsApp

Settings → WhatsApp → scan QR (WhatsApp → Settings → Linked devices → Link a
device — scan within 20s). Once connected, open **"Message yourself"** on your
phone and text the agent: `spent 23.50 at Metro groceries`, a receipt photo,
or `what are my expenses this month?`. Approve other people's numbers under
*Allowed senders*; everyone else gets silence.

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
| `GET/POST /api/settings/ocr` | OCR provider selection |
| `GET /api/sync/status` · `POST /api/sync/now` | Google sync |
| `GET/POST /api/whatsapp/accounts` · `DELETE /{id}` · `POST /{id}/refresh` | account pairing, unpair, QR refresh |
| `GET/POST/DELETE /api/whatsapp/allowed` | sender allowlist |
| `GET /api/google/auth` · `/callback` · `/status` | Google OAuth |
| `GET /api/audit` | activity feed (writes + sync outcomes) |
| `GET /api/health` | liveness |

Errors always follow `{"error": {"code", "message"}}`.

## Periods

`?period=` accepts `2026-06`, `last3`, `last6`, `ytd`,
`YYYY-MM-DD:YYYY-MM-DD`; default = current month.

## State

Everything lives in `api/data/` (Docker: `api-data` volume): `expense.db`
(SQLite), `receipts/` (images), `whatsapp/` (session DBs per account).
`make cleanup` wipes it all — including WhatsApp pairing.
