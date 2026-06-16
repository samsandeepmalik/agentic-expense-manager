# Expense Manager

> Local-first expense tracking with an AI agent — chat it, WhatsApp it, or snap a receipt.

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org)
[![Node 22](https://img.shields.io/badge/node-22-brightgreen.svg)](https://nodejs.org)

Income and expense tracker with an AI chat agent, receipt OCR, a warm web
dashboard, a WhatsApp channel, and optional one-way Google Sheets/Drive sync.
SQLite on your machine is the single source of truth — everything works with
just a Claude token; OCR and Google sync are opt-in add-ons.

![Quick-add demo — enter a total, taxes are derived server-side, the dashboard updates](docs/images/demo.gif)

*Enter what you paid; GST/QST are back-calculated server-side and the dashboard updates instantly.*

## Engineering highlights

- **Strict layering, zero frontend business logic.** Routes are thin (validate → call service → return); every service owns its own SQL with the DB connection as the first argument; the React app only renders what the API computes. All money math lives behind one `_compute` path on the server.
- **Server-side tax back-calculation.** You enter the total paid; GST/QST/HST are derived from the category's taxable flag and the active tax profile — computed once, server-side, and reused identically by the REST API, the chat agent, and the live quick-add preview (via a dedicated `/preview` endpoint, so the preview can never drift from what's recorded).
- **One agent, two channels.** A single Claude tool-calling agent backs both the web chat and WhatsApp off the *same* service layer — no duplicated business logic per channel.
- **Idempotent one-way Google sync.** Writes set a dirty flag; a debounced worker reconciles each profile's Sheet/Drive by mapping transaction id → row (missing rows are removed via `deleteDimension`, not blanked), with a frozen TOTALS row and per-profile configurable columns.
- **Crash-safe SQLite migrations.** Idempotent migrations with atomic table rebuilds that recover an orphaned scratch table from an interrupted prior run.
- **Profiles as full data partitions.** Each profile owns its transactions, categories, tax profile, Google Sheet, and Drive folder; every query is scoped to the active profile.
- **Tested, not asserted.** 200+ backend tests including an adversarial sync suite designed to *break* reconciliation, with dependency-injected seams (fake Sheets client, WhatsApp client factory) instead of internal patching.

See [docs/architecture.md](docs/architecture.md) for diagrams and the reasoning behind these decisions.

## Features

- **Profiles** — separate books per context (Personal, Incorporation, etc.); every part of the app (transactions, recurring rules, categories, tax profile, dashboard, imports, audit feed, Google sheet, and Drive folder) is strictly scoped to the active profile.
- **Dashboard** — income/expenses/net, 6-month trend, category pie, per-category budgets with 90% alerts, quick-add modal with live tax preview, optional receipt link, and a duplicate-add warning.
- **Tax back-calculation** — enter the total paid; GST/QST/HST are derived server-side from the category's taxable flag and the active tax profile (Quebec / Ontario / Alberta presets).
- **Chat agent** — natural-language entry ("spent $42 at Metro on groceries") or a receipt photo/PDF; streaming replies, inline generative charts/tables. Before recording, the agent confirms the target profile (when 2+ exist) and the category/sub-category.
- **WhatsApp** — pair your account (QR), then talk to the agent in your *"Message yourself"* chat; sender allowlist; weekly summary; strangers ignored.
- **Sub-categories** — one level of nesting under any category (e.g. Groceries → Produce); unique per (name, profile, parent); exposed in REST, the agent, and Settings.
- **Receipt OCR** — accepts images **and PDF files** (PyMuPDF renders each page to PNG, then OCRs); selectable provider: NVIDIA PaddleOCR, Claude vision, or OpenAI vision (Settings → Receipt OCR). Works from web chat and WhatsApp document messages.
- **Transactions** — filters, inline edit (taxes recomputed), bulk delete/recategorize, CSV export, receipt lightbox, loan flag, per-row notes; a dependent Category → Sub-category picker; expand any row to see the full breakdown (income/expense, base × category% = counted, every tax component); each tax component (GST, QST, …) shown on its own line.
- **Statement imports** — upload CSV/XLSX/PDF bank statements; the agent structures rows, then a review grid lets you pick the target profile, edit each row inline (category/sub-category, type, total, loan, notes, receipt link), and approve; likely duplicates are flagged and pre-skipped.
- **Duplicate warning** — adding the same transaction twice (same total + merchant + date, or the same receipt link) is flagged before it's saved, in the web quick-add and the chat agent alike. It's a warning, never a block — confirm "add anyway" and it records.
- **Recurring rules** — rent/salary templates auto-record on schedule; create, edit, and pause/resume them in Settings (or via the agent).
- **Tax profiles** — create/edit tax profiles and their components (rates) in Settings, or activate a preset (Quebec / Ontario / Alberta).
- **Audit trail** — every write and sync outcome logged (Settings → Activity).
- **Google sync** — optional one-way mirror (app → Sheet + Drive), debounced, idempotent, never read back; per-profile (each gets its own sheet + folder). Choose which columns appear and in what order (Settings), with a live preview; a frozen, always-visible TOTALS row; receipts shown as a readable name plus a Drive link; a "Counted %" column.

## Screenshots

[![Dashboard](docs/images/dashboard.png)](docs/images/dashboard.png)

| Transactions | Quick add (live tax preview) |
|---|---|
| [![Transactions](docs/images/transactions.png)](docs/images/transactions.png) | [![Quick add](docs/images/quick-add.png)](docs/images/quick-add.png) |

| Chat agent — generative UI | Settings (categories, budgets, sub-categories) |
|---|---|
| [![Generative UI](docs/images/chat-generative-ui.png)](docs/images/chat-generative-ui.png) | [![Settings](docs/images/settings.png)](docs/images/settings.png) |

> The charts and tables in the chat panel are generated on the fly by the agent (`render_ui`), not hard-coded.

## Quickstart

**Requires Docker + Docker Compose.** (For a non-Docker dev setup see [docs/development.md](docs/development.md).)

```bash
cp .env.example .env   # fill in at minimum a Claude credential (see below)
make start             # Docker: web → http://localhost:5173  api → :8000
```

Then open http://localhost:5173 and try the chat: *"spent $42 at Metro on groceries"*.

```bash
make stop              # stop containers, keep state (DB, WhatsApp session)
make logs              # follow all logs  (also: logs-api, logs-web)
make cleanup           # remove containers + images; data in ./data is PRESERVED
make cleanup-data      # DESTRUCTIVE: also deletes ./data — wipes DB and WhatsApp pairing
```

Local dev without Docker: `make dev-api` / `make dev-web` (see [docs/development.md](docs/development.md)).

## Setup & configuration

### LLM (Claude)

The agent requires one of:

| Var | How to get it |
|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | Run `claude setup-token` (requires a Claude Max subscription) — token starts with `sk-ant-oat01-` |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) — pay-per-use; **use this if you don't have a Claude Max subscription** |

Set just one of the two. Everything else (OCR provider, Google sync, WhatsApp) is optional and configurable in-app.

`CLAUDE_MODEL` is optional (default: `claude-sonnet-4-6`).

### WhatsApp

1. Settings → WhatsApp → click **Link account** → a QR code appears.
2. On your phone: WhatsApp → Settings → Linked devices → Link a device → scan within ~20 s. Use the **Refresh** button if the code expires.
3. Open **"Message yourself"** on your phone and text the agent (`spent 23.50 at Metro groceries`, a receipt photo, `what are my expenses this month?`, …).
4. To allow other senders, add their numbers under *Allowed senders*; everyone else gets silence.

### Google Drive/Sheets sync (optional)

The quickest path — no redirect URI registration needed:

1. [console.cloud.google.com](https://console.cloud.google.com) → create a project
2. **APIs & Services → Library** → enable **Google Drive API**
3. **APIs & Services → Library** → enable **Google Sheets API**
4. **OAuth consent screen** → User type: **External** → fill app name + email → **Test users** → add your Gmail → Save
5. **Credentials → Create credentials → OAuth 2.0 Client ID** → Application type: **Desktop app** → Create → **Download JSON**
6. **Settings → Google sync → JSON key** tab → paste the downloaded file → Connect

The app asks only for access to files it creates (`drive.file` scope) — your existing Drive files are untouched.  
Each profile gets its own Google Sheet and Drive subfolder, all nested under one root app folder: `Expense Manager / {profile} / sheet + {year} / receipts`.
You choose which columns the sheet has and in what order (Settings → Google sync, with a live preview); taxes are written as one column per component (e.g. GST, QST). Each tab has a frozen, always-visible TOTALS row, and receipts appear as a readable name alongside their Drive link. Changing the column set rewrites the sheet on the next reconcile.

For more detail and troubleshooting: [docs/google-drive-setup.md](docs/google-drive-setup.md).

### Receipt OCR (optional)

Choose the provider in **Settings → Receipt OCR**:

| Provider | Required var | Where to get it |
|---|---|---|
| NVIDIA PaddleOCR | `NVIDIA_API_KEY` | [build.nvidia.com](https://build.nvidia.com) |
| Claude vision | — | reuses the Claude token above |
| OpenAI vision | `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com) |

### Environment variable reference

| Var | Purpose | Required |
|---|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | Agent + Claude OCR | one of these two |
| `ANTHROPIC_API_KEY` | Agent + Claude OCR (fallback) | one of these two |
| `CLAUDE_MODEL` | Override model | no |
| `NVIDIA_API_KEY` | NVIDIA PaddleOCR | no |
| `OPENAI_API_KEY` | OpenAI vision OCR | no |
| `GOOGLE_CLIENT_ID` | Google sync | no (can paste in-app) |
| `GOOGLE_CLIENT_SECRET` | Google sync | no (can paste in-app) |
| `GOOGLE_REDIRECT_URI` | Google OAuth callback | no (default: `http://localhost:8000/api/google/callback`) |

## Security & privacy

- **Your data stays local.** All transactions, receipts, and chat history live
  in SQLite + files on your machine (the `./data/` folder). No telemetry, no
  accounts, no cloud backend.
- **What leaves the machine:** chat messages and receipt images are sent to
  the LLM/OCR provider you configure (Anthropic, and optionally NVIDIA or
  OpenAI). Google sync, if enabled, pushes transactions and receipts to *your*
  Sheet and Drive — one-way, never read back.
- **WhatsApp is locked down by default.** Only your own "Message yourself"
  chat is processed; every other sender is silently ignored unless you add
  them to the allowlist. Group chats are never processed.
- **Credentials** (Claude token, Google OAuth client + tokens) are stored
  locally — in `.env` and the SQLite settings table.

## Data & state

All persistent data lives in **`./data/`** at the repo root (`expense.db`,
`receipts/`, `whatsapp/`), bind-mounted into the container. This survives
`docker compose down -v`, volume prune, and `make cleanup`. Local dev
(`make dev-api`) uses the same `./data/` directory.

`make cleanup` removes only containers and images — your data is untouched.
`make cleanup-data` is the destructive command that deletes `./data/`
including the database and WhatsApp pairing (you will need to re-scan the QR
code after running it).

## Documentation

| I want to… | Read |
|---|---|
| Set up Google Drive/Sheets sync (step by step) | [docs/google-drive-setup.md](docs/google-drive-setup.md) |
| Set up WhatsApp pairing | [docs/whatsapp-setup.md](docs/whatsapp-setup.md) |
| Understand how it works (diagrams, data flow, design decisions) | [docs/architecture.md](docs/architecture.md) |
| Set up a dev environment, run tests, add a feature | [docs/development.md](docs/development.md) |
| Contribute a PR | [CONTRIBUTING.md](CONTRIBUTING.md) |
