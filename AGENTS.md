# AGENTS.md — Company OS

Read this first, then `docs/architecture.md` and `docs/roadmap.md`.

## Layout

- `backend/` — FastAPI control plane (Python ≥3.12, managed by `uv`).
- `web/` — Vite + React + TypeScript SPA (managed by `pnpm`).
- `docs/` — architecture (with decision log) and roadmap. Update both when you change architecture or finish a milestone.
- `vendor/hermes-agent/` — gitignored reference checkout of Hermes Agent. Read it to verify runtime behaviour; never import from it or edit it.

## Commands

```bash
# backend
cd backend
uv sync                                   # install
uv run alembic upgrade head               # migrate (SQLite at ../data/company_os.db by default)
uv run uvicorn app.main:app --reload --port 8700
uv run pytest                             # tests
uv run ruff check . && uv run ruff format --check .

# web
cd web
pnpm install
pnpm dev                                  # http://localhost:5173, proxies /api → :8700
pnpm test                                 # vitest
pnpm typecheck && pnpm lint
pnpm gen:api                              # regenerate src/api/schema.d.ts from running backend's OpenAPI
```

## Non-negotiable rules

1. **Tenancy:** every tenant-owned model has `company_id`; services receive a `CompanyContext` and filter every query by it. Never query tenant tables from route handlers directly. Non-members get 404.
2. **Authorization lives in services**, not only in routes. Agents may never change their own permissions, policies or budgets.
3. **No fake data in production paths.** If a subsystem is not built, return `{"available": false, "milestone": "Mx"}` and render an explicit "not built yet" state. Demo data, if ever added, must be labelled as demo in API and UI.
4. **Secrets** never go to the frontend, logs, audit data, agent instructions, or runtime config visible to agents.
5. **Money** is integer micro-USD (`*_micros`). Costs carry `cost_kind` (`verified`/`estimated`/`unknown`). Never present an estimate as a charge.
6. **Every schema change ships an Alembic migration.** Keep SQL portable (SQLite and Postgres).
7. **State changes write an AuditEvent** in the same transaction.
8. **Do not claim an integration works** unless it was exercised against the real system. Document what was and was not verified.
9. Enforcement (budgets, approvals, tool permissions) is code — never rely on prompt instructions.

## Conventions

- Backend: routes are thin; business logic in `app/services/`; Pydantic schemas in `app/schemas/`; errors raised as `AppError` subclasses and rendered as `{"error": {code, message, details}}`. Lists return `{items, next_cursor}`.
- Web: server state via TanStack Query hooks in `src/api/`; UI state stays in components. Reusable primitives in `src/components/ui/`, one folder per nav area in `src/features/`. Use design tokens from `src/styles/tokens.css`; no hard-coded colors. Every data view handles loading, empty, error, and not-available states. No inert buttons.
- Tests: add tests for business rules and security boundaries (tenancy, lifecycle transitions, self-modification) with every feature.
- Keep changes incremental; run backend and web checks before declaring work done.
