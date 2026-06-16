# Chat Statement Import — Design

**Date:** 2026-06-15
**Status:** Approved design, pending implementation plan
**Scope:** v1 = web chat only (WhatsApp deferred — see §13)

## 1. Goal

In **web chat**, the user drops a statement file and the agent:

1. parses it into transaction rows,
2. proposes a category / sub-category structure,
3. **(confirm gate 1)** creates / reorganizes / deletes categories,
4. maps rows to categories,
5. **(confirm gate 2)** records the batch.

The agent **proposes, the user confirms at two gates** (category mutations; recording). Nothing commits transactions before gate 2. The whole flow reuses the existing import pipeline; transaction rows never travel through agent context.

## 2. Non-goals (v1)

- WhatsApp statement import (deferred — §13).
- Row-by-row editing inside chat (deep-link to the existing Import review UI for that).
- Intra-batch duplicate detection (existing dedup compares against persisted rows only — unchanged).
- Slice-on-demand row inspection ("show me the dining rows") — clean later add, not v1.

## 3. Architecture overview

```
web chat upload ──► routes/chat ──► file-type router
                                       │
   image ──────────────────────────────┘            → receipt OCR (today, unchanged)
   CSV / XLSX ─────────────────────────► statement path (always multi-row)
   PDF ──► extract_text ──► extractable? ─┬─ no  → receipt OCR path (scanned)
                                          └─ yes → parse_with_agent → rows≥2 statement
                                                                      rows==1 single receipt/txn
                                       │
                              imports.start_import()   (existing: parse + dedup + persist, status='review')
                                       │
                              import_id handed to the agent
                                       ▼
   agent loop:  get_import_summary → [manage_categories] → remap_import → approve_import
                                       ▼
                              create_transaction (existing) → audit + sync dirty flag
```

**Central invariant:** parsed rows live in the `imports` table for the whole flow. The agent references the import by `id` and works on **aggregates + a category mapping** — never the raw row payload. This is what keeps agent context bounded on large statements.

## 4. Chat ingestion & file-type routing

- Frontend chat file input `accept` widens to `image/*,application/pdf,.csv,.xlsx,.xls`. The upload field becomes a generic `file` (currently `image`); the chat route inspects MIME / extension.
- `routes/chat` branches:
  - **image/\*** → existing receipt path (unchanged).
  - **CSV / XLSX** → statement path (always treated as multi-row).
  - **PDF** → run existing `imports.extract_text`. The existing `len(text.strip()) < 40 → pdf_unreadable` check already separates scanned PDFs; those route to the receipt/OCR path. Text-extractable PDFs run `parse_with_agent`: **≥2 rows → statement; exactly 1 row → offered as a single receipt/transaction; 0 → parse_failed.**
- No new fragile heuristic: PDF classification reuses the LLM parser already used by imports. The agent's first message always states what it detected and accepts a one-word override ("treat as statement" / "that's one receipt").

## 5. Data model

`imports` already stores `rows` (JSON), `profile_id`, `status`, `error`. One additive, idempotent migration:

- `imports.channel TEXT DEFAULT 'import'` — distinguishes chat-originated imports from UI uploads in the audit feed.

No other schema change. (Migration goes in `db.init_db()` with a `tests/test_legacy_migration.py` case, per the existing convention.)

## 6. New agent tools

Three tools, registered in `agent/tools.py` (UI + WhatsApp-safe — logic does not depend on `render_ui`), each wrapped in `try/except → {"error": …}`, profile-scoped via `_resolve_pid`. Mentioned in `agent/prompts.py`.

### `get_import_summary(import_id)` — read-only
```json
{
  "import_id": 12, "profile": "Personal",
  "total_rows": 142, "duplicates": 8, "to_record": 134,
  "parsed_categories": [{"label": "Groceries", "count": 30, "resolved_category_id": 1}],
  "unresolved": [{"index": 5, "merchant": "UBER *EATS ...", "total": 24.10, "guessed": null}],
  "sample": [{"date": "2026-05-02", "merchant": "Metro", "total": 78.6, "category": "Groceries"}]
}
```
`unresolved` and `sample` are capped (e.g. 15 / 10). Feeds the compact review card.

### `remap_import(import_id, mapping)`
`mapping = [{ "match": {"merchant": "..."} | {"contains": "..."} | {"index": n}, "category_id": k }]`
- Applies deterministically to stored rows, re-resolves each row's `category_id` (via `transactions._resolve_category`), re-runs `dedup.flag_duplicates`, persists back to the import record.
- Returns updated counts + remaining `unresolved`. No LLM. Idempotent (re-running the same mapping is a no-op).
- Handles only the **delta**: `parse_with_agent` already best-guesses categories from the existing list, so remap covers unresolved merchants + rows affected by newly-created categories.

### `approve_import(import_id, indexes=null)`
- Thin wrapper over existing `imports.approve_import` (per-row fault tolerance, `external_ref = import:{id}:{index}` idempotency, audit).
- Refuses unless status is `review`. Returns `{created, failed: [{index, category, error}]}`.

Category create / reorganize / delete uses the existing `manage_categories` tool — no new category tool.

### Optional escape hatch (v1.1, not v1)
`recategorize_unresolved(import_id)` — re-runs the parser's category-assignment prompt over only the unresolved rows against the updated category list. Keeps mapping ergonomic for messy merchant strings.

## 7. End-to-end flow

1. Upload → router → `start_import` → `import_id`, status `review`.
2. Agent calls `get_import_summary` → "142 rows, 8 duplicates skipped. Can't place: Uber, Lyft, Hydro-Québec. Want *Transport › Rideshare* and *Utilities › Hydro*?"
3. **Gate 1:** on user OK → `manage_categories` (create / reparent / delete). Deletes require explicit confirmation in the message.
4. Agent calls `remap_import` with merchant→category_id mapping → server re-resolves + re-dedups → returns leftovers.
5. **Gate 2:** agent renders the compact review card (counts + mapping + ~10-row sample + a deep link to the Import UI) → on user OK → `approve_import`.
6. Reports: "Recorded 134, skipped 8 duplicates, 0 errors." Sync fires via the existing dirty-flag path.

## 8. Confirm gates & UX

- Two explicit gates. The agent must not call mutating `manage_categories` or `approve_import` without an affirmative in the immediately preceding user turn (enforced via `prompts.py` instruction).
- **Review card:** `render_ui` table (UI channel) showing the ~10-row sample + category mapping + counts. Compact by design — samples, never dumps the full set.
- **Deep edits:** "edit individual rows" → link to the existing Import review screen for the same `import_id` (row-level editing already exists there).
- **Profile:** reuse the existing rule — with 2+ profiles, the agent confirms the target profile before `start_import` / recording. The import carries its own `profile_id`; the active profile is not changed.

## 9. Error handling

- Parse failure → import `status='failed'`; agent says so, suggests a CSV export (mirrors today).
- PDF misclassified → user override flips the path; nothing committed before gate 2.
- Unresolved categories at approve → those rows fail per-row (existing fault tolerance), reported in `failed`; good rows still record.
- All tool bodies degrade to `{"error": …}` so the agent recovers conversationally.

## 10. Idempotency, dedup, audit

- `external_ref = import:{id}:{index}` → re-approve never double-inserts.
- `dedup.flag_duplicates` (pure reads — verified idempotent) runs at parse and again after each `remap_import`.
- Audit rows: `import_uploaded` (`channel='chat'`), `import_approved`, plus the usual category/transaction audit. Nothing bypasses `create_transaction`.

## 11. Reuse vs new

- **Reuse:** `extract_text`, `parse_with_agent`, `dedup.flag_duplicates`, `imports` table, `imports.approve_import`, `transactions.create_transaction`, `transactions._resolve_category`, `manage_categories`, profile scoping, sync.
- **New:** chat file-type router + PDF classification (via existing parser); one migration column (`imports.channel`); three agent tools; one `render_ui` review card; `prompts.py` updates; frontend `accept` widening + generic file field.

## 12. Testing (TDD)

- **Unit:** PDF classification (extractable/scanned/multi/single fixtures using the existing extract+parse path); `remap_import` (merchant / contains / index matching, re-dedup, idempotency); `get_import_summary` shape + caps; `approve_import` gate + fault tolerance (extend existing for `channel='chat'`).
- **Integration:** chat route file routing (image→receipt, csv→statement, pdf→classify) via `TestClient`.
- **Agent:** tool registration + profile scoping; confirm-gate behavior (record only after affirmation).
- **Migration:** `imports.channel` upgrade case in `test_legacy_migration.py`.

## 13. Deferred: WhatsApp parity

WhatsApp cannot carry statements today, for two concrete reasons:

1. `channels/whatsapp.py` `_handle_message` only downloads `documentMessage` mimes containing `pdf` or starting `image/` — **CSV/XLSX are dropped.**
2. Documents are passed as `image_bytes` into the shared handler contract `(chat_id, text, image_bytes, image_mime)` → the **receipt** pipeline. There is no filename / generic-file slot.

Enabling WhatsApp requires widening the document mime filter **and** changing the `MessageHandler` contract to carry a generic document (`filename + bytes + mime`) — a cross-cutting change. Deferred to a follow-up so v1 stays high-confidence and web-scoped.

## 14. Risks

- **Context blow-up** — mitigated by id-based, server-side rows (the central decision).
- **Mapping expressiveness** — start with merchant-exact + substring + index; the v1.1 `recategorize_unresolved` escape hatch handles messy merchant strings if needed.
- **PDF classification** — reuses the existing LLM parser + the existing scanned-PDF gate, rather than a bespoke heuristic.

## 15. Phasing

1. Chat ingests CSV/XLSX → `start_import` + `get_import_summary` + `approve_import` tools (uses parse-time guesses; no remap yet).
2. `remap_import` + `manage_categories` organize-then-record loop + both gates.
3. PDF classification + single-receipt fallback.
4. `render_ui` review card + Import-UI deep link.
5. (v1.1) `recategorize_unresolved`; (later) WhatsApp parity.
