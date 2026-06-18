# Contributing

[README](README.md) · [Architecture](docs/architecture.md) · [Development](docs/development.md) · [Issues](https://github.com/samsandeepmalik/agentic-expense-manager/issues)

Expense Manager is a local-first expense tracker with a Claude agent backend, FastAPI + SQLite core, React/Vite frontend, and optional WhatsApp and Google integrations. Contributions are welcome — read this guide before opening a PR.

---

## Priorities

Contributions are valued in this order:

1. **Bug fixes** — incorrect behavior, data corruption, crashes. Always top priority.
2. **Security** — auth gaps, injection vectors, credential exposure.
3. **Test coverage** — especially for migration paths and edge cases in sync/dedup.
4. **New integrations** — channels (Telegram, Signal), OCR providers, export formats.
5. **Documentation** — fixes, clarifications, missing examples.
6. **Refactors** — only when a concrete correctness fix requires restructuring, or a maintainer has explicitly asked.

---

## What not to contribute

- **Refactor-only PRs** — not accepted unless a maintainer has explicitly asked.
- **Frontend business logic** — the app enforces zero frontend math. PRs that compute money, taxes, or budgets in the React layer will be reverted.
- **Features the agent already covers** — if the chat agent can reach the data via an existing tool, extend the prompt or the tool rather than building a new code path.

---

## Dev setup

Follow [docs/development.md](docs/development.md) for prerequisites, how to run locally (Docker or hot-reload), and how to run the test suite.

---

## Deciding what to build

### Endpoint vs agent tool vs channel?

**Add an HTTP endpoint** when the feature is user-initiated from the web UI, needs a pydantic request/response shape, or is consumed by a non-agent surface (dashboard, settings, imports grid). Pattern: service function in `services/` → router in `routes/` → register in `main.py` → types + fetch in `web/src/api.ts`.

**Add an agent tool** (`agent/tools.py`) when the capability must be reachable by natural language through web chat or WhatsApp. Tools are thin async wrappers — no SQL, no business logic — they call the same services the HTTP routes use. Wrap the body in `try/except` and return `{"error": ...}` for friendly degradation.

**Add a channel** (`channels/`) when connecting a new messaging platform (Telegram, Signal, IRC, etc.). Implement `BaseChannelRegistry` (`set_handler / start / list_accounts / send_weekly_summary`), normalise messages to `(chat_id, text, image_bytes, image_mime)`, and append to `CHANNELS` in `main.py`. The agent, tools, and services all reuse unchanged.

---

## Workflow

- **TDD** — write the failing test first, watch it fail, then implement.
- **Never commit on red.** Both must be green before you push:
  ```bash
  cd api && poetry run pytest -v   # backend suite
  cd web && npm run build          # TypeScript check + Vite build
  ```
- Keep commits focused. One logical change per commit; explain *why* in the message body, not just *what*.
- Schema changes require an idempotent migration block in `db.init_db()` (via `PRAGMA table_info` check → `ALTER TABLE`) plus a migration test in `tests/test_legacy_migration.py`.
- All transaction writes go through `services/transactions.create_transaction` — audit row and sync dirty-flag fire automatically. Never INSERT into `transactions` directly.

---

## Protected code

Do not change the semantics of these files without explicit test coverage for the behavior being changed:

| File | Why |
|---|---|
| `app/agent/anthropic_provider.py` | Claude Max OAuth quirk (Bearer + beta header + Claude Code system block), verified live |
| `app/services/ocr.py` | NVIDIA NIM client including large-image asset upload, verified live |
| `channels/whatsapp.should_process` | WhatsApp `@lid` self-chat detection and loop-prevention — fully unit-tested semantics |
| `services/transactions._compute` | Single source of money math — all tax derivation |

---

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>
```

| Type | When to use |
|---|---|
| `fix` | Bug fix |
| `feat` | New feature or capability |
| `test` | Test-only change |
| `docs` | Documentation only |
| `refactor` | Code restructuring, no behavior change |
| `chore` | Build, deps, CI |

Scopes: `transactions`, `sync`, `agent`, `chat`, `whatsapp`, `profiles`, `imports`, `ocr`, `ui`, `db`, `tests`.

Examples:

```
fix(sync): prevent deleteDimension crash on empty sheet
feat(agent): add remap_import tool for statement row remapping
test(profiles): cover prompt_loan migration and PATCH route
docs(architecture): add Boundaries section
```

---

## Before you PR

- Tests pass (`pytest -v` + `npm run build` both green).
- You ran the changed code path in the actual running app — the test suite alone is not sufficient.
- PR description explains the *why*: what problem does this solve and why is this the right approach?
- New endpoints follow the route → service → `api.ts` pattern in [docs/development.md](docs/development.md).
- New agent tools wrap their body in `try/except` and return `{"error": ...}` on failure.
- Diffs are focused — one logical change per PR; don't bundle unrelated cleanup with feature work.

---

## Community

- **Issues:** [github.com/samsandeepmalik/agentic-expense-manager/issues](https://github.com/samsandeepmalik/agentic-expense-manager/issues) — bugs, feature requests, questions
- **Discussions:** architecture proposals and design questions welcome via GitHub Issues with the `discussion` label

---

## License

By contributing you agree your work will be licensed under the [MIT License](LICENSE).
