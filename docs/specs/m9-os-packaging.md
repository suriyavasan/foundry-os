# M9 — OS packaging, CLI & hardening — spec (v2)

Company OS must feel like an OS: one command to start everything, a CLI for every core
operation, reproducible deployment, backups, and an honest final verification.
Already done elsewhere: accounts/sessions/API tokens (M7), reachability + `app/serve.py`
launcher that applies the saved LAN bind (M8), guardrails + emergency stop (M6).

## 1. `companyos` CLI (backend package `app/cli/`, Typer + Rich, `[project.scripts] companyos`)
Talks to the API over HTTP. Config `~/.config/companyos/config.toml` (`api_url`,
`company_id`); credential = personal API token stored in `~/.config/companyos/token`
(0600) via `companyos login` (username/password → creates a scoped token through the API;
never stores the password), or the owner token (`data/owner.token`) when run on the server
machine. Every command supports `--json`; non-zero exit codes on errors with the API's
error message.

| command | does |
|---|---|
| `companyos up [--port 8700] [--no-worker] [--open] [--dev]` | migrate DB, build web if `web/dist` is missing or stale (or use `--dev` to proxy Vite), start the API (serving `web/dist` at `/` with SPA fallback) via `app.serve` (honours the saved LAN bind) + the worker/scheduler as a supervised child; graceful shutdown on Ctrl-C/SIGTERM; prints local/LAN/public URLs and, on first run, the setup URL (+ setup token only when needed) |
| `companyos doctor` | Python/uv, DB reachable + migrations at head, runtime venv + import check, Ollama reachable + served context (warn if < 32K, print the fix), Docker daemon, cloudflared/tailscale presence, disk space, secret/owner-token file perms, auth mode + exposure gate status |
| `companyos login / logout / whoami` | |
| `companyos status` | company, goal, AI Co-founder state, headcount by family, live agent states, task counts, spend today/month (verified vs estimated), open alerts, reachability |
| `companyos note "…"` / `notes ls / show <id>` | write a Founder's note; show triage + AI Co-founder replies |
| `companyos goal ls / add` | |
| `companyos agents ls / show / hire --template … / pause / resume / retire / skills` | |
| `companyos tasks ls / show / create / cancel / retry` | |
| `companyos runs tail [--agent X] [--follow]` | live SSE stream of run events, colorized |
| `companyos skills ls / search [--github] / install / show` | |
| `companyos approvals ls / approve / reject` | |
| `companyos budget show / set` · `policy show / set key=value` | |
| `companyos pause / resume` · `companyos stop --all` / `companyos resume --all` | company kill switch · global emergency stop |
| `companyos providers ls / detect / test` · `companyos credentials set NAME` | secret read from stdin/prompt, never argv |
| `companyos reach` | reachability status + URLs (+ QR in terminal for the LAN URL) |
| `companyos backup create [--out PATH]` / `backup restore PATH` | SQLite online backup API + tarball of `data/` (artifacts, skills, agent homes; excludes workspaces caches); restore only into a stopped install, verifies archive manifest + checksums |
| `companyos auth reset-password USERNAME` | local-only (server machine, requires owner token file access); prompts for a new password; revokes that user's sessions |

## 2. Serving & deployment
- FastAPI serves `web/dist` when present (`COMPANY_OS_WEB_DIST`), immutable caching for
  hashed assets, no caching for `index.html`, SPA fallback that never shadows `/api`.
- Web: route-level code splitting so the initial bundle stays well under 500 kB.
- `Dockerfile`s: `backend` (API/worker; multi-stage; non-root user; runtime venv baked in;
  web dist copied in) — keep images lean; no secrets in images.
- `docker-compose.yml`: `api` (embedded worker off), `worker`, `postgres` (optional
  profile; SQLite default with a volume), volumes for `data/`; health checks; `.env.example`;
  documented opt-in for host Docker socket (agent sandbox) with its risk.
- Postgres: run the full backend test suite against Postgres in CI-like local check if
  Docker is available; otherwise document as unverified.
- `docs/deploy.md`: local (`companyos up`), Docker Compose, upgrade (pull → migrate),
  backup/restore, configuration reference (all env vars), Ollama context setup.

## 3. Security review (`docs/security.md`)
Execute and record (with test references or commands): tenancy, authz role matrix, agent
self-modification, run-token scope, guardrail coverage, secret redaction (logs, events,
audit, errors, CLI output), file jail + sandbox fallback, private-URL blocking, prompt-
injection posture, DLP, rate limits, CSRF/session/cookie flags, exposure gate, webhook
signatures, dependency audit (`pip-audit`/`uv` and `pnpm audit` — report findings), HTTP
security headers (add: CSP for the SPA, X-Content-Type-Options, Referrer-Policy,
frame-ancestors none). Fix what is cheap; list the rest honestly.

## 4. Final verification (`scripts/verify.sh`, plus a manual checklist)
1 company created · 2 AI employee configured · 3 task executed through the company agent
runtime (local Ollama) · 4 events + artifacts persisted · 5 usage/costs recorded ·
6 spending policy enforced · 7 approvals gate protected actions · 8 agent paused and retired ·
9 recovery after restart (kill worker mid-run → `worker_lost` → retried) · 10 deploy from
docs (`companyos up` from a clean checkout into a temp data dir).
Record results honestly in `docs/roadmap.md`; never claim production readiness for items
that did not pass.
