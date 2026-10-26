# Deploying and operating Foundry AI OS (M9, M11)

This is the operator guide: run it on your own machine, in Docker, upgrade it, back it up, configure it.
Network access from other devices and the public internet is a separate guide: `docs/deploy-reachability.md`.
What was and was not verified is listed in section 10 and in `docs/roadmap.md` ("Final verification").
Installed copies (Homebrew, the one-line installer, Docker) are covered in section 11; this page's other sections describe the same program run from a source checkout.

## 1. Requirements

| | |
|---|---|
| Python | 3.12+ (the dev machine uses 3.14); managed with [`uv`](https://docs.astral.sh/uv/) |
| Node | 20+ and `pnpm` (only to build the web UI; not needed if `web/dist` is already built) |
| Agent runtime | `runtime/` has its own virtualenv (Hermes fork, Python 3.14, ~180 MB). Without it the API and UI run, but agents cannot execute and non-built-in skills cannot be scanned |
| A model | a local [Ollama](https://ollama.com) (free) or a cloud key (OpenRouter, Anthropic, OpenAI, any OpenAI-compatible endpoint) |
| Disk | a few hundred MB plus your data; `foundryos doctor` warns below 2 GB free |

## 2. Run it locally: `foundryos up`

```bash
git clone <this repo> company-os && cd company-os

cd backend && uv sync && cd ..                                        # the control plane + the `foundryos` command (alias `companyos`)
cd runtime && uv sync --frozen --no-install-project \
      --extra anthropic --extra ddgs --python 3.14 && cd ..           # the agent runtime (optional but recommended)
cd web && pnpm install --frozen-lockfile && cd ..                     # only needed to build the UI

cd backend
uv run foundryos doctor                  # what is missing on this machine, and the fix for each item
uv run foundryos up                      # http://127.0.0.1:8700
```

`foundryos up` does, in order:

1. refuses to start if the port is busy;
2. migrates the database to the current version (`alembic upgrade head`; creates it on first run). When the program version changed since the last start, or migrations are pending, it first takes an automatic `pre-<version>-<time>.tar.gz` backup (section 5) and refuses to start if that backup or the migration fails;
3. builds `web/dist` with `pnpm build` when it is missing or older than the sources (skip with `--no-build`; a
   failed build is reported and the API still starts);
4. starts the API through `python -m app.serve`, which applies your saved **Local network access** preference
   (and still refuses to bind to the network unless password sign-in is set up), and serves the UI from the same port;
5. waits for `/api/health`, then starts the worker/scheduler as a **supervised child**: if it crashes it is
   restarted after 1 s, 2 s, 4 s ... capped at 60 s (a run that was in flight is recovered by the engine: it is
   marked `worker_lost` and retried);
6. prints the local, network and public URLs and, on the first run, the setup URL.

`Ctrl-C` (or `SIGTERM`) stops the worker first, then the API (each gets 20 s to finish before it is killed).
If the API itself dies, everything stops and `foundryos up` exits non-zero.

| flag | meaning |
|---|---|
| `--port 8700` | port for API + UI (default: `FOUNDRYOS_PORT`, else 8700; the worker is told the same port for its agent API calls) |
| `--host H` | override the bind address (still held to the exposure gate); default is the saved preference |
| `--no-worker` | API only (run `companyos-worker` yourself, e.g. in another terminal or container) |
| `--dev` | start the Vite dev server (hot reload, proxying `/api` to this API) instead of serving the built UI; open the URL Vite prints |
| `--open` | open the browser when ready |
| `--no-build` | never run `pnpm build` |

**First run.** Open the printed URL on this machine and create your account (username + password; no setup token is
needed from the machine itself). From another device you also need the **setup token** (it proves you control the
server): `foundryos auth setup-token` on the server prints it while setup is pending; it is also in
`<data dir>/owner.token` (mode 0600). `foundryos up` prints it only when it is on a terminal **and** the server is
reachable beyond this machine (network bind or a running tunnel); it is never written to a log.

Data lives in `data/` at the repository root by default in a source checkout (an installed copy uses the per-user folder, section 11) (`company_os.db`, `secret.key`, `owner.token`, `artifacts/`,
`skills/`, `agents/`, `workspaces/`). Change it with `COMPANY_OS_DATABASE_URL` / `COMPANY_OS_DATA_DIR` (section 7).

You can still run the pieces yourself: `uv run alembic upgrade head`, `uv run python -m app.serve --port 8700`,
`uv run companyos-worker` (or `COMPANY_OS_EMBEDDED_WORKER=true` to run the worker inside the API process, as the
M2-M8 dev setup did).

### Ollama

Foundry AI OS talks to Ollama's OpenAI-compatible endpoint, which **ignores `num_ctx`**. Start Ollama with the context
size the agents need or long prompts are silently truncated:

```bash
OLLAMA_CONTEXT_LENGTH=32768 ollama serve          # or: launchctl setenv OLLAMA_CONTEXT_LENGTH 32768 (macOS app)
foundryos providers detect --register              # finds Ollama, picks a tool-capable model, registers it
foundryos providers test "Ollama 127.0.0.1:11434"  # loads the model, reports the served context (warns if < 32K)
```

`foundryos doctor` shows the served context of any loaded model and prints this fix when it is below 32K.
A 7B model runs 1-6 minutes per task and follows multi-step tool instructions only partly; a larger local model or a
cloud key is the biggest lever for quality (the free-first router uses the local model unless told otherwise).

## 3. The CLI

`foundryos` talks to the API over HTTP, so it works from another machine too. Every command accepts `--json`
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
| *(no arguments)* | start in the background if needed, then open the browser (section 11) |
| `start`, `stop`, `restart`, `open`, `logs [-f]`, `version` | run it as a background program (section 11). `stop --all` is still the instance-wide emergency stop |
| `serve [--port --host]` | foreground, no browser, never builds the UI: what the background service runs |
| `service install [--linger] / uninstall / status` | start at login (section 11) |
| `update [--version X] [--check] [--auto-check/--no-auto-check]`, `rollback [--restore-data]`, `uninstall [--purge]` | installer installs (section 11) |
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
docker compose exec api foundryos auth setup-token      # first run from another device
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
  its tools) controls the host. Prefer `foundryos up` on a host with Docker, or a rootless/remote daemon. Without it,
  agents have no terminal/code-execution tools in the container (the file tools stay jailed to the run workspace).
* Ollama on the host: register `http://host.docker.internal:11434` as the provider base URL (`extra_hosts` is set on
  the worker).
* Reverse proxy / TLS: terminate TLS in front and set `COMPANY_OS_COOKIE_SECURE=1`; uvicorn trusts `X-Forwarded-*`
  from any peer inside the compose network (`--forwarded-allow-ips *`), so do not publish the API port directly
  next to an untrusted proxy.

## 5. Upgrading

```bash
foundryos backup create                 # optional: the start below also takes one
git pull
cd backend && uv sync                   # (and `cd runtime && uv sync ...` / `cd web && pnpm install` if their lockfiles changed)
uv run foundryos up                     # migrates the database to the new version on start, rebuilds a stale UI
```

**Every version change takes an automatic backup first.** When `up` / `serve` starts and the version (the `VERSION` file)
differs from the one that last started successfully, or the database is behind the code, it writes
`<data dir>/backups/pre-<new version>-<UTC time>.tar.gz` with the same code as `backup create`, keeps the five newest
`pre-*` archives (manual backups are never deleted), and only then runs `alembic upgrade head`. The last-started version
is remembered in `<data dir>/state/version.json`. If the backup fails, the migration fails, or the database was last
used by a *newer* version, the server does not start and prints how to go back (`foundryos rollback` for installer
installs, otherwise the previous version plus `foundryos backup restore "<the pre- archive>" --force`).
`FOUNDRYOS_SKIP_PRE_UPGRADE_BACKUP=1` skips the backup (not recommended). PostgreSQL installs are migrated without a file backup.

Docker: `git pull && docker compose up -d --build` (the `migrate` service runs `alembic upgrade head` before `api`; that
path does not take the automatic backup, so run `backup create` first).
Migrations only go forward; to go back, restore the backup taken before upgrading. A backup made by a newer
version than the running code is refused by `restore`.

## 6. Backup and restore

```bash
foundryos backup create                          # -> <data dir>/backups/companyos-backup-<UTC time>.tar.gz
foundryos backup create --out /mnt/safe/       # or choose where
foundryos backup restore ARCHIVE --verify-only   # check every checksum, change nothing
foundryos backup restore ARCHIVE                 # into an EMPTY / new data directory
foundryos backup restore ARCHIVE --force         # replace an existing install (asks; --yes answers for scripts)
```

* **What is in it:** a consistent snapshot of the database made with SQLite's online backup API (safe while the server is
  running and writing), plus `secret.key`, `owner.token`, `artifacts/`, `skills/` and `agents/` (agent homes). The
  archive has a `manifest.json` with a SHA-256 and size for every file. **Not included:** `workspaces/` (run scratch
  space), `bin/` (downloaded helper binaries such as cloudflared), `run/`, earlier backups.
* **It contains your secret key and owner token** (without the key, stored credentials cannot be decrypted). The file
  is created mode 0600; keep it encrypted wherever you copy it (`age`, `gpg`, an encrypted volume).
* **Restore is conservative.** It verifies the whole archive against the manifest (and the database's integrity check)
  *before* touching anything, and refuses: damaged, truncated or modified archives (a changed byte, an added or missing
  file, unsafe paths, links); a data directory a Foundry AI OS process is using (API or worker; they register a pid file in
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
| `DATABASE_URL` | `sqlite:///../data/company_os.db` (installed copies: `<data dir>/company_os.db`) | SQLAlchemy URL; relative SQLite paths are relative to `backend/` (use `sqlite:////abs/path.db`). PostgreSQL: `postgresql+psycopg://...` (needs the `postgres` extra; **unverified**) |
| `DATA_DIR` | `FOUNDRYOS_DATA_DIR`, else (installed copy) the per-user folder, else the database file's folder, else `<repo>/data` | artifacts, workspaces, skills, agent homes, `secret.key`, `owner.token` |
| `SECRET_KEY` | generated into `data/secret.key` | encrypts stored credentials, signs run tokens and CSRF tokens. Losing it makes stored credentials unreadable |
| `AUTH_MODE` | `password` | `password` (accounts, sessions) or `local` (**development only**: owner token gate, refuses non-loopback binds and clients) |
| `BIND_HOST` / `PORT` | `127.0.0.1` / `8700` | what the process is bound to / listens on (set by `app.serve` and `foundryos up`; used for the exposure gate and the tunnel). `FOUNDRYOS_PORT` sets the port the same way |
| `UPDATE_CHECK` | `true` | look for a newer release at most once a day (`FOUNDRYOS_UPDATE_CHECK=0` or the owner's stored preference turns it off) |
| `PUBLIC_URL` | `http://localhost:8700` | the address people type; used in printed links |
| `CORS_ORIGINS` | the Vite dev origins | JSON list of allowed browser origins (credentials allowed) |
| `COOKIE_SECURE` | auto (https => Secure) | force the Secure flag behind a TLS-terminating proxy |
| `EMBEDDED_WORKER` | `false` | run worker + scheduler inside the API process (`foundryos up` and compose run them separately) |
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

Product-named aliases (a `COMPANY_OS_*` variable that is set wins): `FOUNDRYOS_DATA_DIR`, `FOUNDRYOS_PORT`,
`FOUNDRYOS_UPDATE_CHECK`. Also read: `FOUNDRYOS_INSTALLED` (`1` forces installed mode, `0` forces source mode; the
release launcher sets it), `FOUNDRYOS_LOG_FILE` (a log file to rotate and show with `logs`, default
`<data dir>/logs/foundry-os.log`), `FOUNDRYOS_LAUNCHER` (the command a background start / the service runs, default the
stable launcher of this install), `FOUNDRYOS_NO_BROWSER=1` (never open a browser), `FOUNDRYOS_DOWNLOAD_BASE`
(`foundryos update` downloads `foundry-os-<v>-<platform>.tar.gz` and `SHA256SUMS` from there; `file://` works),
`FOUNDRYOS_SKIP_PRE_UPGRADE_BACKUP`.

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

`foundryos doctor` first (it also lists the install kind, the data folder and what the last update check found). Common cases: *"Port 8700 is already in use"* - another instance runs (`doctor` lists
registered processes); *"Database schema is missing or out of date"* - `uv run alembic upgrade head`;
*runs fail immediately with a credential error* - the provider needs a credential (`credentials set`, then attach it in
Integrations); *Ollama prompts truncated* - restart Ollama with `OLLAMA_CONTEXT_LENGTH=32768`; *the UI shows a blank
page after an upgrade* - hard-reload (the entry file is `no-cache`, but a stale tab keeps old chunks).

## 10. Verified / not verified

See "Final verification" in `docs/roadmap.md` for the dated table produced by `scripts/verify.sh`. Not verified in M9:
the Docker image build and Compose stack, PostgreSQL (no server was available; migrations are written to be portable but
the suite has only ever run on SQLite), the Docker-socket sandbox, running behind a TLS proxy, Windows, and Linux
(everything was run on macOS).

## 11. Installed copies (M11)

The installer, Homebrew and Docker run a self-contained release bundle (its own Python, the agent runtime and the built
UI). The program is the same; what differs is where things live and how you run, update and remove it.

**How a copy knows what it is.** `foundryos version --json` shows `install_kind`: `source` (a git checkout),
`installer` (`~/.foundry-os/versions/<v>` + `~/.foundry-os/current`), `homebrew` (inside a Homebrew `Cellar`), `bundle`
(a release unpacked by hand) or `docker`. Everything below follows from it.

**Data folder** (installed copies; a source checkout keeps `<repo>/data`):

| | |
|---|---|
| macOS | `~/Library/Application Support/Foundry AI OS` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/foundry-os` |
| anywhere | `FOUNDRYOS_DATA_DIR` (also moves the database) |

It holds `company_os.db`, `secret.key`, `owner.token`, `artifacts/`, `skills/`, `agents/`, `backups/`, `logs/` (the server log,
rotated at 10 MB, 5 kept), `run/` (pid files) and `state/` (the last-started version, the update-check cache).

**Running.** `foundryos` (no arguments) starts a detached `foundryos serve` (a pid file in `run/`, output in
`logs/foundry-os.log`) unless it is already answering on the port, waits for `/api/health` and opens the browser (not on a
headless box, see below);
`start`, `stop`, `restart`, `open`, `logs` are the pieces. "Running" means a Foundry health answer on the port; "who
started it" comes from the run files and the service manager. If the port answers but nothing here started it,
`stop` says so instead of killing a stranger.

**The service.** `foundryos service install` creates the macOS LaunchAgent `ai.foundry-os` (`RunAtLoad`, `KeepAlive`,
`ThrottleInterval 10`, logs in the data folder) or the Linux user unit `foundry-os.service` (`Restart=on-failure`, log
appended to the same file; no `loginctl enable-linger` unless `--linger`). Both run `foundryos serve` through the
stable launcher path, so an update needs only a restart. Environment baked in: a PATH that includes `~/.local/bin`
and the usual system folders, and `FOUNDRYOS_DATA_DIR` / `FOUNDRYOS_PORT` / `FOUNDRYOS_UPDATE_CHECK` if they were set
when you installed it. With Homebrew, the formula's `service do` block (`brew services`) is the only service: `service
install` defers to it, `start` / `stop` / `restart` drive it through `brew services`, and an existing Homebrew service is
never duplicated even by an installer copy.

**Headless boxes, linger and launchd domains (M13).** `app/ops/headless.py` decides whether there is a screen: `FOUNDRYOS_OPEN_BROWSER=1` says
yes; `FOUNDRYOS_NO_BROWSER`, `SSH_CONNECTION` / `SSH_TTY`, Linux without `DISPLAY` and `WAYLAND_DISPLAY`, or `/.dockerenv` say no. With no screen
`foundryos`, `start`, `open` and `up` never open a browser (and never let `webbrowser` start a text-mode one); they print the local URL, the SSH port-forward
(`ssh -N -L <port>:127.0.0.1:<port> user@host`) and the public URL from `<data>/public-url.json`, and offer `foundryos setup` while the install is unclaimed.
`foundryos doctor` has a `headless` line (info).

*Linux.* `systemctl --user` fails with "Failed to connect to bus" in a shell without a user session bus (`su`, `sudo -u`, some SSH setups). Foundry retries
once with `XDG_RUNTIME_DIR=/run/user/<uid>` and `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/<uid>/bus` when that directory exists. A user unit stops at the
user's last logout unless *linger* is on: `service install --linger`, `install.sh --linger` / `FOUNDRYOS_LINGER=1` and the setup wizard (default on) run
`loginctl enable-linger <user>` as that user and never use `sudo`; if it is refused the output says to run `sudo loginctl enable-linger <user>` yourself. On a
headless box `service install` without `--linger` warns. `doctor` reports `linger` (warns when off and headless) and `service lifetime` (warns when the
service stops at logout). When the service cannot be started at all, `foundryos start` runs a plain detached background process instead and says so.

*macOS.* A LaunchAgent is bootstrapped into `gui/<uid>` first (needs a graphical login) and into `user/<uid>` when that is refused (a Mac reached only over
SSH). The domain that worked is written to `~/Library/LaunchAgents/ai.foundry-os.domain`; `status`, `start`, `restart`, `stop` and `uninstall` look in both.
In `user/<uid>` the job runs and is restarted by launchd but is not started after a reboot until someone logs in. For start at boot without a login use
automatic login or a **LaunchDaemon**: `service install` writes `<data>/ai.foundry-os.daemon.plist` (it runs as your user, with `HOME` set, because launchd
does not set it for daemons) and prints the commands, which you run yourself: `foundryos service uninstall`, then `sudo cp` the plist to
`/Library/LaunchDaemons/ai.foundry-os.plist`, `sudo chown root:wheel`, `sudo chmod 644`, `sudo launchctl bootstrap system /Library/LaunchDaemons/ai.foundry-os.plist`.
`doctor` reports `service lifetime` for both cases.

**Updates.** Installer: `foundryos update` / `rollback` (see `docs/guide/updates-backups.md`). Homebrew: `brew upgrade
foundry-os`. The check for a newer release is cached for 24 hours in `state/update-check.json` and exposed to the owner as
`GET /api/system/update` (and `PUT /api/system/update/settings {"enabled": bool}`, audited), `foundryos status`, `doctor`
and a dismissible banner in the web app.

**Uninstall.** `foundryos uninstall [--purge]` (see the same guide). The installer's own `--uninstall` does the same
without needing a working install.

**What was and was not verified (M11 app side).** Verified by tests: data-folder resolution per OS (monkeypatched),
service files and the `launchctl` / `systemctl` / `brew` call sequences (fake programs, a temporary HOME; no real agent
was ever installed), `update` / `rollback` against a fake GitHub API and local tarballs (checksum mismatch, tampered file,
missing `SHA256SUMS`, wrong version inside, unsafe paths all refused), the update-check cache and opt-outs, the
pre-migration backup and its retention, uninstall safety, and start / restart / stop against a real detached process.
Run by hand from a source checkout with a temporary data folder on port 8713: start, status, logs, stop, restart, the
automatic backup on a version change. **Not verified:** a real LaunchAgent / systemd unit loading (macOS and Linux
service managers), `brew services`, a real update from a published GitHub release (none exists yet), the bundle launcher
end to end (owned by the packaging work), Windows. M13's headless additions (the `user/<uid>` fallback, the LaunchDaemon steps, the systemd bus retry,
`loginctl enable-linger`) were also tested only through fake `launchctl`, `systemctl` and `loginctl`: no real service manager, reboot or logout was involved.
