# Company OS

A self-hosted operating system for a company staffed by AI agents. You are the human
co-founder; an AI Founder/CEO plans and delegates; AI employees execute work through
pluggable agent runtimes (first target: [Hermes Agent](https://github.com/NousResearch/hermes-agent)).

Status: **Milestone 1 (foundation)** — see [`docs/roadmap.md`](docs/roadmap.md).

## Quick start (local development)

Requires `uv` and Python ≥3.12, Node ≥20 and `pnpm`.

```bash
# 1. backend (http://localhost:8700, API docs at /api/docs)
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --port 8700

# 2. web (http://localhost:5173), in another terminal
cd web
pnpm install
pnpm dev
```

Open http://localhost:5173 and create your company.

> Auth runs in **local single-owner mode**: every request is the local owner. Only run it
> bound to localhost. Real authentication arrives in Milestone 8.

## Docs

- [`AGENTS.md`](AGENTS.md) — conventions and rules for anyone (human or AI) changing this repo
- [`docs/architecture.md`](docs/architecture.md) — architecture, security model, decision log
- [`docs/api.md`](docs/api.md) — HTTP API contract
- [`docs/roadmap.md`](docs/roadmap.md) — milestones and status
