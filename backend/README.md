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
uv run uvicorn app.main:app --reload --port 8700   # bind to localhost only (local auth mode)
```

- API: `http://localhost:8700/api` - OpenAPI at `/api/openapi.json`, UI at `/api/docs`
- `COMPANY_OS_AUTH_MODE=local` treats every request as the local owner (`owner@localhost`)
  with no authentication. Never expose it beyond localhost.

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
| `COMPANY_OS_AUTH_MODE` | `local` | Only `local` exists in M1 |
| `COMPANY_OS_CORS_ORIGINS` | `["http://localhost:5173","http://127.0.0.1:5173"]` | JSON list of allowed origins |
| `COMPANY_OS_DATA_DIR` | the SQLite file's directory | artifacts, run workspaces and `secret.key` live here |
| `COMPANY_OS_SECRET_KEY` | auto-generated `<data dir>/secret.key` (0600) | HMAC key for agent run tokens; never logged or returned |
| `COMPANY_OS_EMBEDDED_WORKER` | `0` | run the worker + scheduler inside the API process |
| `COMPANY_OS_ENABLE_TEST_RUNTIMES` | `0` | register the `scripted` test-fixture runtime |
| `COMPANY_OS_MAX_UPLOAD_BYTES` | `26214400` | artifact upload limit (25 MB) |
| `COMPANY_OS_AGENT_API_URL` | `http://127.0.0.1:8700/api/agent/v1` | where runtimes reach the agent API |
| `COMPANY_OS_WORKER_CONCURRENCY` | `4` | worker threads (runs in flight per worker process) |
| `COMPANY_OS_ENGINE_TICK_SECONDS` / `_POLL_SECONDS` | `2` / `0.5` | scheduler tick and job-queue poll intervals |
| `HERMES_API_URL` | unset | Hermes gateway base URL; unset = runtime `not_configured` |
| `HERMES_API_KEY` | unset | Hermes `API_SERVER_KEY` (sent as Bearer; never returned or logged) |
