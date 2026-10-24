# Company OS

A self-hosted operating system for a company staffed by AI agents. You are the human
co-founder; an AI Co-founder plans and delegates; AI employees execute work through our own agent
runtime (a Hermes Agent fork) on local or cloud models.

Status: **Milestones 1-9 built** (see [`docs/roadmap.md`](docs/roadmap.md) for what was and was not verified).

## Quick start

Requires `uv` and Python >=3.12, Node >=20 and `pnpm` (to build the UI once).

```bash
cd backend
uv sync                      # installs the control plane and the `companyos` command
uv run companyos doctor      # what this machine is missing, with the fix for each item
uv run companyos up          # migrates, builds the UI, starts API + worker -> http://127.0.0.1:8700
```

Open the printed URL and create your account. Then:

```bash
ollama pull qwen2.5:7b && OLLAMA_CONTEXT_LENGTH=32768 ollama serve   # a free local model (or add a cloud key)
uv run companyos login                    # username + password -> stores a personal API token
uv run companyos providers detect --register
uv run companyos note "Build a landing page for our first product"
uv run companyos status
```

For the agent runtime (agents cannot execute without it) see [`docs/deploy.md`](docs/deploy.md) section 2.
Docker Compose, upgrades, backups and every setting are in [`docs/deploy.md`](docs/deploy.md); reaching the
install from your phone or the internet is in [`docs/deploy-reachability.md`](docs/deploy-reachability.md).

> New installs use **password sign-in** (the first visit creates the owner account). `COMPANY_OS_AUTH_MODE=local`
> is a development-only mode that refuses to run on a network.

### Development

```bash
cd backend && uv run uvicorn app.main:app --reload --port 8700     # or: uv run companyos up --dev
cd web && pnpm install && pnpm dev                                   # http://localhost:5173, proxies /api
```

## Docs

- [`AGENTS.md`](AGENTS.md) - conventions and rules for anyone (human or AI) changing this repo
- [`docs/architecture.md`](docs/architecture.md) - architecture, security model, decision log
- [`docs/deploy.md`](docs/deploy.md) - run, CLI, Docker, backup/restore, configuration
- [`docs/security.md`](docs/security.md) - security review, headers/CSP, dependency audit, open gaps
- [`docs/api.md`](docs/api.md) - HTTP API contract
- [`docs/roadmap.md`](docs/roadmap.md) - milestones, status and the final verification table
