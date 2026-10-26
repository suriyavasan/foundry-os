# M13 — Headless setup over SSH and a free public URL — spec

Owner direction (2026-10-26): most real installs happen on a box you reach over SSH (a home server, a
VPS, a Mac mini in a cupboard). There is no browser there. A person must be able to go from a fresh
install to a working company in the terminal, keep it running after logging out, and reach it from a
phone through a free public URL, without opening a router port and without paying for a domain.

Status: code committed in `099092d`; the docs and the small web changes (public-URL card, passkey reason) follow it.
**Verified with fakes only** (see section 6). No real tunnel, service manager, coding CLI or provider
was run.

## 1. Gaps found and what closes them

| # | Gap | Fix |
|---|---|---|
| 1 | Onboarding existed only in the web app. Over SSH the owner had to forward a port and use a browser on another computer | `foundryos setup`: the same onboarding in a terminal (section 2) |
| 2 | `foundryos`, `start`, `open` and `up` tried to open a browser. Over SSH from a Mac that opens Safari on the remote console; on a Linux server `webbrowser` can start a text browser that blocks the terminal | Headless detection (`app/ops/headless.py`): no browser is opened; the local URL, an SSH port-forward hint and the public URL are printed, and the wizard is offered |
| 3 | On Linux the user service stopped at the last logout, and `systemctl --user` failed with "Failed to connect to bus" in shells without a user bus (`su`, `sudo -u`, some SSH setups) | Retry with `XDG_RUNTIME_DIR=/run/user/<uid>` and the matching bus address; linger can be turned on with `loginctl enable-linger` (never `sudo`); `doctor` warns when linger is off on a headless box |
| 4 | On macOS the LaunchAgent is loaded into `gui/<uid>`, which needs a graphical login. Over SSH to a Mac nobody is logged in to, `launchctl bootstrap` is refused | Fall back to `user/<uid>`; the domain that worked is recorded next to the plist; status, stop and uninstall look in both. `service install` prints the exact `sudo` commands for a LaunchDaemon and writes the daemon plist, but never runs `sudo` |
| 5 | A quick-tunnel URL changes on every start. Nothing remembered the old one, told the owner or recorded it | URL changes are stored (the last five), written to the audit log, logged, announced to the web app, and written to `<data>/public-url.json` (section 3) |
| 6 | If the API process died hard (SIGKILL, power loss) its `cloudflared` child kept running in its own session; the next start launched a second one | The tunnel child's pid is registered in `<data>/run/tunnel.<pid>.json`; a later start stops a recorded tunnel whose command line still names `cloudflared` and whose owner is dead. A recycled pid is not touched |
| 7 | `cloudflared` read the user's own `~/.cloudflared/config.yml` and certificates, which could change what a quick tunnel does | The quick tunnel runs with an isolated empty `HOME` (`<data>/run/cloudflared-home`, mode 0700) |
| 8 | A stopped tunnel kept showing its old URL | A stop goes through the same change path, so listeners hear that the URL is gone and the file says `stopped` |
| 9 | A passkey registered on a `*.trycloudflare.com` address is orphaned when the address changes, yet counted as strong authentication for the exposure gate | Passkeys are refused on ephemeral origins (`400 passkey_origin_unsupported`) and such passkeys do not count for the gate (section 4) |

## 2. `foundryos setup`

The terminal equivalent of the web onboarding. All server state changes go over HTTP, through the same
endpoints and the same checks as the web app (decision D36). Only these things are local: starting the
server, the background service and linger, and running a coding CLI's own login in this terminal.

Steps, in order. Each asks the server first and says "Already done" when it is, so the command is safe
to run again; `--only <step>` runs one step.

| Step | What it does |
|---|---|
| `service` | Starts the server if it is not running; optionally installs the background service and (Linux) turns on linger. Skipped for a remote server and inside Docker |
| `account` | Creates the owner account (hidden password prompt; the server's password policy applies and a rejection is shown). Mints a personal admin token and saves it for the CLI (mode 0600). A server reached from another machine needs the setup token (`foundryos auth setup-token` on that machine) |
| `2fa` | Optional authenticator-app two-factor sign-in with a terminal QR code, the secret and link as a fallback, and recovery codes shown once. Strongly recommended before a public URL. Needs a terminal |
| `company` | Company name, one-line idea (at most 280 characters), timezone (default: this machine's) |
| `ai` | The model API: OpenRouter, Anthropic, OpenAI, OpenAI-compatible or Ollama. Runs the same five live checks as the web gate (reachable, auth, tool calling, context, model exists) and does not continue until they pass. A failed provider is left disabled, not half-working |
| `work` | Work mode: `api`, or `hybrid` (API plus a coding CLI). For a CLI: install it if missing (explicit consent, never `sudo`), sign in with its own login in this terminal or with an API key, run a one-prompt test capped at $0.05. Antigravity is never installed by the wizard (Google's installer is `curl \| bash`) |
| `public-url` | Offers a free public URL once: one confirmed download of `cloudflared` (version, URL and SHA-256 are shown), then the quick tunnel (section 3) |

The idea becomes the first Founder's note once the model API is ready (the server refuses notes until
then); a rerun does not create it twice.

Options: `--non-interactive` (never prompts; every needed value must be given, otherwise exit 2 and
nothing is created), `--json` (progress goes to stderr, one JSON document to stdout), `--secrets-stdin`
(see below), and one option per answer: `--username`, `--display-name`, `--email`, `--company-name`,
`--idea`, `--timezone`, `--provider`, `--model`, `--base-url`, `--allow-private-network`, `--work-mode`,
`--cli`, `--cli-auth`, `--install-cli`, `--service/--no-service`, `--linger/--no-linger`, `--2fa/--no-2fa`,
`--public-url quick|named|tailscale|off`, `--hostname`.

**Secrets never go through argv.** Passwords, API keys and the setup token come from a hidden prompt or
from `--secrets-stdin`: `key=value` lines, split on the first `=`, with only the keys `password`,
`api_key`, `cli_api_key` and `setup_token` allowed (anything else is refused, not ignored; a duplicate or
empty value is refused). `--2fa` cannot be combined with `--non-interactive` (it needs a code from the
app). In a non-interactive run the service step defaults to "no" and the public URL to "off" unless the
flag is given; passing `--public-url quick` counts as consent to the `cloudflared` download, which is
still printed.

The wizard is used from three other places: `foundryos` and `foundryos start` offer it on a headless box
with a terminal when setup is pending (`open` and `up` only print the hint `foundryos setup`),
`install.sh` hands off to it (section 5), and `company create` gained `--idea` and `--timezone`.

## 3. A free public URL

The Cloudflare quick tunnel is the default free option (decision D37): no account, no domain, no open
port. `foundryos reach public start [quick|named|tailscale] [--hostname H] [--yes]` and the wizard use the
same function (`app/cli/tunnel_flow.py`), which only talks to the API, so the exposure gate and the
checksum-verified download stay in the server. `reach public stop` turns it off and it stays off after a
restart.

**URL changes.** `on_tunnel_change` (`app/services/reachability.py`) is the tunnel supervisor's change
listener and never raises. When a running tunnel has a URL that differs from the saved one it:

1. stores `last_url`, `previous_url`, `url_changed_at`, the provider and the last five URLs in
   `system_state` (key `tunnel_url`);
2. writes a `reachability.tunnel_url_changed` audit event for each of the owner's companies, in the same
   transaction, and emits `reachability.updated` so open screens refresh;
3. logs `Public URL: <url>` at INFO (to uvicorn's logger if ours is not enabled for INFO, so it is in the
   server log);
4. writes `<data>/public-url.json` (atomic, mode 0600): `url`, `provider`, `state`, `changed_at`,
   `previous_url`. When the tunnel stops or fails the file says so with `url: null`.

An unchanged URL records nothing. `GET /api/reachability` reports `tunnel.ephemeral`, `tunnel.last_url`
and `tunnel.url_changed_at` (kept across restarts, so a stopped tunnel still shows its last address).
`foundryos status` shows the public URL first ("changes on restart" for a quick tunnel); `foundryos reach`
shows the tunnel with its last URL; `foundryos reach --watch` prints the URL now and each time it changes
until Ctrl-C, and waits out a server restart instead of failing. `foundryos`, `start` and `up` print the
URL from `public-url.json`.

**Server-sent events through a quick tunnel.** Cloudflare documents that quick tunnels do not support
server-sent events (and cap in-flight requests). The live-update stream (`GET /api/companies/{cid}/stream`)
may therefore not work through a `trycloudflare.com` address. The web app already handles this: after two
failed attempts it falls back to polling every 10 seconds (`web/src/api/stream.tsx`). The effect is
slower live updates, not a broken screen. Not observed by us on a real tunnel. For instant updates use
the SSH port-forward, a named tunnel or Tailscale Funnel.

## 4. Passkeys and ephemeral origins

A passkey is bound to the host name it was created on. A quick tunnel gets a new host name on every
start, so a passkey made there would stop working at the next restart while still counting as strong
authentication for the exposure gate. So:

- `passkeys.resolve_rp` returns nothing for a `*.trycloudflare.com` host and for the running tunnel's host
  when that tunnel is a quick tunnel. Both options routes answer `400 passkey_origin_unsupported`.
- `AuthSession` carries `passkey_supported_origin: false` and the new `passkey_unsupported_reason:
  "ephemeral_origin"`. The web app hides the passkey offers on such an origin and, where it shows a
  reason, says passkeys need a stable address.
- `auth.passkey_added` audit events now carry the `rp_id`. The exposure gate ignores passkeys registered on
  an ephemeral origin. A passkey from before this change has no `rp_id` in its event and counts as an
  ordinary one.
- A named tunnel and Tailscale Funnel have stable names and keep passkey support. Over the SSH
  port-forward (`http://localhost:8700`) passkeys work because browsers treat localhost as secure.

## 5. Headless behaviour, services and the installer

**Headless detection** (`app/ops/headless.py`), first match wins: `FOUNDRYOS_OPEN_BROWSER=1` is not
headless; `FOUNDRYOS_NO_BROWSER` is headless; `SSH_CONNECTION` or `SSH_TTY` is headless; Linux without
`DISPLAY` and `WAYLAND_DISPLAY` is headless; `/.dockerenv` is headless. A headless box never opens a
browser (text-mode browsers picked by `webbrowser` are refused too).

**Linux.** `systemctl --user` retries once with `XDG_RUNTIME_DIR` and `DBUS_SESSION_BUS_ADDRESS` pointing
at `/run/user/<uid>` when the first call fails for lack of a bus. `foundryos service install --linger`
(and the wizard, where linger defaults to on) runs `loginctl enable-linger <user>`; if that is refused it
prints `sudo loginctl enable-linger <user>` to run yourself. `foundryos start` falls back to a plain
background process when the service cannot be started.

**macOS.** `launchctl bootstrap` tries `gui/<uid>` and then `user/<uid>`. In `user/<uid>` it runs now and
restarts if it stops, but does not start after a reboot until someone logs in. For a Mac that is only
reached over SSH, `service install` prints the `sudo` steps for a LaunchDaemon that runs as your user
(remove the per-user agent first so two copies do not fight over the port).

**`doctor`** gained three checks: `headless` (info), `linger` (Linux; warning when off on a headless box)
and `service lifetime` (warns when the service stops at logout or does not start at boot).

**`install.sh`** gained `--linger` / `FOUNDRYOS_LINGER=1` and a hand-off: after the install and the first
start, on a headless box with a terminal and an unclaimed install, it runs `foundryos setup` on
`/dev/tty`; otherwise it prints "Next: foundryos setup". The headless test in the script mirrors
`app/ops/headless.py`.

## 6. Tests and what was not verified

New or extended backend tests (132 new test functions): `test_cli_setup_wizard.py` (30),
`test_headless.py` (28), `test_public_url_announce.py` (17), `test_m11_services.py` (15 new),
`test_cli_reach.py` (14), `test_cli_onboarding_units.py` (13), `test_passkeys.py` (5 new),
`test_m9_doctor.py` (5 new), `test_m9_up.py` (4 new), `test_m11_lifecycle.py` (1 new). The full backend
suite on `099092d` passed: 1,685 tests. Web: `TunnelCard` (ephemeral note, last address, no note for a
stable tunnel), the passkey environment (`ephemeral_origin`) in the login form and the account card, and
the `webauthn` unit tests. Web suite at the end of this milestone: 1,217 passed, with typecheck and lint
clean.

What the tests cover: the wizard end to end against the real API with a fake `cloudflared`, fake
`launchctl`, `systemctl` and `loginctl`, fake coding CLIs and fake providers; secrets absent from argv,
output and audit data; resumption; the non-interactive refusal listing every missing input; URL change
recording, audit, file contents and permissions; stale-tunnel reaping; ephemeral passkey refusal and the
gate; headless detection per environment; the service fallbacks as call sequences.

**Not verified:**

- A real `cloudflared` quick tunnel: no real URL was handed out, none was opened from a phone, and
  nothing was observed about how long a URL lives or what happens to server-sent events.
- A real `systemd` user session, `loginctl enable-linger`, a real LaunchAgent in `user/<uid>`, a real
  LaunchDaemon, or a reboot with any of them. Only the command sequences and their error handling were
  tested.
- A real model provider, a real Claude Code, Codex, OpenCode or Antigravity login through the wizard.
- `install.sh` handing off to `foundryos setup` on a real SSH session (shellcheck was run; the hand-off
  was not exercised end to end).
- The wizard against a remote server over the internet (only a non-loopback address on the test client).
- Tailscale Funnel and named tunnels through the wizard.
- Linux, Docker and Postgres, as before.

## 7. Security notes

See `docs/security.md` section 6: the exposure of a fresh install while setup is pending, the
username-lockout side effect of the login back-off once a URL is public, and the Docker
`--forwarded-allow-ips '*'` caveat (G1) that now matters more because a published port is easier to reach.
