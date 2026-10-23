# Company OS — Architecture

Status: living document. Update the relevant section (and the decision log) whenever an
architectural decision changes. Last major revision: Milestone 1.

## 1. What we are building

Company OS is a self-hosted control plane that lets a human co-founder operate a company
staffed by AI agents. The human sets goals and boundaries; an AI Founder/CEO plans and
delegates; AI employees execute work through pluggable agent runtimes (first: Hermes Agent).

Company OS owns **business state and governance**: companies, org structure, tasks,
budgets, approvals, artifacts, audit. Agent runtimes own **execution**: running an LLM
loop with tools in an isolated environment. The two communicate only through the runtime
adapter interface (§6).

## 2. System overview

```
┌──────────────────────────── Company OS (this repo) ────────────────────────────┐
│                                                                                │
│  web/  React SPA ──HTTP/JSON──▶  backend/  FastAPI API (control plane)         │
│                                   │  services (authz, policy, budget ledger)   │
│                                   │  SQLAlchemy ──▶ SQLite (dev) / Postgres    │
│                                   │                                            │
│                                   └─ worker + scheduler (same codebase,        │
│                                      separate process, DB-backed job queue)    │
│                                          │                                     │
│                                          ▼  RuntimeAdapter interface           │
└──────────────────────────────────────────┼─────────────────────────────────────┘
                                           │ HTTP (Bearer key from secret store)
                     ┌─────────────────────┴───────────────────────┐
                     ▼                                             ▼
          Hermes gateway API server                     future adapters (OpenHands,
          (/v1/runs, SSE events, /stop,                  Codex/Claude Code CLIs,
           /approval) per isolated HERMES_HOME           direct model APIs, local LLMs)
          terminal backend: docker, no network
```

## 3. Technology choices

| Concern | Choice | Why |
|---|---|---|
| Frontend | Vite + React 19 + TypeScript (strict), React Router, TanStack Query, Tailwind CSS v4 with CSS-variable design tokens, Radix primitives where accessibility matters (dialog, tabs, dropdown) | Mainstream, fast, no SSR needed for a self-hosted console. Server state lives in TanStack Query; UI state stays local. |
| Backend | Python 3.12+ (runs on 3.14), FastAPI, Pydantic v2, SQLAlchemy 2.0 (sync), Alembic | Python matches the agent ecosystem (Hermes is Python); typed schemas generate OpenAPI for the frontend. Sync SQLAlchemy keeps transactions simple. |
| Database | SQLite (WAL) for local dev and single-node installs; PostgreSQL for production compose | Zero-setup local runs; Postgres for concurrency. Code must stay portable across both (no SQLite-only SQL). |
| Background work | DB-backed job table with leases, run by a worker process; scheduler loop in the same process | Durable across restarts without Redis/Celery. Revisit only if throughput demands it. |
| Package managers | `uv` (backend), `pnpm` (web) | Both installed; lockfiles committed. |
| API contract | FastAPI OpenAPI → `openapi-typescript` generated types in `web/src/api/schema.d.ts` | One source of truth for request/response shapes. |

## 4. Repository layout

```
AGENTS.md                 conventions for coding sessions (read first)
docs/architecture.md      this file
docs/roadmap.md           milestones and status
backend/
  pyproject.toml, uv.lock
  alembic/                migrations (every schema change needs one)
  app/
    main.py               FastAPI app factory
    core/                 config, errors, security/principal, ids, time, money
    db/                   engine/session, Base, mixins
    models/               SQLAlchemy models (one module per aggregate)
    schemas/              Pydantic request/response models
    services/             business logic; ALL authorization + tenancy checks live here
    api/routes/           thin HTTP handlers → services
    runtimes/             RuntimeAdapter interface, registry, adapters (hermes/…)
  tests/
web/
  src/
    api/                  fetch client, generated schema types, query hooks
    components/ui/        design-system primitives
    components/layout/    app shell, sidebar, page header
    features/<area>/      one folder per navigation area
    styles/tokens.css     design tokens (dark default, light theme)
vendor/                   gitignored reference checkout of hermes-agent
```

## 5. Domain model

All ids are UUIDv4 strings. All tenant-owned rows carry `company_id` and every query that
reads them is filtered by the caller's resolved company (§7). Timestamps are UTC
(`created_at`, `updated_at`); each company has a display `timezone` for reporting periods.
**Money is stored as integer micro-USD (`*_micros`, 1 USD = 1,000,000)** — never floats.

Entities (✅ = exists, others are planned with the milestone that introduces them):

| Entity | Notes |
|---|---|
| ✅ Company | name, slug, mission, strategic_goal, constraints[], timezone, status (`active`/`paused`) |
| ✅ User, CompanyMember | human accounts; membership role `owner`/`admin`/`viewer` |
| ✅ Department | name, description, lead agent |
| ✅ Agent | persistent employee: kind (`founder`/`lead`/`specialist`/`reviewer`), role title, department, manager (agent) or reports to human, responsibilities, instructions, model provider/name, runtime + non-secret runtime config, budget limits, lifecycle status |
| ✅ AuditEvent | append-only: actor (human/agent/system), action, target, data |
| Project, Task, TaskDependency (M3) | task business status is separate from TaskRun status |
| TaskRun (M3/M4) | one execution attempt; idempotency key; runtime execution id; timings; error |
| Schedule (M5) | cron/interval, next_run_at, unique (schedule_id, scheduled_for) run key |
| Artifact, ArtifactVersion (M7) | metadata in DB, content in artifact storage (filesystem/S3) |
| Decision, Approval (M5/M6) | approvals authorize one specific scoped action |
| Budget, BudgetReservation, UsageRecord (M6) | atomic reservations; usage deduped by (run_id, provider_event_id); `cost_kind` = `verified`/`estimated`/`unknown` |
| Policy (M3) | company + per-agent: spend limits, allowed providers/integrations/tools, approval rules, max retries/runtime/concurrency/delegation depth |
| Integration, CredentialReference (M4/M8) | credentials stored encrypted in a secret store; rows only hold references |

Agent lifecycle: `proposed → active ⇄ paused → retiring → retired` (also `proposed → rejected`).
Transitions are enforced in `services/agents.py`; retirement preserves history and never
deletes rows.

**Self-modification rule:** an agent principal can never change its own enforced
permissions, policies, or budgets. Only human members with sufficient role may.

## 6. Agent runtime layer

`backend/app/runtimes/base.py` defines the contract every runtime adapter implements:

- `capabilities` — declared set: `streaming_events`, `cancel`, `usage_tokens`,
  `usage_cost`, `approvals`, `artifacts`, `idempotent_start`.
- `validate_config(config) -> list[ConfigIssue]`
- `check_health() -> RuntimeHealth` (`ok` / `degraded` / `unavailable` / `not_configured` + detail)
- `start(request: ExecutionRequest) -> ExecutionHandle` (must accept an idempotency key)
- `get_status(handle)`, `stream_events(handle, after_seq)`, `cancel(handle)`,
  `get_result(handle)`, `get_usage(handle)`

Normalized types: `ExecutionRequest`, `ExecutionHandle`, `ExecutionEvent` (seq, type,
payload, ts), `ExecutionResult`, `UsageReport` (tokens + cost with `cost_kind`),
`RuntimeError` taxonomy (`config`, `unavailable`, `rejected`, `timeout`, `cancelled`, `failed`).

Model providers (OpenRouter, Anthropic, OpenAI, local OpenAI-compatible endpoints) are a
**different integration category** from agent runtimes: a model API has no tool loop or
sandbox. A future "direct model" runtime may wrap one, but it must declare its limited
capabilities honestly.

### 6.1 Hermes adapter (findings from source inspection, hermes-agent @ db1fa15, 2026-10-23)

- **Integration surface:** the Hermes gateway's OpenAI-compatible API server
  (`gateway/platforms/api_server.py`), started with `hermes gateway run` and enabled by
  `API_SERVER_KEY` (≥16 chars). Default bind `127.0.0.1:8642`; `Authorization: Bearer`.
  - `POST /v1/runs` `{input, session_id?, instructions?, model?, provider?}` → 202 `run_id`;
    honours an `Idempotency-Key` header.
  - `GET /v1/runs/{id}`; `GET /v1/runs/{id}/events` (SSE, resumable via `Last-Event-ID`);
    `POST /v1/runs/{id}/stop|approval|steer`; `GET /health`.
  - Terminal events `run.completed|failed|cancelled|interrupted` carry token usage only.
    Cost: `GET /api/sessions/{id}` exposes `estimated_cost_usd`/`actual_cost_usd` → recorded
    as `estimated` unless Hermes reports an actual charge.
- **Rejected surfaces:** `hermes -z` / `chat -q` are thin clients of a per-profile gateway
  daemon; killing the process does *not* cancel the run. ACP exposes no cost. In-process
  `AIAgent` embedding couples us to Hermes' exact-pinned Python 3.14 deps and auto-approves
  dangerous commands when not in an interactive context.
- **Isolation per employee:** a standalone `HERMES_HOME` per agent (never nested under a
  shared `profiles/` root — nested profiles fall back to the root `auth.json` and share the
  kanban board and cron ticker). Company OS generates its `config.yaml`:
  explicit toolset allowlist (unset = all tools enabled), `approvals.mode: manual`,
  `terminal.backend: docker` with `docker_network: false`, `container_persistent: false`,
  `HERMES_WRITE_SAFE_ROOT` set to the task workspace, `gateway.standalone: true`,
  memory/skills scoped to that home, curator and background review disabled by default.
- **Known limits:** toolsets are configured per api_server platform, not per request, so
  per-agent tool permissions require per-agent homes/gateways. Profiles are not a security
  boundary — the OS/container is. Deployment topology (gateway per agent vs. pooled) is
  decided in M4.

## 7. Security model

- **Principal & tenancy:** every request resolves a `Principal` (human user or, later, an
  agent service identity). Company-scoped routes live under `/api/companies/{company_id}/…`;
  a dependency resolves membership and yields a `CompanyContext`. Services take the context
  and filter every query by `company_id`. Non-members get **404** (no existence leaks).
- **Auth (M1):** `COMPANY_OS_AUTH_MODE=local` — a single local owner account is created at
  startup and every request is that owner. Only acceptable bound to localhost. Real
  sessions (password/OIDC, secure cookies, CSRF) arrive in M8; the principal abstraction
  means services do not change.
- **Secrets:** never returned by the API, never logged, never written into agent
  instructions or runtime config visible to agents. Stored encrypted (Fernet key from
  `COMPANY_OS_SECRET_KEY`) behind `CredentialReference` (M4).
- **Enforcement is code, not prompts:** budgets, approvals and tool permissions are checked
  by Company OS gates before dispatch and at checkpoints. A failed check never falls through.
- **Audit:** state-changing service calls write an `AuditEvent` in the same transaction.

## 8. API conventions

- JSON over HTTP under `/api`. Pydantic validates all input server-side.
- Errors: `{"error": {"code": "not_found" | "validation_error" | "conflict" | "forbidden" | "invalid_transition" | ..., "message": str, "details": any}}` with matching HTTP status.
- Lists: `{"items": [...], "next_cursor": str | null}`; query `?limit=` (default 50, max 200) and `?cursor=`.
- Subsystems that are not built yet are reported as `{"available": false, "milestone": "M3"}`
  in aggregate endpoints — never as zeros that look like real data.

### Milestone 1 endpoints

| Method & path | Purpose |
|---|---|
| `GET /api/health` | liveness + db check |
| `GET /api/me` | principal + memberships |
| `GET/POST /api/companies` | list my companies / create (also creates owner membership + AI Founder agent + audit) |
| `GET/PATCH /api/companies/{cid}` | read / update vision, goal, constraints, timezone |
| `GET /api/companies/{cid}/overview` | Command Center aggregate |
| `GET/POST /api/companies/{cid}/departments`, `PATCH …/{id}` | departments |
| `GET/POST /api/companies/{cid}/agents`, `GET/PATCH …/{id}` | employees (POST creates `proposed`) |
| `POST /api/companies/{cid}/agents/{id}/{activate,pause,resume,retire}` | lifecycle transitions |
| `GET /api/companies/{cid}/activity` | audit events, newest first, paginated |
| `GET /api/runtimes` | registered runtime adapters, capabilities, health |

## 9. Decision log

| # | Date | Decision |
|---|---|---|
| D1 | 2026-10-23 | Monorepo: `backend/` (FastAPI) + `web/` (Vite React). No microservices. |
| D2 | 2026-10-23 | Hermes is an external runtime reached via its gateway HTTP Runs API, not embedded and not shelled out to. |
| D3 | 2026-10-23 | Money as integer micro-USD; costs tagged verified/estimated/unknown. |
| D4 | 2026-10-23 | SQLite for dev, Postgres for production; DB-backed job queue instead of Redis. |
| D5 | 2026-10-23 | Tenancy enforced in services via `CompanyContext`; non-members get 404. |
| D6 | 2026-10-23 | No fake data in production paths. Unbuilt subsystems report `available:false`; any future demo mode must be explicitly labelled in UI and API. |
