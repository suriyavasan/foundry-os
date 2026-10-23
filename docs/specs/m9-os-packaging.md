# M9 — OS packaging, CLI & hardening — spec

## 1. `companyos` CLI (backend package, Typer + Rich, talks HTTP to the API)
Config: `~/.config/companyos/config.toml` (`api_url`, `company_id`, session token file
0600). Every command supports `--json`.

| command | does |
|---|---|
| `companyos up [--port 8700] [--no-worker] [--open]` | migrate, start API (serving the built web UI at `/`) + worker/scheduler in one supervised process tree; prints URL |
| `companyos doctor` | checks Python/uv, DB, migrations, runtime venv + import check, Ollama reachable + models, Docker daemon, disk space, secret key perms |
| `companyos init` | interactive: create company (name, goal, constraints, timezone), detect Ollama, register provider, choose default model |
| `companyos status` | company status, goal, headcount by family, live agent states, task counts, spend today/month, alerts |
| `companyos goal set "…"` / `goal ls` | |
| `companyos agents ls / show / hire --template … / pause / resume / retire / skills` | |
| `companyos tasks ls / show / create / cancel / retry` | |
| `companyos runs tail [--agent X] [--follow]` | live stream (SSE) of run events, colorized |
| `companyos skills ls / search [--github] / install / show` | |
| `companyos approvals ls / approve / reject` | |
| `companyos budget show / set` · `policy show / set key=value` | |
| `companyos pause / resume` | kill switch |
| `companyos providers ls / add / test` · `credentials set NAME` (reads value from stdin/prompt, never argv) | |
| `companyos backup create / restore` | SQLite online backup + data dir tarball; restore into a stopped install |

## 2. Serving & deployment
- Production build: `pnpm build` → `web/dist`; FastAPI serves it (SPA fallback) when
  `COMPANY_OS_WEB_DIST` exists. Single port.
- `docker-compose.yml`: `api` (+ embedded scheduler off), `worker`, `postgres`, volumes for
  `data/` (artifacts, skills, agent homes, workspaces); runtime venv baked into the worker
  image; health checks; `.env.example`; no secrets in images. Agent sandbox: the worker
  uses the host Docker socket only if the owner opts in (documented risk) — otherwise
  sandbox `none` (no terminal tools).
- Docs: `docs/deploy.md` (local, Docker Compose, upgrade = pull + migrate, backup/restore,
  configuration reference).

## 3. Auth & security
- `COMPANY_OS_AUTH_MODE=password`: owner account with Argon2 password hash
  (`companyos init` sets it), server-side sessions (random 256-bit id, HttpOnly, SameSite=Lax,
  Secure when HTTPS), CSRF token for state-changing requests from the browser, login rate
  limiting, session expiry. CLI uses a personal API token (hashed in DB). `local` mode
  remains for localhost-only dev and refuses to bind non-loopback hosts.
- Security review checklist executed and recorded in `docs/security.md`: tenancy tests,
  secret redaction (logs, events, audit, errors), agent self-modification, run-token scope,
  file jail, sandbox fallback, prompt-injection posture (untrusted content marked as data in
  prompts; tool outputs never escalate permissions), rate limits on agent API, CORS, headers.

## 4. Final verification (scripted where possible: `scripts/verify.sh`)
1 company created · 2 AI employee configured · 3 task executed through the company agent
runtime · 4 events + artifacts persisted · 5 usage/costs recorded · 6 spending policy
enforced · 7 approvals gate protected actions · 8 agent paused and retired · 9 recovery
after restart (kill worker mid-run → run marked worker_lost → retried) · 10 deploy from docs.
Record results honestly in `docs/roadmap.md`; do not claim production readiness for items
that did not pass.
