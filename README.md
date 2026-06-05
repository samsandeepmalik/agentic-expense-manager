# Expense Manager

Income & expense management with a chat agent, receipt OCR, Google Sheets/Drive
storage, a web dashboard with generative UI, and a WhatsApp channel.

## Architecture

```
web/  React + Vite (no business logic — renders backend data + generative UI specs)
  │  /api/*
api/  Python 3.13 + FastAPI (all business logic)
  ├─ Pi agent (pi-agent Python SDK) + Claude  — chat, extraction, tool calling
  ├─ neonize                                  — WhatsApp (QR pairing, messages)
  ├─ NVIDIA build.nvidia.com (PaddleOCR)      — receipt OCR
  └─ Google Drive + Sheets                    — receipt images + transaction data
```

- **Agent framework**: [Pi agent](https://pi.dev) via the Python SDK
  ([`pi-agent`](https://pypi.org/project/pi-agent/)). Claude is wired through
  Anthropic's OpenAI-compatible endpoint using the SDK's
  `OpenAICompletionsProvider` registered as the `anthropic` provider.
- **Channels**: web UI chat (SSE streaming) and WhatsApp (scan QR on the
  Connect tab). Both go through the same agent pipeline.
- **Generative UI**: the agent's `render_ui` tool emits declarative component
  specs (metric / line / bar / pie / table — A2UI-style); the frontend just
  renders them. On WhatsApp the agent answers with plain text instead.
- **Data**: every transaction is a row in the `Transactions` tab of the
  "Expense Manager" Google Sheet, with the receipt's Drive link. Categories
  (+ percent counting formula, default 100%) live in the `Categories` tab.

## Setup

### 1. Environment

```bash
cp .env.example .env
```

| Variable | Where to get it |
|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com, or a token from your Claude Max subscription (`claude setup-token`) |
| `NVIDIA_API_KEY` | build.nvidia.com → any model → Get API Key |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google Cloud Console → OAuth client (Web application). Enable the **Drive** and **Sheets** APIs and add `http://localhost:8000/api/google/callback` as an authorized redirect URI |

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

### 4. Connect accounts

Open the **Connect** tab:

1. **Google** — click *Connect Google*, approve. The app auto-creates the
   "Expense Manager" spreadsheet and "Expense Receipts" Drive folder.
2. **WhatsApp** — scan the QR with WhatsApp → Settings → Linked devices →
   Link a device. Then message yourself / the linked account to talk to the
   agent.

## Using it

- **Chat tab**: type "spent $42.50 at Metro on groceries" or attach a receipt
  photo. The agent OCRs it (NVIDIA), stores the image in Drive, and appends a
  row (date, amount, GST, QST, total, counted amount, image link) to the sheet.
- **Questions**: "what are my expenses this month?", "categorize my expenses",
  "net income this month and the 6-month trend" — answers stream back, and on
  the web the agent renders charts/tables inline (generative UI).
- **Categories tab**: add income/expense categories and set each one's percent
  formula — e.g. Dining at 50 counts half of every dining total. Default 100%.
- **WhatsApp**: send text or a receipt photo to the linked account; same agent,
  plain-text replies.

## API surface

| Endpoint | Purpose |
|---|---|
| `POST /api/chat` (multipart, SSE) | chat with the agent, optional image |
| `GET /api/dashboard?months=6` | summary, trend, recent transactions |
| `GET/POST/DELETE /api/categories` | category + formula config |
| `GET /api/whatsapp/qr` · `/status` | pairing QR + connection state |
| `GET /api/google/auth` · `/callback` · `/status` | Google OAuth |
| `GET /api/health` | liveness |

## Notes

- Runtime state (Google tokens, WhatsApp session, sheet ids) lives in
  `api/data/` (or the `api-data` volume in Docker).
- The OCR model defaults to `baidu/paddleocr`; switch with `NVIDIA_OCR_MODEL`.
- If OCR fails the receipt image is still uploaded and the agent asks you for
  the missing details.
