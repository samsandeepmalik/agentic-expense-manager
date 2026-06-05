# Architecture & Design Review — Expense Manager

Scope: read-only review of `/Users/sandeepmalik/Documents/projects/expense_management`. All claims cite `file:line`.

## Summary

The codebase is in genuinely good shape for a recently-rewritten local-first app: clean routes → services → db layering, a thin tool layer over business logic, a well-isolated Anthropic provider, and money math centralized in one place. The "patch on patch" worry is real but contained — it concentrated almost entirely in `channels/whatsapp.py` and the `settings` table, which has become an untyped junk drawer. The single most important structural problem is that the **channel abstraction does not exist yet** (adding Telegram/iMessage means duplicating the registry/manager/gating machinery). The most urgent *correctness* problem is a **silent gap in sync triggering**: agent-created and recurring transactions never fire the background Google push.

## Architecture findings

**Layering is sound.** Routes are thin; services own SQL and money math; `tools.py:139-312` are thin async wrappers that `asyncio.to_thread` into the sync service functions. `transactions.py:29-33` (`_compute`) is the single source of tax/counted math, reused by create, update, and bulk recategorize. Strongest part of the design — leave untouched.

**The channel abstraction does not exist (top extensibility risk).** There is no channel interface. `MessageHandler` (`channels/whatsapp.py:35`) is the only seam, and it is WhatsApp-shaped (`chat_id, text, image_bytes, image_mime`). Pairing, QR, gating, allowlist, reply-JID tracking, sent-ID loop prevention, and summary delivery all live inside `WhatsAppManager`/`WhatsAppRegistry`. `main.py` wires WhatsApp specifically (`main.py:14,58,68,70`) and the scheduler calls `whatsapp.send_weekly_summary()` directly (`main.py:58`). WhatsApp logic is contained today only because there is exactly one channel — containment is incidental, not structural.

**Identity / JID normalization is mostly contained, with one inconsistency.** Centralized via `_digits()` (`whatsapp.py:40-42`) and `Jid2String(JIDToNonAD(...))` (`:132,236-237`); `@lid` self-chat handling is documented and tested (`should_process` at `:45-64`, tests at `test_whatsapp_qr.py:105-127`). Inconsistency: the allowlist matches on `_digits()` (`:64`) but `whatsapp_summary_chat` stores the full normalized JID (`:270`) and `send_weekly_summary` matches it against `_reply_jids` keys (`:365`). Two identity representations coexist.

**config.py env/settings split is mostly principled but leaky.** `google_drive_folder_id` and `google_spreadsheet_id` exist in **both** `config.py:40-41` (env) and the settings table (`sync.py:29`, `google_client.py:127`), resolved differently: `ensure_drive_folder` prefers env (`google_client.py:127`), `_ensure_spreadsheet` uses only DB (`sync.py:29`). Two sources of truth.

**settings table is a typed-value junk drawer (top design-debt item).** `set_setting/get_setting` (`db.py:133-143`) is a generic JSON KV store now holding `ocr_provider`, `whatsapp_allowed_senders`, `whatsapp_summary_chat`, `google_tokens`, `drive_folder_id`, `spreadsheet_id`, and unbounded `receipt_link_{txn_id}` keys (`sync.py:105,113`). The `receipt_link_{id}` pattern is the worst offender: one row per synced transaction with an image, never cleaned up on delete (`transactions.py:110`), turning a config table into a side-table that should be a transactions column. Key names are string literals scattered across 6 files.

**Scheduler design is fragile.** `_scheduler_loop` (`main.py:42-62`) is a single `while True: ... sleep(3600)` task. The Sunday-18:00 summary fires only if a tick lands in that hour with `last_summary_day` tracked in memory (`:51,56-59`) — a restart resets it (double-send or skip). All three jobs (recurring, reconcile, summary) share one failure domain; a fixed-hour sleep drifts when reconcile is slow.

**SQLite connection-per-request + WAL is fine, but pragmas-per-connection are wasteful and there's no busy_timeout.** `get_db()` (`db.py:117-130`) re-sets `journal_mode=WAL` and `foreign_keys=ON` on every call (`:121-122`); WAL is a persistent DB property. There is no `busy_timeout`, so concurrent writers (scheduler reconcile mid-table-scan + a user write + an agent write) can hit `database is locked` immediately instead of waiting.

**Sync triggering has a silent correctness gap (top correctness risk).** `create_transaction` always sets `sync_status='pending'` (`transactions.py:52`), but `schedule_push` is called **only** from the REST route (`routes/transactions.py:102`). The agent tool path (`tools.py:139-154`) and the recurring path (`recurring.py:69`) call `txn_svc.create_transaction` directly and never schedule a push; `update_transaction` (`transactions.py:101`) also resets to `pending` without scheduling. So transactions recorded via chat/WhatsApp — the primary UX — stay `pending` up to ~60 minutes until the next hourly reconcile, and only if Google is connected and the tick succeeds. Failures are swallowed (`sync.py:128-132`).

**Fire-and-forget sync swallows everything and has no debounce.** `schedule_push` → `_safe_reconcile` (`sync.py:117-132`) spawns a detached task that logs-and-drops all failures; `reconcile` does whole-table scans with row-by-row Sheets calls (`sync.py:72-100`); each `schedule_push` enqueues a full reconcile (`:125`), so a burst of writes triggers N overlapping reconciles racing on the same `sync_status` updates.

## Design-debt inventory

**Fine — leave alone:**
- `transactions.py` money math and `_compute` reuse — central, correct.
- `anthropic_provider.py` — protected, isolated, well-structured event translation (`:188-287`).
- `services/ocr.py` and the `vision.py` dispatch wrapper — clean provider strategy (`vision.py:52-58`), good test seam (`test_vision.py:5-30`).
- `should_process` gating (`whatsapp.py:45-64`) — pure, fully unit-tested, correct `@lid` handling. The part feared as patchy is the opposite.
- `errors.py` contract — consistent `{error:{code,message}}`.
- Frontend API client (`web/src/api.ts`) — tidy, typed; SSE parser (`:55-78`) correct.

**Tolerable — monitor, don't rush:**
- WhatsApp registry/manager lifecycle (`whatsapp.py:67-371`). Start/stop/refresh has race surface (`refresh_qr` does `stop()` then `start()` at `:193-194`; `start()` early-returns if `_connect_task` is set at `:94`), but single-user, low-churn, failure mode is "re-scan QR."
- Sent-ID `deque(maxlen=256)` loop prevention (`whatsapp.py:83,279-283`) — in-memory, bounded; ample for a personal assistant.
- `_handle_whatsapp_message` (`main.py:27-39`) — works, but is the only stand-in for a real channel pipeline.

**Needs restructuring:**
- settings-table sprawl, especially unbounded `receipt_link_{id}` (`sync.py:105-113`) → promote to a `transactions.receipt_link` column or `sync_state` table.
- Missing channel abstraction (incidental containment only).
- Sync-trigger gap (`schedule_push` missing on agent/recurring/update paths).
- Observability: no structured event/audit trail. `logging.basicConfig(level=INFO)` (`main.py:23`) is the whole strategy; channel logs are positional free-text (`whatsapp.py:248-251`); no request IDs, no per-transaction sync audit, no record of which channel/sender created which transaction beyond the `source` column. An append-only audit of channel-originated writes is the highest-value missing capability for a money app.

## Testability

Mixed seam quality. **Good:** `should_process` is pure and tested directly (`test_whatsapp_qr.py:105`); `vision.extract_text` dispatch is tested by monkeypatching the three `_*_extract` functions (`test_vision.py:16-18`) — clean because the strategy boundary is a real function boundary. **Fragile:** tests monkeypatch `WhatsAppManager.start/stop` (`test_whatsapp_qr.py:70-71,95-97`) because the neonize client is constructed inline inside `start()` (`whatsapp.py:106`) with no injectable factory — so tests verify registry bookkeeping but can never exercise the real start path. Injecting a `client_factory` into `WhatsAppManager.__init__` converts a method-replacement seam into a dependency-injection seam. `runtime.Session` builds its DB-backed history inline (`runtime.py:72-83`), making the event-bridge logic (`runtime.py:101-172`) hard to unit test; no coverage visible.

## Frontend

- API client (`api.ts`): clean, typed; SSE framing parser (`:71-77`) handles partial frames correctly.
- Query invalidation is coarse but mostly correct. Smell: `dashboard` is keyed `["dashboard", period]` (`Dashboard.tsx:22`) but **nothing invalidates it** after a chat-driven `record_transaction` or `QuickAdd` — the dashboard won't refresh after the agent records a transaction. This mirrors the backend sync gap: agent writes don't propagate to dependent views.
- Component boundaries reasonable; `Chat.tsx` cleanly separates UI sessions from read-only WhatsApp sessions (`:9-13,55`).
- Minor: WhatsApp accounts poll every 4s (`Settings.tsx:54`) and WA chat sessions every 5s (`Chat.tsx:11`) unconditionally while mounted — fine for single-user.

## Error handling risks

- Broad `except Exception` with friendly degradation pervades `tools.py` (`:153,165,177,198,212,228,236`), returning `{"error": ...}` strings to the model. Deliberate and good for agent UX, but it bypasses the `errors.py` typed contract — an `AppError("category_not_found")` from `transactions.py:38` is flattened to a generic string at `tools.py:153`, losing the structured code.
- `_handle_whatsapp_message` (`main.py:37-39`) and the chat stream (`chat.py:67-70`) catch-all to avoid 500-ing mid-stream — correct.
- Genuinely swallowed: `_safe_reconcile` (`sync.py:128-132`) and the detached `schedule_push` task (`:125`) — failures vanish into logs with no surfaced state beyond the `pending` count in `status()` (`sync.py:137-139`). Combined with the trigger gap, a user can believe a transaction synced when it is silently stuck.

---

## Top 5 architectural risks (ranked)

1. **Sync-trigger gap on agent/recurring/update paths** (`tools.py:139`, `recurring.py:69`, `transactions.py:101` never call `schedule_push`; only `routes/transactions.py:102` does). *Failure scenario:* a user records 20 expenses via WhatsApp over an afternoon; all sit `pending`; the 18:00 reconcile throws once (network blip — `sync.py:131` logs and drops); nothing retries until the next hour; the user opens the Sheet expecting today's data and it's empty, with no error surfaced anywhere. Highest impact because it's silent and hits the primary UX.

2. **No channel abstraction** (`MessageHandler` at `whatsapp.py:35`; `main.py:14,58,68` hardwire WhatsApp). *Failure scenario:* adding Telegram requires copy-pasting ~370 lines of registry/manager/gating/reply-tracking, and the summary scheduler (`main.py:58`) only knows WhatsApp, so Telegram users silently get no weekly summary. This is the "patch on patch" risk for the *next* feature.

3. **settings-table sprawl, especially unbounded `receipt_link_{id}`** (`sync.py:105-113`). *Failure scenario:* thousands of `receipt_link_*` rows accumulate; a deleted transaction (`transactions.py:110`) leaves its link row orphaned forever; debugging "why is settings huge / why does this stale link exist" becomes archaeology because keys are string literals across 6 files.

4. **Scheduler reliability and in-memory state** (`main.py:42-62`). *Failure scenario:* the app restarts on deploy; `last_summary_day` resets (`:51`); a Sunday-evening restart double-sends or skips the weekly summary; a slow reconcile drifts the hourly cadence. Three unrelated jobs share one failure domain.

5. **SQLite write contention without `busy_timeout`** (`db.py:117-130`). *Failure scenario:* a user QuickAdds while the hourly reconcile holds the write lock mid-table-scan (`sync.py:74-91`) and an agent tool writes concurrently (`tools.py:152`) — one gets an immediate `database is locked` instead of waiting, surfacing as a random tool error or 500. Rare today (single user), unhandled.

## Prioritized refactor roadmap

Four coherent, independent moves, ordered by value-to-effort.

**Move 1 — Close the sync loop (effort: S, ~half day).** Make `create_transaction`/`update_transaction` (the service) the place that schedules the push, not the route — e.g. a `mark_dirty(txn_id)` hook, or have the recurring runner and agent tool call `schedule_push` like the route does. Add `PRAGMA busy_timeout=5000` in `get_db()` and stop re-setting `journal_mode` per connection. Add debounce/coalesce so a burst of writes triggers one reconcile, not N. On the frontend, invalidate the `dashboard` query after agent writes (the mirror bug). *Unlocks:* correct, predictable sync regardless of write origin; eliminates the silent-stale-Sheet bug and the lock race. **Do this first.**

**Move 2 — Extract a Channel protocol (effort: M, ~1-2 days).** Define a minimal `Channel` interface (`start/stop`, `status/info`, `send(chat_id, text)`, an injected pipeline that yields normalized `(chat_id, text, image_bytes, image_mime)` and consumes a reply) plus a `ChannelRegistry` base. Refactor `WhatsAppManager`/`WhatsAppRegistry` to implement it — mostly moving existing code behind an interface, *not* rewriting, so the protected neonize wiring (`whatsapp.py:111-163`) stays byte-for-byte. Move `send_weekly_summary` to iterate all connected channels; make `main.py` iterate channels instead of naming `whatsapp`. While there, inject a `client_factory` into `WhatsAppManager.__init__` (fixes the test seam). *Unlocks:* Telegram/iMessage become "implement the protocol"; weekly summary works for all channels; honest test seam. Don't do speculatively if a second channel isn't on the roadmap.

**Move 3 — Promote sync state out of the settings junk drawer (effort: S-M, ~1 day).** Add a `receipt_link` column to `transactions` (or a small `sync_state(txn_id, receipt_link, sheet_row)` table) and migrate `receipt_link_{id}` off settings (`sync.py:105-113`). Consolidate `spreadsheet_id`/`drive_folder_id` to one source of truth (drop env duplication in `config.py:40-41` or make DB authoritative everywhere). Centralize remaining settings keys as named constants. *Unlocks:* bounded settings table, automatic cleanup on delete (FK cascade), one place to reason about sync state.

**Move 4 — Structured logging + channel-write audit trail (effort: S-M, ~1 day).** Replace `logging.basicConfig` (`main.py:23`) with structured logging (request/message IDs) and add an append-only audit row for every channel-originated transaction write (who/which channel/which message → which txn id). *Unlocks:* the ability to answer "did this WhatsApp message create a transaction and did it sync?" without grepping free-text logs — essential for a money app and a prerequisite for trusting the agent write path.

## Explicitly do NOT refactor

- `agent/anthropic_provider.py` — protected (Claude Max OAuth quirk), verified-live, well-structured. Event translation (`:188-287`) is correct and isolated.
- `services/ocr.py` and the neonize event wiring inside `whatsapp.py:111-163` (`client.event.qr` callback; `ConnectedEv/PairStatusEv/LoggedOutEv/MessageEv` handlers) — verified-live, behavior must not change. Move 2 relocates the surrounding class but leaves these handler bodies untouched.
- `should_process` gating (`whatsapp.py:45-64`) — pure, fully tested, correct `@lid` handling. The "patch on patch" fear does not apply here.
- `transactions.py` money math (`_compute` and `back_calculate` usage) — central, correct, reused.
- QR TTL logic (`whatsapp.py:201-219`) and its tests — encodes a real WhatsApp constraint (20s rotation), verified-live.
- Frontend `api.ts` SSE parser and typed client — correct, low-churn.

Files most relevant to the action items: `api/app/services/sync.py`, `api/app/agent/tools.py`, `api/app/services/recurring.py`, `api/app/db.py`, `api/app/channels/whatsapp.py`, `api/app/main.py` (all under `/Users/sandeepmalik/Documents/projects/expense_management/`).