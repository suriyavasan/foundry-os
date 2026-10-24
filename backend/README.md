# Company OS backend

FastAPI control plane (Python >= 3.12, managed with `uv`). See `../docs/architecture.md`
and `../docs/api.md`.

## Setup

```bash
cd backend
uv sync
cp .env.example .env        # optional
uv run alembic upgrade head # creates ../data/company_os.db (gitignored)
```

The app never creates tables itself; if the schema is missing it refuses to start and tells
you to run the migration.

## Run

```bash
uv run uvicorn app.main:app --reload --port 8700   # default password mode; --host 127.0.0.1 unless you want the LAN
```

- API: `http://localhost:8700/api` - OpenAPI at `/api/openapi.json`, UI at `/api/docs`

## Accounts and logging in (Milestone 7)

Default mode (`COMPANY_OS_AUTH_MODE=password`): people have a username and password.

1. **First run.** Start the backend, open the web app and *Create your account*. From the machine the server runs on that is all;
   from another device the setup form also asks for the **setup token** (the server prints it in its terminal, and it is the
   content of `data/owner.token`). On an existing install this *claims* the old owner account: your company and memberships stay.
2. **Sign in** with the username and password (sessions: `HttpOnly` cookie, 14 days sliding / 30 days absolute, CSRF token on
   every change). Settings has password change, sessions (log out everywhere), 2FA (TOTP + recovery codes) and personal API tokens.
3. **Scripts / the CLI** use a personal API token (`Authorization: Bearer cos_pat_...`, scope read/write/admin) or, as a fallback on
   the machine that runs the server, `Authorization: Bearer <contents of data/owner.token>`.
4. **Team.** Owners/admins invite people (email + role); until an email provider is connected (M8) the UI shows a link to share.
   Members appear in the org tree, can be given tasks, reviews and approvals, and get notifications.
5. Agent runs authenticate with their run token (`Authorization: Bearer cos_run...`); an agent can never create users,
   invitations, tokens or sessions.

`COMPANY_OS_AUTH_MODE=local` is the old single-owner mode (the `cos_owner` cookie from the terminal's login link
`/api/auth/local?token=...`). It is **development only**: it refuses to start unless `COMPANY_OS_BIND_HOST` is a loopback address and
answers only loopback requests.

The runtime process never receives the owner token: it is not in its environment or arguments, and
the file lives outside the agent's workspace jail (tested). Delete `data/owner.token` and restart to
rotate it. Treat the token like a password; `data/secret.key` (run-token signing and credential
encryption) is equally sensitive.

## Model providers, credentials and the company agent runtime (Milestone 3b)

```bash
# 1. runtime venv (once): see ../runtime/README.md, then
curl -s -X POST localhost:8700/api/companies/$CID/model-providers/detect \
  -H "Authorization: Bearer $(cat ../data/owner.token)" -H 'content-type: application/json' \
  -d '{"register": true}'      # registers local Ollama (tool-capable model) + makes it the default
```

- Providers (`/model-providers`) say where agents get their LLM: local Ollama first, then OpenRouter,
  Anthropic, OpenAI and any OpenAI-compatible endpoint. API keys live in the credential store
  (`/credentials`): Fernet-encrypted with a key derived from `COMPANY_OS_SECRET_KEY` / `data/secret.key`
  (losing that key makes stored credentials unreadable: re-enter them), metadata-only API, decrypted
  only inside the runtime adapter right before the run spec is written to the runtime's stdin.
- **Ollama context trap:** Ollama's OpenAI-compatible endpoint ignores per-request `num_ctx` and serves
  whatever the *server* was started with (4096 by default). Company-agent prompts need ~5K tokens.
  `POST /model-providers/{id}/test` loads the model and reads the served window from `/api/ps`; it warns
  with the fix: `OLLAMA_CONTEXT_LENGTH=32768 ollama serve` (restart Ollama).
- The `company_agent` runtime (`app/runtimes/company_agent.py`) spawns `runtime/.venv/bin/python -m
  company_agent.run` per run: spec on stdin, JSONL on stdout, `HERMES_HOME` =
  `data/agents/<agent id>/home` (skills materialized before every run), workspace =
  `data/workspaces/<company>/<run>`. Without a reachable Docker daemon the sandbox is `none` and
  `terminal` / `code_execution` are never granted.
- Settings: `COMPANY_OS_RUNTIME_PYTHON`, `COMPANY_OS_RUNTIME_DIR`, `COMPANY_OS_PUBLIC_URL`,
  `COMPANY_OS_PRINT_LOGIN_URL`, `COMPANY_OS_SANDBOX_DOCKER_IMAGE`, `COMPANY_OS_RUNTIME_KILL_GRACE_SECONDS`.
- Agent API URL for local runtimes: set `COMPANY_OS_AGENT_API_URL` to the port you serve on.

## Work engine (Milestone 2)

Tasks assigned to an active agent are dispatched, executed through a runtime adapter and settled
against the budget ledger by two loops that share the database:

```bash
uv run companyos-worker                       # scheduler + worker; SIGTERM shuts down gracefully
COMPANY_OS_EMBEDDED_WORKER=1 uv run uvicorn app.main:app --port 8700   # or inside the API process
```

For local experiments without a model, `COMPANY_OS_ENABLE_TEST_RUNTIMES=1` registers the
`scripted` runtime (a test fixture, labelled `kind: "test_fixture"`); give an agent
`"runtime": "scripted"` and an optional `runtime_config.script`. See `../docs/api.md`
("Milestone 2") for the API, the agent API and the live stream.

Concurrency notes: SQLite runs in WAL mode with a 30 s busy timeout. Budget reservations take the
company's write lock with `UPDATE company_policies SET budget_lock_version = ...` as the first
statement of their transaction (pysqlite begins its transaction at that first write, so it is
serialized correctly; see `tests/test_ledger.py` for the thread-based proof).

## Test and lint

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Tests run the real Alembic migrations against a temporary SQLite file.

## Migrations

```bash
uv run alembic revision --autogenerate -m "describe change"   # then review the file
uv run alembic upgrade head
```

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `COMPANY_OS_DATABASE_URL` | `sqlite:///../data/company_os.db` | SQLAlchemy URL; relative SQLite paths resolve against `backend/` |
| `COMPANY_OS_AUTH_MODE` | `password` | `password` (accounts, default) or `local` (dev only, loopback only; see "Accounts and logging in") |
| `COMPANY_OS_BIND_HOST` | `127.0.0.1` | the interface the server is bound to (set by the launcher); `local` mode refuses non-loopback values |
| `COMPANY_OS_COOKIE_SECURE` | unset | force the `Secure` flag on cookies behind a TLS-terminating proxy (https requests always get it) |
| `COMPANY_OS_ARGON2_TIME_COST` / `_MEMORY_KIB` / `_PARALLELISM` | `3` / `65536` / `4` | Argon2id cost; raising it rehashes users at their next login |
| `COMPANY_OS_SESSION_IDLE_DAYS` / `_ABSOLUTE_DAYS` | `14` / `30` | session sliding window and hard cap |
| `COMPANY_OS_LOGIN_FREE_ATTEMPTS` / `_MAX_LOCKOUT_SECONDS` | `3` / `900` | failed logins before back-off per username (per IP: 3x), and the longest lock |
| `COMPANY_OS_INVITATION_TTL_DAYS` | `7` | invitation lifetime |
| `COMPANY_OS_PUBLIC_URL` | `http://localhost:8700` | base of the printed login link |
| `COMPANY_OS_PRINT_LOGIN_URL` | unset | `1`/`0` force/suppress printing the login link / setup hint (default: only on a TTY) |
| `COMPANY_OS_RUNTIME_PYTHON` | `<runtime>/.venv/bin/python` | interpreter of the company agent runtime |
| `COMPANY_OS_SANDBOX_DOCKER_IMAGE` | `python:3.12-slim` | image for sandboxed terminal/code tools (docker only; unverified) |
| `COMPANY_OS_RUNTIME_KILL_GRACE_SECONDS` | `10` | SIGTERM -> SIGKILL grace when cancelling a run |
| `COMPANY_OS_CORS_ORIGINS` | `["http://localhost:5173","http://127.0.0.1:5173"]` | JSON list of allowed origins |
| `COMPANY_OS_DATA_DIR` | the SQLite file's directory | artifacts, workspaces, agent homes, `secret.key` and `owner.token` live here |
| `COMPANY_OS_SECRET_KEY` | auto-generated `<data dir>/secret.key` (0600) | HMAC key for agent run tokens; never logged or returned |
| `COMPANY_OS_EMBEDDED_WORKER` | `0` | run the worker + scheduler inside the API process |
| `COMPANY_OS_ENABLE_TEST_RUNTIMES` | `0` | register the `scripted` test-fixture runtime |
| `COMPANY_OS_MAX_UPLOAD_BYTES` | `26214400` | artifact upload limit (25 MB) |
| `COMPANY_OS_AGENT_API_URL` | `http://127.0.0.1:8700/api/agent/v1` | where runtimes reach the agent API |
| `COMPANY_OS_WORKER_CONCURRENCY` | `4` | worker threads (runs in flight per worker process) |
| `COMPANY_OS_ENGINE_TICK_SECONDS` / `_POLL_SECONDS` | `2` / `0.5` | scheduler tick and job-queue poll intervals |
| `HERMES_API_URL` | unset | Hermes gateway base URL; unset = runtime `not_configured` |
| `HERMES_API_KEY` | unset | Hermes `API_SERVER_KEY` (sent as Bearer; never returned or logged) |
