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
| `HERMES_API_URL` | unset | Hermes gateway base URL; unset = runtime `not_configured` |
| `HERMES_API_KEY` | unset | Hermes `API_SERVER_KEY` (sent as Bearer; never returned or logged) |
