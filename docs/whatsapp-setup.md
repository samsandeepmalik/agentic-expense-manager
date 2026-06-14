# WhatsApp Setup

Expense Manager can connect to your WhatsApp account so you can record expenses, upload receipts, and query your finances by messaging yourself — no app switching needed.

This uses the [neonize](https://github.com/krypton-byte/neonize) WhatsApp client, which pairs like a linked device (similar to WhatsApp Web).

## Prerequisites

- A smartphone with WhatsApp installed
- The app running (`make start`)

## Pairing

1. Open **Settings → WhatsApp → Link account**.
2. A QR code appears in the browser.
3. On your phone: **WhatsApp → Settings → Linked devices → Link a device** → point camera at the QR code.
4. Scan within ~20 seconds. If the code expires, click **↻ Refresh QR** and scan the new one.
5. The status indicator turns green (**connected**) once pairing succeeds.

> QR codes are single-use and expire ~20 seconds after generation. If you see `qr_expired`, click **Refresh QR** — do not reload the page.

## Sending messages

Open **"Message yourself"** on your phone (your own contact at the top of the chat list) and text the agent:

```
spent 42 at Costco groceries
```
```
what are my expenses this month?
```
```
[send a receipt photo]
```
```
[send a PDF receipt as a document]
```

The agent responds in the same chat. It has the same capabilities as the web chat (record transactions, query history, upload receipts, show summaries).

### Receipt photos and PDF receipts

The agent accepts both **image messages** (photo receipts) and **PDF document messages** sent via WhatsApp. Both are processed through the same receipt pipeline:

- **Images**: OCR'd directly and matched to a transaction.
- **PDFs**: each page is rendered to a PNG (up to 10 pages), OCR'd page by page, then matched to a transaction. A preview image of the first page is stored locally for the web UI.

Simply send the receipt — the agent will extract the date, merchant, and total and ask you to confirm the profile and category before saving.

## Allowing other senders

By default only your own self-chat is processed. To allow a family member or business partner:

1. **Settings → WhatsApp → Allowed senders → add their number** (international format, e.g. `+15145551234`).
2. They can now message the app from their WhatsApp — all other senders are silently ignored.

## Weekly summary

Every Sunday evening the app sends a weekly spending summary to your self-chat automatically. No configuration needed.

## Unpairing

- **Settings → WhatsApp → Unpair** removes the linked device from the app side.
- To also remove it from your phone: **WhatsApp → Settings → Linked devices → [device] → Log out**.

## Troubleshooting

### QR code shows `qr_expired`
The QR code was not scanned within ~20 seconds. Click **↻ Refresh QR** to generate a new one and scan immediately.

### Status shows `disconnected` after pairing
WhatsApp may have logged out the linked device (this happens if the phone has no internet for several days, or if you log out all devices). Re-pair using the QR flow above.

### Messages are ignored
- If you are messaging from your own self-chat and messages are ignored, check `make logs-api` for `should_process` gate decisions.
- If you added another sender and they are ignored, verify the number in **Allowed senders** matches exactly what WhatsApp reports (international format, no spaces).

### WhatsApp session corrupted
If the pairing session becomes corrupted, you need to wipe the WhatsApp session data and re-pair:

1. `make stop` — stop containers.
2. Delete (or move) `./data/whatsapp/` — this removes the session without touching your transactions.
3. `make start` then re-pair via the QR flow.

> `make cleanup` stops containers and removes the Docker images but **preserves `./data`** (your database and receipts are safe). `make cleanup-data` also removes `./data` — **this deletes all transactions and receipts**. Back up `./data/expense.db` before running it.
