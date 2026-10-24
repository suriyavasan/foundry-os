# Deploying and operating Company OS (M9)

This is the operator guide: run it on your own machine, in Docker, upgrade it, back it up, configure it.
Network access from other devices and the public internet is a separate guide: `docs/deploy-reachability.md`.
What was and was not verified is listed in section 10 and in `docs/roadmap.md` ("Final verification").

## 1. Requirements

| | |
|---|---|
| Python | 3.12+ (the dev machine uses 3.14); managed with [`uv`](https://docs.astral.sh/uv/) |
| Node | 20+ and `pnpm` (only to build the web UI; not needed if `web/dist` is already built) |
| Agent runtime | `runtime/` has its own virtualenv (Hermes fork, Python 3.14, ~180 MB). Without it the API and UI run, but agents cannot execute and non-built-in skills cannot be scanned |
| A model | a local [Ollama](https://ollama.com) (free) or a cloud key (OpenRouter, Anthropic, OpenAI, any OpenAI-compatible endpoint) |
| Disk | a few hundred MB plus your data; `companyos doctor` warns below 2 GB free |

## 2. Run it locally: `companyos up`

```bash
git clone <this repo> company-os && cd company-os

cd backend && uv sync && cd ..                                        # the control plane + the `companyos` command
cd runtime && uv sync --frozen --no-install-project \
      --extra anthropic --extra ddgs --python 3.14 && cd ..           # the agent runtime (optional but recommended)
cd web && pnpm install --frozen-lockfile && cd ..                     # only needed to build the UI

cd backend
uv run companyos doctor                  # what is missing on this machine, and the fix for each item
uv run companyos up                      # http://127.0.0.1:8700
```

`companyos up` does, in order:

1. refuses to start if the port is busy;
2. migrates the database to the current version (`alembic upgrade head`; creates it on first run);
3. builds `web/dist` with `pnpm build` when it is missing or older than the sources (skip with `--no-build`; a
   failed build is reported and the API still starts);
4. starts the API through `python -m app.serve`, which applies your saved **Local network access** preference
   (and still refuses to bind to the network unless password sign-in is set up), and serves the UI from the same port;
5. waits for `/api/health`, then starts the worker/scheduler as a **supervised child**: if it crashes it is
   restarted after 1 s, 2 s, 4 s ... capped at 60 s (a run that was in flight is recovered by the engine: it is
   marked `worker_lost` and retried);
6. prints the local, network and public URLs and, on the first run, the setup URL.

`Ctrl-C` (or `SIGTERM`) stops the worker first, then the API (each gets 20 s to finish before it is killed).
If the API itself dies, everything stops and `companyos up` exits non-zero.

| flag | meaning |
|---|---|
| `--port 8700` | port for API + UI (the worker is told the same port for its agent API calls) |
| `--host H` | override the bind address (still held to the exposure gate); default is the saved preference |
| `--no-worker` | API only (run `companyos-worker` yourself, e.g. in another terminal or container) |
| `--dev` | start the Vite dev server (hot reload, proxying `/api` to this API) instead of serving the built UI; open the URL Vite prints |
| `--open` | open the browser when ready |
| `--no-build` | never run `pnpm build` |

**First run.** Open the printed URL on this machine and create your account (username + password; no setup token is
needed from the machine itself). From another device you also need the **setup token** (it proves you control the
server): `companyos auth setup-token` on the server prints it while setup is pending; it is also in
`<data dir>/owner.token` (mode 0600). `companyos up` prints it only when it is on a terminal **and** the server is
reachable beyond this machine (network bind or a running tunnel); it is never written to a log.

Data lives in `data/` at the repository root by default (`company_os.db`, `secret.key`, `owner.token`, `artifacts/`,
`skills/`, `agents/`, `workspaces/`). Change it with `COMPANY_OS_DATABASE_URL` / `COMPANY_OS_DATA_DIR` (section 7).

You can still run the pieces yourself: `uv run alembic upgrade head`, `uv run python -m app.serve --port 8700`,
`uv run companyos-worker` (or `COMPANY_OS_EMBEDDED_WORKER=true` to run the worker inside the API process, as the
M2-M8 dev setup did).

### Ollama

Company OS talks to Ollama's OpenAI-compatible endpoint, which **ignores `num_ctx`**. Start Ollama with the context
size the agents need or long prompts are silently truncated:

```bash
OLLAMA_CONTEXT_LENGTH=32768 ollama serve          # or: launchctl setenv OLLAMA_CONTEXT_LENGTH 32768 (macOS app)
companyos providers detect --register              # finds Ollama, picks a tool-capable model, registers it
companyos providers test "Ollama 127.0.0.1:11434"  # loads the model, reports the served context (warns if < 32K)
```

`companyos doctor` shows the served context of any loaded model and prints this fix when it is below 32K.
A 7B model runs 1-6 minutes per task and follows multi-step tool instructions only partly; a larger local model or a
cloud key is the biggest lever for quality (the free-first router uses the local model unless told otherwise).

## 3. The CLI

`companyos` talks to the API over HTTP, so it works from another machine too. Every command accepts `--json`
(machine-readable output; errors then go to stderr as `{"error": {code, message, details}}`).

**Configuration.** `~/.config/companyos/config.toml` (`api_url`, `company_id`; no secrets, mode 0600) and
`~/.config/companyos/token` (mode 0600). `COMPANYOS_CONFIG_DIR` moves both. Global options: `--api-url`,
`--company` (id or name; optional when you belong to exactly one). Environment: `COMPANYOS_API_URL`,
`COMPANYOS_COMPANY`, `COMPANYOS_TOKEN` (for CI).

**Credentials, in this order:** `COMPANYOS_TOKEN` -> the stored personal API token -> on the server machine, the
owner token file (`<data dir>/owner.token`). Secrets are **never** accepted as arguments: passwords and credential
values come from a hidden prompt or from stdin (`--password-stdin`, `credentials set NAME --stdin`). Tokens are
never printed (the only place a token is shown is the web app's "create token" dialog).

| command | does |
|---|---|
| `up [--port --host --no-worker --dev --open --no-build]` | section 2 |
| `doctor` | Python/uv, web build, database + migrations, agent runtime import, Ollama + served context, Docker, tunnel tools, disk, secret-file permissions, auth mode + exposure gate, running processes. Exit 1 on a failed check |
| `login [-u USER] [--password-stdin] [--scope read\|write\|admin] [--set-company X]` | username + password -> creates a personal API token (default scope `admin`) and stores it; the password is not kept and the browser-style session is closed again. 2FA accounts are asked for a code (second stdin line with `--password-stdin`) |
| `logout`, `whoami` | forget the stored token (revoke it in the web app: Account > API tokens; a token cannot revoke itself), show the account and which credential is used |
| `company ls / create NAME` | |
| `status` | company, AI Co-founder state, active goals, headcount by role family and by live state, task counts, approvals waiting, spend today/month split verified / estimated / unknown, open alerts, reachability, emergency stop |
| `note "text"` (or `-` for stdin), `notes ls / show ID` | Founder's notes, their triage and the Co-founder's replies |
| `goal ls / add TITLE [--priority --target --draft]` | |
| `agents ls / show / templates / hire -t TEMPLATE -n NAME [--manager --monthly-budget --skill --provider --model --no-activate] / pause / resume / retire / skills NAME [--add --remove]` | |
| `tasks ls / show / create TITLE [--assignee --criteria --budget --depends-on ...] / cancel / retry` | ids may be abbreviated to a unique prefix; agents by name |
| `runs tail [--agent X \| --task ID \| --run ID] [-f] [-n N]` | the events of a run; `-f` streams live events (server-sent events) until the run finishes or Ctrl-C |
| `skills ls / search QUERY [--github] / install SOURCE_REF / show` | `hermes:<slug>`, `github:<owner>/<repo>:<path>` or a GitHub URL; scanned first, unsafe ones are quarantined |
| `approvals ls [--status] / approve ID / reject ID [--note]` | |
| `budget show / set [--daily --monthly] [--agent NAME]`, `policy show / set key=value ...` | USD for budgets (`none` clears); policy values are JSON or text, money keys end in `_micros` |
| `pause`, `resume` | pause / resume the company |
| `stop --all [--reason]`, `resume --all` | the **instance-wide emergency stop** (owner only; `stop` without `--all` is refused) and lifting it |
| `providers ls / detect [--base-url --register] / test NAME`, `credentials ls / set NAME [--stdin]` | model providers; write-only secrets |
| `reach` | mode, local / network / public URLs, a QR code for the network address |
| `backup create / restore` | section 6 |
| `auth reset-password USERNAME`, `auth setup-token` | **server machine only**: they read the data dir directly and need to read `owner.token` (exit 4 otherwise) |

**Exit codes:** 0 ok - 1 error (the API's message is printed) - 2 usage - 3 not signed in / credential rejected -
4 forbidden - 5 not found - 6 conflict (e.g. invalid transition) - 7 server unreachable.

`auth reset-password` sets a new password under the same strength policy as the web app, signs the account out
everywhere (`--revoke-tokens` also revokes its API tokens), clears its login lockout and writes an audit event. It is
deliberately not an HTTP endpoint: being able to read `owner.token` on the machine is the proof of ownership.

## 4. Docker Compose

> **Status: written, not built or run.** No Docker daemon was available during M9. `docker compose config` accepts
> all three files; the image build and the stack are unverified. Expect to fix small things on first use and
> please report them.

```bash
cp .env.example .env                 # edit: ports, public URL, optional secret key
docker compose up -d --build         # migrate -> api -> worker
docker compose exec api companyos auth setup-token      # first run from another device
open http://localhost:8700
```

* `Dockerfile` (repository root): multi-stage - web build (node), Python environments (backend with `uv sync --frozen
  --no-dev`; the agent runtime venv from Hermes' lockfile), final slim image running as a **non-root** user (uid
  10001), the built UI at `/app/web/dist`, data in the `/data` volume. No secrets are baked in; build args:
  `WITH_RUNTIME=0` (leave the ~180 MB runtime out), `WITH_POSTGRES=1` (psycopg driver), `INSTALL_DOCKER_CLI=1`.
* `docker-compose.yml`: `migrate` (one-shot `alembic upgrade head`) -> `api` (embedded worker **off**, health check on
  `/api/health`, published on `127.0.0.1` only) -> `worker` (`companyos-worker`, told to reach the API at
  `http://api:8700`), a `data` volume, and an optional `postgres` service behind `--profile postgres`.
* **Network exposure** is a deliberate step: set `COMPANYOS_BIND=0.0.0.0` in `.env` only after the owner account has a
  strong password (and 2FA), and read `docs/deploy-reachability.md`. Inside the container the server binds `0.0.0.0`, so
  the compose port mapping is the only gate; the default maps loopback.
* **Agent sandbox through the host Docker socket is opt-in and dangerous.** `docker-compose.sandbox.yml` mounts
  `/var/run/docker.sock` into the worker; anyone who controls that container (or a prompt-injected agent that escapes
  its tools) controls the host. Prefer `companyos up` on a host with Docker, or a rootless/remote daemon. Without it,
  agents have no terminal/code-execution tools in the container (the file tools stay jailed to the run workspace).
* Ollama on the host: register `http://host.docker.internal:11434` as the provider base URL (`extra_hosts` is set on
  the worker).
* Reverse proxy / TLS: terminate TLS in front and set `COMPANY_OS_COOKIE_SECURE=1`; uvicorn trusts `X-Forwarded-*`
  from any peer inside the compose network (`--forwarded-allow-ips *`), so do not publish the API port directly
  next to an untrusted proxy.

## 5. Upgrading

```bash
companyos backup create                 # always first
git pull
cd backend && uv sync                   # (and `cd runtime && uv sync ...` / `cd web && pnpm install` if their lockfiles changed)
uv run companyos up                     # migrates the database to the new version on start, rebuilds a stale UI
```

Docker: `git pull && docker compose up -d --build` (the `migrate` service runs `alembic upgrade head` before `api`).
Migrations only go forward; to go back, restore the backup taken before upgrading. A backup made by a newer
version than the running code is refused by `restore`.

## 6. Backup and restore

```bash
companyos backup create                          # -> <data dir>/backups/companyos-backup-<UTC time>.tar.gz
companyos backup create --out /mnt/safe/       # or choose where
companyos backup restore ARCHIVE --verify-only   # check every checksum, change nothing
companyos backup restore ARCHIVE                 # into an EMPTY / new data directory
companyos backup restore ARCHIVE --force         # replace an existing install (asks; --yes answers for scripts)
```

* **What is in it:** a consistent snapshot of the database made with SQLite's online backup API (safe while the server is
  running and writing), plus `secret.key`, `owner.token`, `artifacts/`, `skills/` and `agents/` (agent homes). The
  archive has a `manifest.json` with a SHA-256 and size for every file. **Not included:** `workspaces/` (run scratch
  space), `bin/` (downloaded helper binaries such as cloudflared), `run/`, earlier backups.
* **It contains your secret key and owner token** (without the key, stored credentials cannot be decrypted). The file
  is created mode 0600; keep it encrypted wherever you copy it (`age`, `gpg`, an encrypted volume).
* **Restore is conservative.** It verifies the whole archive against the manifest (and the database's integrity check)
  *before* touching anything, and refuses: damaged, truncated or modified archives (a changed byte, an added or missing
  file, unsafe paths, links); a data directory a Company OS process is using (API or worker; they register a pid file in
  `<data dir>/run/`); an archive from a newer version; an existing install without `--force` and a confirmation. With
  `--force` the current data is **moved aside** to `<data dir>.pre-restore-<time>` (never deleted); delete it yourself
  when you are satisfied. `--json` mode never prompts: combine `--force --yes` for scripts.
* Limits: a server started before this version (without run files) is not detected - stop it yourself. The data directory
  is also used by `--data-dir DIR` (both commands) to work on a directory other than the configured one.
* **PostgreSQL** installs are not covered: use `pg_dump` / `pg_restore` for the database and copy `secret.key`,
  `owner.token`, `artifacts/`, `skills/`, `agents/` yourself.

## 7. Configuration reference

All settings are environment variables with the `COMPANY_OS_` prefix (also read from `backend/.env`). Unset = default.

| variable | default | meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///../data/company_os.db` | SQLAlchemy URL; relative SQLite paths are relative to `backend/` (use `sqlite:////abs/path.db`). PostgreSQL: `postgresql+psycopg://...` (needs the `postgres` extra; **unverified**) |
| `DATA_DIR` | the database file's folder, else `<repo>/data` | artifacts, workspaces, skills, agent homes, `secret.key`, `owner.token` |
| `SECRET_KEY` | generated into `data/secret.key` | encrypts stored credentials, signs run tokens and CSRF tokens. Losing it makes stored credentials unreadable |
| `AUTH_MODE` | `password` | `password` (accounts, sessions) or `local` (**development only**: owner token gate, refuses non-loopback binds and clients) |
| `BIND_HOST` / `PORT` | `127.0.0.1` / `8700` | what the process is bound to / listens on (set by `app.serve` and `companyos up`; used for the exposure gate and the tunnel) |
| `PUBLIC_URL` | `http://localhost:8700` | the address people type; used in printed links |
| `CORS_ORIGINS` | the Vite dev origins | JSON list of allowed browser origins (credentials allowed) |
| `COOKIE_SECURE` | auto (https => Secure) | force the Secure flag behind a TLS-terminating proxy |
| `EMBEDDED_WORKER` | `false` | run worker + scheduler inside the API process (`companyos up` and compose run them separately) |
| `WORKER_CONCURRENCY`, `ENGINE_TICK_SECONDS`, `ENGINE_POLL_SECONDS` | `4`, `2.0`, `0.5` | engine tuning |
| `AGENT_API_URL` | `http://127.0.0.1:8700/api/agent/v1` | where runtimes reach the agent API (`up` derives it from `--port`; compose sets `http://api:8700/...`) |
| `RUNTIME_DIR`, `RUNTIME_PYTHON` | `<repo>/runtime`, `<runtime>/.venv/bin/python` | agent runtime checkout and its interpreter |
| `RUNTIME_KILL_GRACE_SECONDS`, `RUNTIME_HEALTH_TIMEOUT_SECONDS` | `10`, `15` | |
| `SANDBOX_DOCKER_IMAGE` | `python:3.12-slim` | image for sandboxed terminal tools (Docker backend) |
| `SKILL_SCAN_TIMEOUT_SECONDS`, `GITHUB_TOKEN`, `GITHUB_API_URL` | `120`, unset, GitHub | skill import; the token only raises GitHub rate limits and goes only to `api.github.com` |
| `MAX_UPLOAD_BYTES`, `WEBHOOK_MAX_BYTES`, `HOSTED_UI_MAX_BYTES` | 25 MiB, 1 MiB, 40 MiB | size caps |
| `WEB_DIST_DIR`, `SERVE_WEB` | `<repo>/web/dist`, `true` | the built UI served on the API port (also what "hosted UI" uploads) |
| `PRINT_LOGIN_URL` | print only on a terminal | force (`1`) or silence (`0`) the printed login/setup hint |
| `ARGON2_TIME_COST`, `ARGON2_MEMORY_KIB`, `ARGON2_PARALLELISM` | `3`, `65536`, `4` | password hashing cost |
| `SESSION_IDLE_DAYS`, `SESSION_ABSOLUTE_DAYS`, `INVITATION_TTL_DAYS` | `14`, `30`, `7` | |
| `LOGIN_FREE_ATTEMPTS`, `LOGIN_MAX_LOCKOUT_SECONDS` | `3`, `900` | login throttling |
| `BIN_DIR`, `CLOUDFLARED_PATH`, `TAILSCALE_PATH`, `CLOUDFLARED_RELEASE_API` | `<data>/bin`, auto, auto, GitHub | tunnel binaries |
| `TUNNEL_HEALTH_INTERVAL_SECONDS`, `TUNNEL_START_TIMEOUT_SECONDS`, `TUNNEL_MAX_BACKOFF_SECONDS`, `TUNNEL_MAX_CONSECUTIVE_FAILURES`, `TUNNEL_AUTORESUME` | `30`, `45`, `60`, `8`, `true` | tunnel supervision |
| `ENABLE_TEST_RUNTIMES` | `false` | registers the deterministic test runtime. **Never in production** |
| `HERMES_API_URL`, `HERMES_API_KEY` | unset | legacy external Hermes server runtime (no prefix) |

Not prefixed: `OLLAMA_HOST` / `OLLAMA_CONTEXT_LENGTH` (Ollama itself); `COMPANYOS_CONFIG_DIR`, `COMPANYOS_API_URL`,
`COMPANYOS_COMPANY`, `COMPANYOS_TOKEN` (the CLI); compose: `COMPANYOS_PORT`, `COMPANYOS_BIND`, `POSTGRES_PASSWORD`,
`WITH_RUNTIME`, `WITH_POSTGRES` (see `.env.example`).

## 8. HTTP serving details

The API port also serves the web UI: hashed `/assets/*` are `Cache-Control: public, max-age=31536000, immutable`;
`index.html`, `theme-init.js` and the favicon are `no-cache` with an `ETag` (conditional requests answer 304); any
path without a file extension that matches no file gets `index.html` (client-side routes), a missing file with an
extension is a 404; `/api/*` is never answered by the UI layer. Every response carries `X-Content-Type-Options:
nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, `Permissions-Policy`, `Cross-Origin-Opener-Policy:
same-origin` (HSTS additionally when the request arrived over https). Details and the CSP: `docs/security.md`.

## 9. Troubleshooting

`companyos doctor` first. Common cases: *"Port 8700 is already in use"* - another instance runs (`doctor` lists
registered processes); *"Database schema is missing or out of date"* - `uv run alembic upgrade head`;
*runs fail immediately with a credential error* - the provider needs a credential (`credentials set`, then attach it in
Integrations); *Ollama prompts truncated* - restart Ollama with `OLLAMA_CONTEXT_LENGTH=32768`; *the UI shows a blank
page after an upgrade* - hard-reload (the entry file is `no-cache`, but a stale tab keeps old chunks).

## 10. Verified / not verified

See "Final verification" in `docs/roadmap.md` for the dated table produced by `scripts/verify.sh`. Not verified in M9:
the Docker image build and Compose stack, PostgreSQL (no server was available; migrations are written to be portable but
the suite has only ever run on SQLite), the Docker-socket sandbox, running behind a TLS proxy, Windows, and Linux
(everything was run on macOS).
