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
                 companyos CLI ─┐          ┌─ web/  single-canvas React UI (SSE live updates)
                                ▼          ▼
┌──────────────────────── backend/  Company OS control plane ────────────────────────┐
│  FastAPI API  (/api: owner API, /api/agent/v1: agent API with scoped run tokens)    │
│  services: tenancy/authz, policy & autonomy limits, budget ledger, tasks, skills    │
│  SQLAlchemy ──▶ SQLite (dev) / Postgres (prod)      company_events outbox ──▶ SSE   │
│                                                                                     │
│  worker process: leases jobs ─▶ dispatches TaskRuns through RuntimeAdapter          │
│  scheduler loop: founder heartbeats, routines, dispatch of ready tasks, recovery    │
└───────────────────────────────┬─────────────────────────────────────────────────────┘
                                │ spawn per run: python -m company_agent.run --spec …
                                │ env: run token, HERMES_HOME (per employee), model creds
                                ▼
            runtime/  company-agent  (lean fork of Hermes Agent, MIT)
            agent loop + tools + skills (only the employee's own) + company toolset
            stdout: JSONL run events  ──▶ worker persists, settles usage, updates task
            company toolset ──HTTP──▶ /api/agent/v1 (create subtasks, artifacts, hires…)
```

Two processes own state changes: the API (human + agent requests) and the worker/scheduler
(dispatch, run lifecycle). Both go through the same services, so policy and tenancy checks
are identical. In local dev both can run inside the API process
(`COMPANY_OS_EMBEDDED_WORKER=1`).

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
    cli/                  the `companyos` command (Typer): config, HTTP client, one module per command group
    ops/                  machine-level operations: up (supervisor), doctor, backup/restore, pid files
    web.py                serving web/dist from the API port + security headers/CSP middlewares
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

### 6.1 Company agent runtime — our Hermes fork (decision D7, supersedes D2)

The owner wants the agent layer reshaped for a company, not Hermes used as a generic
assistant. We therefore fork Hermes Agent (MIT, upstream commit `db1fa15`) into
`runtime/` and keep only what an AI employee needs:

- **Kept:** the agent loop (`AIAgent`), tool registry and core tools (file, terminal,
  web, code execution, todo, skills), the skills system, model provider plugins, terminal
  backends (local, docker), skills guard.
- **Dropped:** messaging gateways, TUI, desktop/web dashboard, website, apps, locales, cron,
  kanban, voice/media extras. Removals are recorded in `runtime/FORK.md`.
- **Added (`runtime/company_agent/`):**
  - `run.py` entrypoint: reads a run spec (task, agent identity, instructions, model,
    allowed toolsets, limits), builds `AIAgent`, streams **JSONL events on stdout**
    (`run.started`, `message`, `tool.started`, `tool.completed`, `usage`, `artifact`,
    `run.completed|failed|cancelled`), exits with a defined code. SIGTERM → interrupt.
  - `company` toolset: tools that call the Company OS agent API with the run token
    (read company context, create/delegate subtasks, submit artifacts, report progress,
    complete/review tasks, record decisions, request hires/approvals, search skills).
  - Policy approval callback: dangerous commands are decided by Company OS policy, never
    auto-approved by Hermes defaults.
- **Per-employee isolation:** each agent gets its own `HERMES_HOME`
  (`data/agents/<agent_id>/home`) containing a generated `config.yaml` and **only that
  employee's skills**; each run gets a workspace directory that is the write root.
  Memory/session DB/curator/background review are off — company memory lives in Company OS.
- **Process boundary:** the runtime runs in its own virtualenv (Python 3.14, Hermes' exact
  pins), spawned by the worker. Company OS never imports it.

The control plane talks to it through `app/runtimes/company_agent.py`, a `RuntimeAdapter`
like any other. The upstream-Hermes gateway adapter (§6.2) stays available for users who
run stock Hermes.

### 6.2 Upstream Hermes gateway adapter (findings, hermes-agent @ db1fa15, 2026-10-23)


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

## 6.3 Autonomy engine (M5)

- **Company heartbeat:** a schedule per company wakes the Founder (default every 15 min, and
  immediately on key events: goal changed, task finished, run failed). The Founder's run
  sees live company state through the agent API and acts through tools: plan, create
  projects/tasks, assign, request hires, review deliverables, record decisions.
- **Delegation:** managers create subtasks for their reports (depth-limited). Tasks and
  artifacts are the only collaboration channel — no free-form agent chat.
- **Dispatch:** the scheduler moves tasks to `ready` when dependencies are done and an
  active assignee exists, then dispatches runs within concurrency and budget limits.
- **Review loop:** tasks with acceptance criteria go to `in_review`; the reviewer (manager
  by default) accepts or requests a revision (bounded by `max_revisions`).
- **Hiring loop:** when no active employee has the skills a task requires, a hiring task
  goes to the HR agent, which picks a role template, attaches/installs/authors skills, and
  hires. Under the default autonomy policy the hire is activated automatically within
  `max_headcount` and budget.
- **Human role:** sets goal and limits, can pause the company (kill switch: cancels runs,
  stops dispatch) and answers the few approvals policy reserves for humans.
- **M12 full autonomy (decisions D31-D35):** an autonomy watchdog closes loops that would
  otherwise stall (reviews without a decision, blocked tasks, projects without work, finished
  projects, goals without projects, retired owners) with bounded interventions recorded in
  `autonomy_interventions`, and alerts the human when a ladder runs out; failures are classified
  transient vs permanent with backoff and breaker auto-resume; management and work runs can use
  different models; hybrid project work shares one git repository per project; decomposition is
  capped per run, parent and project. Spec: `docs/specs/m12-full-autonomy.md`.

## 6.4 Skills (M4)

- Skills use the SKILL.md format (Hermes / Agent Skills). The **library** lives on disk
  (`data/skills/<slug>/`), metadata in the DB (`Skill`: source, license, version, sha256,
  trust level, tags). Sources: Hermes' bundled/optional skills (MIT), GitHub repositories
  (license-checked, pinned to a commit), and company-authored skills (written by HR).
- Every imported or authored skill passes the runtime's skills guard scan; failures are
  quarantined and never materialized.
- **Role templates** (founder, HR, engineering, design, marketing, sales, customer success,
  research, QA, operations) define default instructions, toolsets and skills.
- Agents only ever see their assigned skills: they are materialized into the agent's
  `HERMES_HOME/skills` before each run.

## 7. Security model

- **Principal & tenancy:** every request resolves a `Principal` (human user or, later, an
  agent service identity). Company-scoped routes live under `/api/companies/{company_id}/…`;
  a dependency resolves membership and yields a `CompanyContext`. Services take the context
  and filter every query by `company_id`. Non-members get **404** (no existence leaks).
- **Auth (M7, decision D15):** `COMPANY_OS_AUTH_MODE=password` (default). People sign in with username + password
  (Argon2id; sessions are server-side rows with a hashed 256-bit secret in an `HttpOnly`, `SameSite=Lax` cookie `cos_session`,
  sliding 14 days / absolute 30; CSRF is a signed double-submit `cos_csrf` / `X-CSRF-Token` required on every state-changing cookie
  request and never for Bearer). Login is rate limited and backs off per username and per IP with generic errors; admins can add
  TOTP 2FA with recovery codes. Scripts use personal API tokens (`cos_pat_…`, hashed, scoped read/write/admin, never able to manage
  the account). Roles per company: `owner`, `admin`, `member` (a human colleague that can be assigned work, reviews and approvals;
  never dispatched to a runtime), `viewer`; enforced in the services. A fresh install is claimed by the first-run setup, which needs
  the setup token (`data/owner.token`) unless the request is from loopback; on an existing install it claims the placeholder owner so
  the company and memberships survive. Run tokens still resolve to their agent, and agents can never create users, invitations,
  tokens or sessions (human-only guardrail actions, `403 guardrail_denied`). The M3b-sec owner token stays as the bootstrap path
  (`cos_owner` cookie while the install is unclaimed) and CLI fallback (`Authorization: Bearer <owner token>`). `local` mode is
  development only: it refuses non-loopback binds and non-loopback clients. Agent processes never receive the owner token and the
  file jail blocks reading it. Details: `docs/api.md` (Milestone 7).
- **HTTP hardening (M9, D24):** every response carries nosniff / no-referrer / frame-deny / permissions-policy headers (HSTS over
  https); the UI is served with `script-src 'self'` CSP and `/api` answers with `default-src 'none'`. Details and the review
  checklist: `docs/security.md`.
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
| D7 | 2026-10-23 | Owner direction: fork Hermes into `runtime/` as a company-employee runtime (spawned per run, JSONL events, company toolset). Supersedes D2 as the primary runtime; the gateway adapter stays as an option. |
| D8 | 2026-10-23 | Autonomy by default. Human approval only for categories in `policy.human_approval_required_for` (default: external purchases, budget increases). Limits remain code-enforced. |
| D9 | 2026-10-23 | Budget reservations are serialized per company by a write to the company's policy row at the start of the transaction (portable lock on SQLite and Postgres). |
| D10 | 2026-10-23 | Live updates via a `company_events` outbox table polled by the SSE endpoint (works across API and worker processes; resumable with Last-Event-ID). |
| D11 | 2026-10-23 | UI moves to a single sidebar-less canvas (owner direction); org chart of avatars with live hover is the primary widget. |
| D12 | 2026-10-24 | Layered autonomy (strategic / management / execution / support loops). The human is the **Founder**; the AI agent of kind `founder` is presented as the **AI Co-founder** and manages all other agents. |
| D13 | 2026-10-24 | Founder's notes: free-form thought stream right of the org chart, triaged and acted on by the AI Co-founder with a threaded reply per note. |
| D14 | 2026-10-24 | Guardrails as a first-class engine: every agent action has a risk tier; company rules map tiers to allow / peer review / AI Co-founder approval / human approval / deny. |
| D15 | 2026-10-24 | Accounts: username + password (Argon2id, server sessions, CSRF); humans can be org members ("human agents") assignable to tasks; invitations by email or link. |
| D16 | 2026-10-24 | Reachability & identity through connected accounts and control-plane provider adapters. No automated sign-up on CAPTCHA/phone/ToS-gated services; the Founder connects them once. Public exposure requires password auth. |
| D17 | 2026-10-24 | M5 orchestration is deterministic control-plane code around agent judgement: a debounced/coalesced/rate-limited wake queue (not direct task creation), reviews and failure analyses as tasks, subtask waiting, project-lead routing with a lead-gap safety net, role-scoped task-creation rules, loop guard, circuit breaker. Small local models choose wrongly, so order and authority are enforced in services (a directive cannot be triaged before the Co-founder created something; a project lead must be an agent of kind `lead`), never only in prompts. |
| D18 | 2026-10-24 | Agent-initiated money is capped in code: `max_agent_monthly_budget_micros` ($25) and a $5 default hire budget; only humans raise the cap (found live in M3b: a $5,000/month hire budget). |
| D19 | 2026-10-24 | Guardrail gate runs as a router-level dependency over the whole agent API (plus direct calls from the dispatcher for paid fallback); a test enumerates every agent-API route from OpenAPI and fails if one lacks a risk tier. Unregistered actions/tools are denied (fail closed). |
| D20 | 2026-10-24 | Default guardrail rules live in code (versioned with the repo); companies only store overrides. Precedence: specific action > tier, then agent > family > all, newest wins ties. |
| D21 | 2026-10-24 | Model routing defaults to `free_first`: local/free providers are preferred; paid fallback only when allowed by policy and budget, gated by the guardrail engine. |
| D22 | 2026-10-24 | Reachability & identity run through control-plane provider adapters (Cloudflare, Vercel, GitHub, AgentMail, Resend, Tailscale, Porkbun, Namecheap). Actions have tier floors that rules cannot lower (domain registration/purchase always need the Founder); public exposure (LAN bind or tunnel) requires password auth with a strong password or 2FA; the server never rebinds itself — the saved LAN preference applies on restart. |
| D23 | 2026-10-24 | M9 packaging: one CLI (`companyos`, Typer) that talks to the API over HTTP and keeps only three things local — `up`/`doctor`/`backup` (they manage this machine) and `auth reset-password|setup-token` (they prove server access by reading `owner.token`; deliberately **not** HTTP endpoints). Secrets never go through argv: stdin or a hidden prompt. |
| D24 | 2026-10-24 | The API port serves the built UI (pure-ASGI middleware, never shadowing `/api`; hashed assets immutable, everything else revalidated) and adds security headers + a strict CSP (`script-src 'self'`; the theme bootstrap is a file, not an inline script). `companyos up` supervises the worker as a child (backoff restart) and treats the API as critical; the worker is no longer embedded in the API process outside dev. |
| D25 | 2026-10-24 | Every long-running process registers `<data dir>/run/<role>.<pid>.json`; `restore` refuses a data dir with a live process. Backups are SQLite-online-backup snapshots + files with a per-file SHA-256 manifest, verified before restore touches anything; replaced data is moved aside, never deleted. |
| D26 | 2026-10-24 | A run that fails on configuration before the runtime executed anything releases its reservation (cost kind `unknown`) instead of settling it as an estimate (found in the M9 live verification). |
| D27 | 2026-10-24 | A company is unusable until it is AI-ready: a model provider whose live test passed reachability, auth, tool calling and context-window checks is selected as the management model. Dispatch, wakes, schedules and creation of notes/tasks/hires are blocked until then. |
| D28 | 2026-10-24 | Work modes: `api` (everything on the forked runtime) or `hybrid` (management roles and planning/review/hiring always on the API; employees' work tasks on a coding CLI — Claude Code, Codex, OpenCode). CLIs reach company tools only through a stdio MCP server bound to the run token, so every action still passes the guardrails. CLIs get a shell only inside a real OS sandbox (Codex workspace-write); dangerous permission flags are refused at spawn. `api_key` auth isolates the CLI in its own HOME; `own_login` uses the real HOME (weaker isolation, terms caveat). |
| D29 | 2026-10-24 | Reflections and standards: the AI Co-founder posts reflections; only a human endorsement adopts a company standard, and active standards are injected into every agent's context. The Command Center guarantees an output for every input (instant slash commands; free text answered by the AI Co-founder, with a system fallback and one automatic retry). |
| D30 | 2026-10-24 | Distribution as **Foundry AI OS** (`foundryos`): self-contained per-platform bundles (two python-build-standalone interpreters, prebuilt UI), Homebrew tap + one-line installer + Docker; installed copies keep data in the platform data dir, run as a user service, update by checksummed atomic version switch with rollback, and back up data before every migration. |
| D31 | 2026-10-26 | Autonomy watchdog (M12): a deterministic scheduler pass (every 60 s per active, AI-ready, not-stopped company) detects stalls (reviews, blocks, hires, leadless or workless projects, finished projects, goals without projects, retired owners, idle company) and remediates through one bounded ladder (`autonomy_interventions`: lead/reviewer, then Co-founder, then a critical human alert `autonomy_stalled`). No automatic nudge is unbounded, every one is audited, and the Co-founder still cannot change budgets or policy. |
| D32 | 2026-10-26 | Transient vs permanent failures (M12): rate limits, overload and connection errors are classified transient, refund the attempt, back off exponentially (30 s to 15 min) and do not count for the circuit breaker; permanent failures back off 0/60/240 s. A breaker pause caused by the system (not a human) resumes itself after `circuit_breaker_cooldown_seconds`, at most twice per 24 h; a human pause is never auto-resumed. |
| D33 | 2026-10-26 | Per-role models and CLI scope (M12): management runs always use the management model, which `free_first` never displaces (only an owner pin or a disabled provider does); work runs may use an optional work model; `cli_role_families` limits which role families run on a coding CLI, because CLIs have no web. Non-free CLI runs reserve `cli_run_reservation_micros` ($1) so the CLI's own spend cap is realistic; management keeps `management_reserved_slots` run slots. |
| D34 | 2026-10-26 | Shared project repository (M12): one git repository per project; each project run works in its own worktree on `run/<run_id>` and successful runs are merged `--no-ff` under a per-project lock, so later tasks see earlier work; a conflict fails the run as retryable `merge_conflict`; failed runs keep their branch; every run records a "Changes" artifact. Git is invoked with hooks, GPG and fsmonitor disabled, a minimal environment, a timeout and no shell; without git the engine falls back to per-run directories. |
| D35 | 2026-10-26 | Decomposition caps and duplicate detection (M12): agent-created tasks are bounded per project (`max_work_tasks_per_project`) and per parent (`max_subtasks_per_task`), an agent may retry its own task twice, and duplicates are detected on normalised titles or token similarity (>= 0.85) against open and recently done tasks, so re-planning cannot multiply work. |
| D36 | 2026-10-26 | `foundryos setup` is the terminal equivalent of the web onboarding (M13). Every change to server state (account, 2FA, company, model provider, work mode, tunnel) goes over HTTP through the same endpoints and checks as the web app, so the AI-ready gate, the password policy and the exposure gate cannot be skipped from the terminal. Only three things are local: starting the server, the background service and linger, and running a coding CLI's own login in the user's terminal. This extends D23 (the CLI keeps only machine-local commands off HTTP). Secrets come from a hidden prompt or `--secrets-stdin`, never argv. A box with no screen (SSH, container, no `DISPLAY`) never gets a browser opened; it gets the local URL, an SSH port-forward hint and the public URL. |
| D37 | 2026-10-26 | The Cloudflare quick tunnel is the default free public URL (M13): no account, no domain, no open port. It is offered once at setup, with one confirmed, checksum-verified `cloudflared` download. Its URL changes on every start, so a change is stored (last five), audited, logged, written to `<data>/public-url.json` and announced to open screens; the API reports `ephemeral`, `last_url` and `url_changed_at`. Passkeys are refused on ephemeral origins (they would break at the next restart) and do not count for the exposure gate. The stable path is a named Cloudflare tunnel on your own domain or Tailscale Funnel. Quick tunnels do not carry server-sent events (per Cloudflare's documentation), so the web app's polling fallback applies. |
| D45 | 2026-10-27 | Agent cards and purchasing (M15): an agent can only **request** a purchase (`company_request_purchase`); the platform decides, issues and records. A deterministic policy (bands that all start at 0, so everything goes to a person until the owner opts in; recurring starts and new vendors always go to a person; the Co-founder never approves its own request; code ceilings on what an AI may approve: $100 auto, $500 per agent and month auto, $1,000 per charge for the Co-founder) feeds an Approval bound to the exact scope (amount, vendor, recurrence, card limit) and spent once. The card (a per-purchase, merchant-scoped, exact-limit card from a provider adapter, or the owner's own card in manual mode) is issued after the approval commits, with the purchase id as idempotency key, and never falls back to another provider or account. Checkout is always a human task: the card number is never fetched, stored, logged or shown by Foundry (no adapter method, schema field or column can carry one; reveal is a link to the provider's own page, owner/admin, signed in within five minutes, rate limited, audited). Freezing is automated (emergency stop, company pause, anomalies, disabling an account); unfreezing is human-only and never happens on resume. Settled charges are `verified` expenses (`source` card, or `human` for an amount a person confirmed), written once per transaction; holds are "committed", never presented as charges. |
| D46 | 2026-10-27 | Purchases have their own budget (M15): `purchase_monthly_budget_micros` (company), an optional per-agent `purchase_monthly_limit_micros` and the band totals are separate from the model-usage budgets, so a $20 tool never exhausts an agent's model budget and model budgets never block a purchase. The purchasing flow uses its own bands (`rules.purchase_band`) for `purchase.make` / `subscription.start`; the same actions arriving as provider actions keep the old `auto_spend_limit_micros` default. Guardrail rules can only tighten a purchase decision. |
