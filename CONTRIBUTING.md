# Contributing

Thanks for your interest in Expense Manager. This is a local-first expense
tracker with an AI agent backend, React frontend, and optional WhatsApp /
Google integrations. Contributions are welcome — please read this guide before
opening a PR.

## Dev setup

Follow [docs/development.md](docs/development.md) for prerequisites, how to
run the app locally (Docker or hot-reload), and how to run the test suite.

## Workflow rules

- **TDD** — write the failing test first, watch it fail, then implement.
- **Never commit on red.** Both of these must be green before you push:
  ```bash
  cd api && poetry run pytest -v   # backend suite
  cd web && npm run build          # TypeScript check + Vite build
  ```
- Keep commits focused. One logical change per commit; explain *why* in the
  message body, not just *what*.
- Schema changes require an idempotent migration block in `db.init_db()` plus
  a migration test.
- All transaction writes must go through `services/transactions.create_transaction`
  (audit row + sync dirty-flag fire automatically). Never INSERT into
  `transactions` directly.

## Protected code

Several files have verified live behaviour that is easy to break silently.
**Do not change their semantics** without a very good reason and explicit test
coverage. See the full table in
[docs/development.md — Protected code](docs/development.md#protected-code--do-not-change-behavior).

## PR expectations

- Tests pass (`pytest -v` + `npm run build` both green).
- Diffs are focused — avoid bundling unrelated cleanups with feature work.
- PR description explains the *why*: what problem does this solve, and why is
  this the right approach?
- New endpoints follow the route → service → `api.ts` pattern documented in
  [docs/development.md](docs/development.md).
- New agent tools wrap their body in `try/except` and return `{"error": ...}`
  on failure (friendly degradation, not a crash).
