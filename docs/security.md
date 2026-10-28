# Security review (M9)

Scope: the whole install as of M9 - control plane (`backend/`), web UI, CLI, agent runtime boundary (`runtime/`),
packaging. Method: for each item in the M9 spec, find the code that enforces it and a test that fails if it stops
holding (test names below are the evidence; nothing here is "believed to be fine"). Where a check was only partly
possible, that is stated. Dated 2026-10-24; reviewer: the M9 implementation pass (not an independent audit).

Legend: **T** = covered by an automated test (file::name) - **L** = checked live during M9 - **G** = known gap (section 3).

## 1. Review checklist

| area | how it is enforced | evidence |
|---|---|---|
| **Tenancy** | every tenant row has `company_id`; services take a `CompanyContext` and filter by it; non-members and cross-company ids are 404 | T `test_tenancy.py` (other user, own-company leakage, cross-company references, activity scoping); T every role/route walk in `test_accounts_team.py::test_role_matrix_on_every_mutating_route` |
| **AuthZ role matrix** | owner / admin / member / viewer enforced in services, not only routes; API-token scopes (`read`/`write`/`admin`) on top; a token never exceeds its user's role | T `test_accounts_team.py::test_role_matrix_on_every_mutating_route`, `test_accounts_auth.py::test_token_scopes_read_write_admin`, `test_m8_reachability_api.py::test_agents_and_write_scoped_tokens_cannot_touch_reachability` |
| **Agent self-modification** | agents can never change their own (or anyone's) permissions, budgets, runtime, policy, credentials, accounts; hires cannot exceed policy caps; agent-authored templates cannot escalate | T `test_agent_api.py::test_agent_cannot_change_policy_budgets_or_permissions`, `test_credentials.py::test_agents_cannot_touch_credentials_or_providers`, `test_hiring.py::test_agent_authored_templates_cannot_escalate`, `test_accounts_work.py::test_no_rule_can_allow_a_human_only_action`, `test_m8_accounts_api.py::test_agents_cannot_manage_accounts_even_with_a_run_token`; **L** (`scripts/verify.sh` item 7): with a token minted for a live run, an agent could not decide its own approval (agent API) nor use the owner API (403) |
| **Run-token scope** | HMAC-signed, tied to run/agent/company, valid only while the run is `starting`/`running`; the owner token is not a run token and vice versa | T `test_engine.py::test_run_token_is_valid_only_during_the_run`, `test_agent_api.py` (tampered / foreign-key / expired tokens), `test_auth.py::test_the_owner_token_is_not_a_run_token`, `test_auth.py::test_run_token_is_an_agent_on_both_apis_and_never_the_owner` |
| **Guardrail coverage** | every agent-API route is registered with a risk tier (a new route without one fails the suite); human-only actions are denied before any rule is read | T `test_guardrails_registry.py::test_every_agent_api_route_is_registered_with_a_tier`, `test_guardrails_engine.py`, `test_containment.py`; emergency stop: T `test_containment.py::test_emergency_stop_cancels_every_run_in_every_company_and_disables_dispatch`, `test_accounts_team.py::test_only_the_installation_owner_can_use_the_emergency_stop`, CLI `test_m9_cli.py::test_pause_resume_and_global_emergency_stop` |
| **Secret redaction** (logs, events, audit, errors, CLI) | run events and audit data are scrubbed of secret-looking keys/values; the access log masks login and invitation tokens; credential values are write-only; responses carry no secret material; the CLI never takes a secret as an argument and never echoes one | T `test_engine.py::test_run_events_are_redacted_before_they_are_stored`, `test_auth.py::test_access_log_masks_the_login_token`, `::test_access_log_masks_invitation_tokens`, `test_credentials.py::test_company_responses_carry_no_secret_material`, `::test_a_replaced_secret_key_makes_credentials_unreadable_not_leaky`, `test_agent_api.py::test_execution_request_repr_never_shows_the_run_token`, `test_skills_github.py::test_rate_limit_and_errors_are_reported_without_leaking_the_token`; CLI T `test_m9_cli.py::test_credentials_secret_comes_from_stdin_never_argv`, `::test_login_stores_a_scoped_token_not_the_password`, `::test_secret_files_are_created_private`, `::test_reset_password_*` (the password appears in no output) |
| **File jail + sandbox fallback** | file tools are jailed to the run workspace (symlinks cannot escape); `owner.token`, `secret.key` and the database sit above the jail; terminal/code tools exist only when a sandbox (Docker) is available, otherwise they are stripped | T `runtime/company_agent/tests/test_file_jail.py`, `test_run_loop.py::test_workspace_jail_holds_when_the_model_tries_to_escape`, `::test_terminal_and_code_execution_are_stripped_without_sandbox`; backend T `test_company_agent_adapter.py::test_terminal_and_code_execution_are_never_granted_without_a_sandbox`. 237 runtime tests passed on 2026-10-24. **Docker sandbox itself: not verified (no daemon)** |
| **Private-URL blocking** | agent web tools use Hermes' `tools/url_safety.py` (private ranges, cloud metadata hosts, redirect and DNS-rebinding checks), kept from upstream | runtime tests of the fork cover the loader, not this module's behaviour independently - **inherited, not re-verified in M9**. Control-plane outbound HTTP goes to fixed provider hosts; owner-supplied URLs (model-provider base URLs) go through the SSRF guard, section 5 (G3 closed) |
| **Prompt-injection posture** | containment, not prevention: untrusted content (web/file/mail/other agents' artifacts) is wrapped and flagged; no flag blocks automatically; real authority never comes from model output (all enforcement is code: tiers, approvals, budgets) | T `test_m8_email.py::test_the_owner_reads_inbound_mail_wrapped_as_untrusted_data_and_injections_are_flagged`; **L finding**: asked to request an approval, the local 7B model *invented an approval id in its summary* without calling the tool. An agent's own statement is therefore never treated as evidence - the approval, run and ledger records in the control plane are |
| **DLP (outbound)** | credential shapes, our own tokens, stored credentials, internal memory are blocked before anything leaves | T `test_guardrails_engine.py::test_dlp_blocks_*`, `test_m8_email.py::test_outbound_dlp_blocks_secrets_and_internal_tokens`, `test_accounts_auth.py::test_api_tokens_are_caught_by_the_outbound_dlp` |
| **Rate limits** | login (per user and per IP, exponential backoff, same response for unknown users); per-agent action limits; wake limits; outbound mail | T `test_accounts_auth.py` (throttling tests), `test_guardrails_registry.py::test_effective_permissions_show_tiers_tools_and_rate_limits`, `test_autonomy_notes.py::test_wake_rate_limit_per_hour`, `test_m8_email.py::test_outbound_is_rate_limited_per_agent`. Generic per-IP HTTP limit and SSE cap: T `test_security_ratelimit.py` (G4 closed, section 5) |
| **CSRF / session / cookies** | server-side sessions (only a SHA-256 stored), HttpOnly `SameSite=Lax` cookie, signed double-submit CSRF header on cookie-authenticated writes, Secure over https; the owner cookie is `SameSite=Strict`; Bearer requests have no ambient credential | T `test_accounts_auth.py::test_cookie_auth_needs_a_matching_signed_csrf_token`, `::test_csrf_token_of_one_session_is_useless_in_another`, `::test_setup_sets_httponly_session_and_readable_csrf_cookies`, `::test_session_cookie_is_secure_over_https`, `::test_session_secret_is_stored_only_as_a_hash`, `test_auth.py::test_login_sets_an_httponly_samesite_strict_cookie_and_redirects` |
| **Exposure gate** | the server will not bind to the network, run a tunnel or deploy the hosted UI unless password sign-in is on and the owner account passed the password policy; `local` mode refuses non-loopback binds and clients | T `test_m8_reachability_api.py::test_lan_needs_the_exposure_gate_and_reports_restart_required`, `::test_local_auth_mode_never_allows_exposure`, `test_m8_provider_actions.py::test_hosted_ui_is_refused_without_the_exposure_gate`, `test_accounts_auth.py::test_local_mode_refuses_a_non_loopback_bind`, `::test_a_proxied_request_is_not_loopback`; `companyos up` goes through the same `decide_host` gate (T `test_m9_up.py::test_up_passes_a_decide_host_warning_through`) |
| **Webhook signatures** | inbound mail webhooks verify an HMAC and fail closed | T `test_m8_email.py::test_webhook_signatures_are_enforced_and_fail_closed` |
| **HTTP security headers** (new in M9) | see section 2 | T `test_m9_serving.py` (12 tests) + **L** in a real browser |
| **Local file permissions** (CLI, M9) | token file and config are created 0600 atomically; `doctor` fails if `owner.token`/`secret.key` are group/world readable; `owner.token` is tightened on read | T `test_m9_cli.py::test_secret_files_are_created_private`, `test_m9_doctor.py::test_secret_file_permissions` (the server also tightens a loose `owner.token` to 0600 when it reads it) |
| **Local-only commands** (M9) | `auth reset-password` / `auth setup-token` need to read `owner.token` (exit 4 otherwise) and are not reachable over HTTP; the reset signs the account out everywhere and audits it | T `test_m9_cli.py::test_reset_password_needs_the_server_machine_and_revokes_sessions`, `::test_setup_token_is_shown_only_while_setup_is_pending` |
| **Backup / restore** (M9) | archive verified (SHA-256 per file, safe paths, no links, nothing outside the manifest) before anything is touched; refuses a running install; replacing needs `--force` plus confirmation and moves the old data aside | T `test_m9_backup.py` (14 tests incl. tamper detection); encryption at rest: T `test_security_backup_encryption.py` (G8 closed, section 5) |

## 2. HTTP security headers and CSP (added in M9)

Implemented in `backend/app/web.py` as two pure-ASGI middlewares (no response buffering, so the SSE stream is unaffected).

On **every** response: `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`,
`Permissions-Policy` (camera, microphone, geolocation, payment, usb off), `Cross-Origin-Opener-Policy: same-origin`;
`Strict-Transport-Security` (6 months) only when the request arrived over https (direct or `X-Forwarded-Proto: https`).
Headers an endpoint sets itself are never overwritten.

The SPA's HTML gets:

```
default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:;
font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self';
frame-ancestors 'none'
```

* **No inline scripts.** `index.html` used an inline theme bootstrap; it moved to `/theme-init.js` (`web/public/`) so
  `script-src 'self'` holds. A test (`test_the_real_vite_build_works_under_the_csp`) parses the real Vite output
  and fails on an inline script, an inline event handler, an external origin or a missing referenced file.
* **Checked in a real browser (L)**: the built app loaded from `companyos up` on a temporary port, the login screen,
  a deep link (SPA fallback), the canvas and a lazily loaded panel rendered with **zero `securitypolicyviolation`
  events**. (One browser, one session; not a full UI sweep.)
* `style-src 'unsafe-inline'` remains because Radix UI's scroll lock injects a `<style>` element at runtime. This
  weakens style injection protection only; scripts are strict. Nonce-based styles would need server-rendered HTML (G5).
* `/api/*` responses get `default-src 'none'; frame-ancestors 'none'` and, for JSON, `Cache-Control: no-store`
  (unless the endpoint sets its own); artifact `.../content` responses get `default-src 'none'; sandbox` on top of the
  existing `nosniff` / attachment rules. `/api/docs` (Swagger UI, loads its assets from a CDN) is exempt from the CSP.
* Caching: hashed `/assets/*` immutable for a year; everything else `no-cache` + `ETag` (304 supported).

## 3. Dependency audit (2026-10-24, network available)

| target | command | result |
|---|---|---|
| backend venv (54 packages; the project itself cannot be audited on PyPI) | `uvx pip-audit --path backend/.venv/lib/python3.14/site-packages` | **No known vulnerabilities** |
| runtime venv (74 packages, Hermes' exact pins) | `uvx pip-audit --path runtime/.venv/lib/python3.14/site-packages` | **No known vulnerabilities** |
| web (368 packages) | `pnpm audit` | **0** info / low / moderate / high / critical |

Notes: these databases only know *published* advisories; "no known vulnerabilities" is not "secure". The runtime's
`pyproject.toml` pins every dependency exactly on purpose (supply-chain policy of the upstream project); **do not
bump those pins to chase an advisory** - re-lock through the fork's process. No finding existed to act on. New
backend dependencies in M9: `typer`, `rich`, `segno` (pure Python, CLI only) and the optional `psycopg[binary]`
(Postgres extra, not installed by default). The container image's base layers are not scanned (unbuilt).

## 4. Findings

Fixed in M9:

1. **No security headers / CSP** on the API or UI -> added (section 2).
2. **A run that failed on configuration before executing anything was billed its reservation as an estimate**
   (live: three "no credential" failures showed `$0.0500 (estimated)` each, $0.15 of phantom spend that also counted
   toward budgets). Fixed in `engine/executor.py` (a `CONFIG` error with no usage releases the reservation, cost kind
   `unknown`); test `test_m9_config_failure_billing.py` (and a guard that failures *after* the runtime started are still
   billed). This was a violation of "never present an estimate as a charge".
3. The inline theme script (blocked CSP) -> external file.

Open - listed so nobody assumes they are handled:

* **G1 Docker image and Compose stack unverified**, Docker-socket sandbox unverified; the Compose file's `--forwarded-allow-ips *`
  trusts `X-Forwarded-*` from any peer (fine behind compose's private network, wrong if the API port is published to an
  untrusted proxy). The same flag is in the image's entrypoint (`packaging/docker/entrypoint.sh`), so it also applies to
  `docker run -p 8700:8700`; section 6.3 says what that does and does not weaken.
* **G2 PostgreSQL unverified** (no server available): migrations are written to be portable, never run there.
* ~~**G3 Server-side requests to owner-supplied URLs**~~ **CLOSED** (post-M9 hardening, section 5): SSRF guard with
  DNS resolution, address pinning and an explicit private-network opt-in. Residual: see 5.1.
* ~~**G4 No generic per-IP rate limit**~~ **CLOSED** (section 5): in-memory per-IP token buckets plus an SSE cap.
  Residual: see 5.2.
* **G5 `style-src 'unsafe-inline'`** (section 2).
* **G6 Private-URL blocking for agent web tools is inherited from Hermes** and was not exercised by M9.
* **G7 The default CLI token scope is `admin`** (needed for policy/emergency-stop/reachability); use `login --scope read|write` for
  least privilege. Tokens do not expire (revoke them in Account > API tokens; a token cannot revoke itself).
* ~~**G8 The backup archive holds the secret key and owner token.**~~ **CLOSED** (section 5): `backup create --encrypt`
  writes AES-256-GCM `*.tar.gz.enc`; plain backups remain possible, warn, and are 0600. Residual: see 5.3.
* **G9 Prompt injection is contained, not prevented.** A weak local model will do what injected text asks if its tools
  allow it; budgets, tiers and approvals are the backstop, which is why they are code.
* **G10 `restore` cannot detect a server started by an older version** (no run file) - it detects every process started
  since M9. A SIGKILLed process leaves a stale run file; a live pid in it blocks the restore (delete the file if the pid is
  not Company OS).
* The standing design assumption "agents run on the same host as the owner token" still holds: `owner.token` is
  outside every agent jail and environment, and `auth reset-password` relies on that.


## Coding-CLI runtimes (M10)

| Control | Status | Evidence |
|---|---|---|
| Dangerous permission flags (`--dangerously-skip-permissions`, `bypassPermissions`, OpenCode `--auto`, Codex `danger-full-access`, Antigravity `always-proceed`, Cursor `--yolo`) never built; spawn refused if present | enforced | `backend/tests/test_m10_cli_*` argv assertions |
| Shell only inside an OS sandbox (Codex `--sandbox workspace-write`); Claude Bash, OpenCode bash and Antigravity `command(*)` and Cursor `Shell(*)` denied | enforced | adapter tests |
| `api_key` mode: isolated HOME per agent, key only in the child env | enforced | adapter tests |
| `own_login` mode: uses the real HOME — the CLI can read the user's files within its own tool permissions | **accepted risk, warned in UI** | docs/cli-runtimes.md |
| Company actions only via the MCP server → agent API (tiers, rate limits, DLP) | enforced | MCP server tests, live OpenCode run |
| Run token in a 0600 file deleted after the run (never argv/config); a CLI able to read outside its workspace could read it | partial | docs/cli-runtimes.md |
| Install runs only with explicit confirmation, never sudo | enforced (fake npm in tests; real npm not exercised) | `test_m10_cli_install*` |
| Claude Code and Codex executed for real | **not verified** (Claude Code not run under the owner's login; Codex not installed) | — |

## 5. Gaps closed after M9 (G3, G4, G8)

Dated 2026-10-24. Every claim below has a test that fails if it stops holding; what was *not* exercised is stated.

### 5.1 SSRF guard for owner-supplied URLs (G3) - `app/core/netguard.py`

* **What is checked**: scheme `http`/`https` only; no `user:pass@` in the URL; the host is resolved and **every**
  address returned is classified. Always refused, whatever the owner says: link-local (`169.254.0.0/16`, `fe80::/10`),
  the cloud-metadata addresses (`169.254.169.254`, `169.254.170.2`, `fd00:ec2::254`, `100.100.100.200`, `192.0.0.192`),
  unspecified (`0.0.0.0`, `::`), multicast, and every range that is not globally routable (reserved, documentation,
  benchmarking). IPv4-mapped (`::ffff:a.b.c.d`), NAT64 and 6to4 addresses are judged by the IPv4 address they carry;
  alternative spellings (`http://2130706433`) are judged by what the system resolver turns them into.
* **Policy by provider kind**: `ollama` and `openai_compatible` may point at **loopback** (local models are a core
  feature); **private networks** (RFC 1918, CGNAT/Tailscale `100.64/10`, IPv6 ULA) need the owner's explicit
  `allow_private_network` on the provider (create/update; one test run can also pass it without saving it);
  `openrouter` / `anthropic` / `openai` must resolve to public addresses and refuse the flag. Violations are
  `422 unsafe_url` with `details[0].reason` and `can_allow` (true when the opt-in would permit it).
* **Where it applies**: provider create/update (the URL and the opt-in are re-validated whenever either changes),
  `detect` (including `--base-url`), the provider **test** and the **model list**. The opt-in is stored in the
  existing `system_state` table (key `pn:<provider id>`, removed when the provider is deleted) and is audited.
* **DNS rebinding**: saving a URL only proves what the name resolved to *then*. Every real request goes through
  `GuardedTransport`, which resolves again, validates every address, and connects to the validated IP (the URL host is
  replaced by the IP; `Host`, TLS SNI and certificate checks keep the original name) - there is no second lookup
  between check and connect. Redirects are never followed by these clients.
* **Evidence**: `test_security_netguard.py` (70 tests): classification table, metadata/link-local/reserved in every
  spelling, a name with one public and one internal record, the rebinding attempt (public at save time,
  `169.254.169.254` at use time: refused, zero connections made), pinning (Host/SNI kept), a real loopback server
  reached by local kinds and refused for cloud kinds, the API policy per kind, opt-in lifecycle and authorization.
* **Residual (5.1)**: the agent *runtime* (Hermes subprocess, coding CLIs) connects to the saved provider URL itself;
  the control plane cannot pin that connection, so a rebinding between a test and a run is not covered. URLs set
  through the operator's environment (`COMPANY_OS_GITHUB_API_URL`, `HERMES_API_URL`, ...) are trusted, not checked.
  Providers saved before this change that point at a private address stop passing "test" until the owner ticks
  the opt-in. Not exercised against a real cloud metadata service.

### 5.2 Generic per-IP HTTP rate limit (G4) - `app/core/ratelimit_http.py`

* In-memory token buckets keyed by client address, applied to `/api/*` only (the web UI's static files are never
  counted). Defaults: **300/min** per IP for `/api/*`; **30/min** for the routes a stranger can reach without an account
  (state-changing `/api/auth/*`, the login link, `/api/invitations/*`, `/api/webhooks/*`); **10 concurrent SSE
  streams** per IP. Refusals are `429 rate_limited` in the standard envelope with `Retry-After` and
  `details.retry_after_seconds`; they carry the CORS and security headers like any other answer.
* The client address is the one uvicorn reports, so the existing trusted-proxy rules decide whether `X-Forwarded-For` is
  believed (`--forwarded-allow-ips`, default `127.0.0.1`); a header sent from an untrusted peer buys no fresh budget.
  IPv6 clients share a bucket per /64. A request from this machine (loopback peer, no proxy headers, loopback `Host`)
  gets 10x the limits; it is not exempt unless `COMPANY_OS_HTTP_RATE_LIMIT_LOOPBACK_EXEMPT=true`. Settings:
  `http_rate_limit_enabled` (set `COMPANY_OS_HTTP_RATE_LIMIT_ENABLED=false` to switch off), `http_rate_limit_per_minute`,
  `http_rate_limit_auth_per_minute`, `http_rate_limit_loopback_multiplier`, `http_rate_limit_loopback_exempt`,
  `http_stream_max_per_ip`.
* The login back-off in `services/auth.py` is unchanged and still applies on top.
* **Evidence**: `test_security_ratelimit.py` (26 tests): envelope + header + refill, per-client isolation, stricter
  stranger bucket, static UI exempt, off switch, trusted vs untrusted proxy header, loopback allowance and exemption,
  SSE cap (slots freed on end and on crash), CORS/security headers on a 429, memory sweep.
* **Residual (5.2)**: state is per process and resets on restart; several worker processes would each keep their own
  buckets. Clients behind one NAT or one untrusted proxy share a bucket (raise the limit rather than trusting
  headers). It is a speed bump against a single noisy client, not a DDoS defence: a WAF in front of a public tunnel
  is still sensible. Not load-tested.

### 5.3 Encrypted backups (G8) - `app/ops/backup_crypto.py`, `app/ops/backup.py`

* `foundryos backup create --encrypt` asks for a passphrase at a hidden prompt (twice) or reads one line from stdin
  with `--passphrase-stdin`; **it is never an argument** (argv is world-visible). Minimum 8 characters; there is no
  recovery for a lost passphrase. Output is `*.tar.gz.enc`: scrypt (N=2^17, r=8, p=1, random 16-byte salt) derives the
  key; the archive is encrypted in 1 MiB chunks with AES-256-GCM (STREAM construction: nonce = random base + counter,
  and the header plus a "last chunk" flag are authenticated data). Header: magic, format version, KDF parameters
  (bounded when read, so a hostile file cannot demand gigabytes), salt, chunk size, and a key check that tells "wrong
  passphrase" from "damaged file". Memory use is one chunk, whatever the size.
* `backup restore` / `--verify-only` recognise an encrypted archive by its magic bytes and ask for the passphrase. A
  wrong passphrase, a flipped byte anywhere (header, any chunk, tag), a truncated or extended file, or reordered /
  repeated / dropped chunks are refused with a clear message before anything in the data directory (or a staging
  folder) is created.
* Unencrypted backups remain possible; the CLI says they are **not** encrypted and contain `secret.key` and
  `owner.token`. **Every backup file is created 0600** (explicitly, independent of umask); the restore staging folder
  is 0700.
* **Automatic pre-upgrade backups stay unencrypted** (local, 0600, in `<data>/backups/`): they run unattended with no
  human to ask for a passphrase, and storing a passphrase next to the data would add nothing - they sit in the same
  directory as `secret.key`, i.e. inside the same trust boundary. Move or encrypt them yourself if the disk is shared.
* **Evidence**: `test_security_backup_encryption.py` (44 tests): round trip, no plaintext/names in the file, wrong and
  missing passphrase, single-byte flips at every header field and in the ciphertext and tag, truncation/append/reorder/
  duplicate/drop chunks, hostile KDF parameters, a 96 MiB stream with bounded memory, a 24 MiB incompressible file
  through a full encrypted backup, 0600 under `umask 0`, the pre-upgrade backup, and the CLI (stdin and hidden prompt,
  passphrase absent from output, `--passphrase VALUE` rejected).
* **Residual (5.3)**: AES-GCM and scrypt come from the `cryptography` library, not our own code; the format has not
  been reviewed by an independent cryptographer. A passphrase on a command line pipe (`echo pw | foundryos ...`) is
  visible in your shell history - prefer the prompt or a file redirect. Restoring from an encrypted backup decrypts
  secrets into a 0700 staging folder on the target disk before they are moved into place. PostgreSQL installs are still
  not covered by `backup`.

## 6. Headless setup and the public URL (M13)

Dated 2026-10-26. This is an analysis of the code as it is after `099092d`; it adds no new enforcement. Where a test backs a claim it is named;
the rest is reading the code, and says so. Nothing here was exercised against a real tunnel, a real Docker daemon or a hostile client.

### 6.1 What can reach a fresh install, and who can claim it

A fresh install has no owner password. The first account is created by `POST /api/auth/setup` (the web screen, or `foundryos setup`). While that is
pending, whoever can make that request first becomes the owner.

| How the server is reachable | Who can claim it | What stops others |
|---|---|---|
| Default: bound to `127.0.0.1` (installer, Homebrew, `foundryos up`) | Anything on this machine that can open a local socket, including another user account on a shared machine. Also anyone who forwards the port with SSH, who by definition has a shell here | The request must be loopback: TCP peer loopback, **no** forwarding headers, loopback `Host`. T `test_setup_from_loopback_needs_no_token`, `test_a_proxied_request_is_not_loopback`. Nothing remote can reach the port |
| Bound to the network (LAN preference, Docker `-p 8700:8700`, `COMPANY_OS_BIND_HOST=0.0.0.0`) | Only someone who knows the setup token (the contents of `owner.token`, mode 0600, shown by `foundryos auth setup-token` on the machine) | T `test_setup_needs_the_setup_token_unless_loopback`, `test_fresh_install_reports_setup_required_and_who_needs_the_token`; wrong tokens are throttled per address (`setup:<ip>`), see 6.3 |
| A tunnel (quick, named, Funnel) | Nobody can start one first: every tunnel start needs the exposure gate (password auth on, owner with a policy-compliant password or 2FA, so an account exists). The wizard orders account before public URL. cloudflared connects from `127.0.0.1` but forwards `X-Forwarded-For` / `CF-*` headers, so tunnelled requests are never "loopback" | T `test_m8_reachability_api.py::test_lan_needs_the_exposure_gate_and_reports_restart_required`, `::test_local_auth_mode_never_allows_exposure`, `test_a_proxied_request_is_not_loopback` |

The window is the time between the server starting and the account existing. `install.sh` starts the server before the account exists, then (on a
headless terminal) runs `foundryos setup` straight away, so the window is the time it takes to answer the first prompts. With `--no-start`, without a
terminal, or when you start it and walk away, the window stays open until someone runs `foundryos setup`.

* **G11 (new, open): on a shared machine another local user can claim an unclaimed install.** The loopback exemption exists so that the person sitting at
  the machine needs no token. It cannot tell them from another local account. Mitigation today: create the account immediately (`foundryos setup`), and
  do not leave an unclaimed install running on a multi-user host. A possible fix (require the setup token even on loopback when the host has other
  interactive users) is not built.
* Password and setup token travel as HTTP request bodies. `foundryos setup` talking to a remote `--api-url` over plain `http://` sends them in clear text,
  and the wizard does not warn. Use `https://` or the SSH port-forward. Not tested against a remote server over the internet.
* `--secrets-stdin` keeps secrets out of argv, output and the audit log (T `test_cli_setup_wizard.py::test_non_interactive_run_keeps_secrets_out_of_argv_output_and_audit`).
  It does not protect a secrets file you leave on disk: delete it after use.

### 6.2 A public URL and the login back-off: username lockout (G12)

Sign-in is throttled per username (`user:<name>`) and per address (`ip:<addr>`): 3 free failures per username, then 2, 4, 8 ... seconds up to 15 minutes
(`login_free_attempts`, `login_max_lockout_seconds`). While a lock is active even the correct password is refused, and an attempt during a lock does not extend
it (T `test_accounts_auth.py::test_lockout_is_exponential_and_capped`, `::test_lock_expires_and_success_resets_the_counter`; unknown usernames are throttled
exactly like real ones, T `::test_unknown_usernames_are_throttled_exactly_like_real_ones`).

* **G12 (new, open): anyone who can reach the sign-in page and guesses the owner's username can keep the owner locked out.** Four wrong passwords start
  the lock; one more wrong attempt each time it expires (about one request per 15 minutes) holds it at the cap. It protects the password and costs the
  owner availability. It was an accepted trade while the app was reachable only locally; a public URL makes it reachable by strangers, and the wizard's
  summary prints the username. This is reading the code, not a measured attack.
* What still works while locked out: a personal API token (the CLI is not throttled by the user key), `foundryos auth reset-password USERNAME` on the server
  machine (it also clears the lockout), and a passkey on a stable address (passkey sign-in uses the credential and address keys, not the username key).
  Passkeys are refused on quick-tunnel addresses (section 4 of the M13 spec), so on a quick tunnel the only web sign-in is the throttled password.
  The SSH port-forward reaches the same throttle rows, so it does not bypass a lock.
* Mitigation today: pick a username that is not guessable, turn on two-factor sign-in (it does not stop the lockout but stops a guessed password), keep a
  CLI token, prefer a named tunnel or Funnel plus a passkey, or put a WAF / Cloudflare Access in front. A real fix (per-address-and-username keys, a
  CAPTCHA, or an owner exemption by passkey or token) is not built.

### 6.3 Docker `--forwarded-allow-ips '*'` (G1, what it changes)

The image runs `uvicorn --proxy-headers --forwarded-allow-ips '*'`: every peer is trusted to set `X-Forwarded-For`. Reading the code:

* **Not weakened:** the loopback and setup-token rule. Any forwarding header disqualifies a request from being "loopback", whatever its claimed address
  (T `test_a_proxied_request_is_not_loopback`). The password check, the per-username lockout and the exposure gate do not look at the client address.
* **Weakened:** everything keyed by client address. If the container port is published to untrusted clients, a client can send a different `X-Forwarded-For`
  with each request and get a fresh bucket each time: the generic per-IP HTTP rate limit (G4), the per-IP login throttle (`ip:<addr>`), the setup-token
  guess throttle (`setup:<addr>`), and the SSE cap per address. Only the per-username lockout is left against password guessing, which makes G12 more
  likely to be the only brake. A 256-bit setup token is not guessable either way.
* **Not weakened by a tunnel in a normal install:** outside Docker the default is `--forwarded-allow-ips 127.0.0.1`, and cloudflared connects from
  `127.0.0.1`, so only the tunnel's `X-Forwarded-For` is believed (T `test_public_url_announce.py::test_a_request_through_the_tunnel_gets_its_own_rate_limit_bucket`,
  which simulates the tunnel's headers). Not verified against a real tunnel.
* Recommended: publish the container port on `127.0.0.1` only (`-p 127.0.0.1:8700:8700`, as `docs/install.md` does) and put the tunnel or a reverse proxy you
  control in front of it; or run uvicorn with `--forwarded-allow-ips` set to that proxy's address. The entrypoint's `'*'` is not changed in M13.

### 6.4 Quick tunnels

* A quick-tunnel address is a bearer-less public door: anyone who learns it can reach the sign-in page. Its random name is not a secret worth relying on
  (it appears in logs, `public-url.json` and the audit log). Use two-factor sign-in with it; the wizard warns when it is off.
* `public-url.json` and the stored URL history are mode 0600 / inside the database. The audit event `reachability.tunnel_url_changed` contains the URL, so
  the URL is visible to company members who can read the activity log.
* Passkeys on ephemeral origins are refused and do not count toward the exposure gate (T `test_passkeys.py::test_a_quick_tunnel_address_gets_no_passkeys`,
  `::test_a_passkey_cannot_be_added_through_a_quick_tunnel`, `::test_a_passkey_from_a_quick_tunnel_origin_is_not_strong_authentication`).
* The `cloudflared` binary is downloaded only after confirmation and verified against the publisher's SHA-256 (M8). The quick tunnel runs with an empty
  `HOME` and its pid is registered, so a crash does not leave a second tunnel behind (T `test_public_url_announce.py::test_a_stale_registered_tunnel_is_killed_before_a_new_one_starts` kills a real stray process, but a fake tunnel program, not `cloudflared`).

## 7. GitHub-backed projects (M14)

What the git engine (`backend/app/services/repo_git.py`, the only module that runs network git), the
push gate (`repo_scan.py`, `repo_gate.py`) and the sync loop (`repo_sync.py`) guarantee, and what they
do not. Everything below was exercised against local bare repositories over smart HTTP and a fake
GitHub API; **nothing was exercised against github.com** (see `docs/specs/m14-github-backed-projects.md`
section 9 for the GitHub behaviours that still have to be confirmed).

**Token handling.** The fine-grained token lives only in the encrypted credential vault (the M8
connected account). It is decrypted per operation inside the control plane and handed to ONE git child
process through its environment (`FOUNDRY_GIT_TOKEN`) and a static `GIT_ASKPASS` script
(`<data>/run/git-askpass`, 0700, rewritten if tampered with). It is never in argv, `.git/config`, the
remote URL (userinfo is refused), FETCH_HEAD, a credential helper, a log, an audit event, an agent's
context or an API response. The askpass script answers only a prompt that names the expected host
exactly (`//host'` or `@host'`), so another host, a look-alike host or another port gets nothing.
git's stderr passes through the token mask and `secrets_scan.scrub` before it is logged or stored.

**Config pinning.** Before every fetch or push `pin_config` rewrites `.git/config` and
`.git/info/exclude` atomically from the database row and deletes `.git/info/attributes`: a planted
`url.*.insteadOf`, `credential.helper`, `core.hooksPath`, `core.fsmonitor`, `remote.*.pushurl` or
filter is gone before the token exists in any process. Global and system git config are never read
(`GIT_CONFIG_GLOBAL=/dev/null`, `HOME=/nonexistent`). Network calls add `credential.helper=` (reset),
`protocol.allow=never` + `protocol.https.allow=always`, `http.followRedirects=false`, no submodule
recursion, `transfer.fsckObjects=true` and `core.sshCommand=/bin/false`; hooks, GPG and fsmonitor are
disabled for every call; the environment is minimal; there is no shell; a timeout kills the whole
process group.

**No force push, no rebase, ever.** `repo_git.push` builds exactly
`git push --porcelain origin <sha>:refs/heads/<branch>` and `assert_safe_push_argv` refuses force,
`--force-with-lease`, delete, mirror, tags, all, prune, `+` and `:` refspecs and any destination
outside `refs/heads/`. A test fails if any other module builds a push argv. Upstream changes come in
as merge commits; a rewritten upstream history stops all pushes (`upstream_rewritten`) until the owner
resets the integration branch (the old head is kept as a rescue branch). Foundry merges a pull request
only when GitHub reports `mergeable_state` `clean` or `has_hooks` and the head equals the reviewed SHA
(sent as `sha`), so an administrator's token cannot make Foundry bypass branch protection.

**The push gate.** Every commit that would leave the machine is scanned: the ADDED lines of every
commit (a secret added and removed in between is in the history that would be pushed), merge
resolutions, and commit messages. Blocking findings: credential-shaped strings, the company's own
stored secrets (including the GitHub token), internal-only memory, blocked file names (`.env`, keys,
`credentials.json`...), protected paths (`.github/workflows/**`, `.github/actions/**`, `.gitmodules`;
the workflow globs are lifted only by `allow_workflow_changes`), files over `max_file_bytes`, pushes
over `max_push_bytes` or 500 commits. E-mail addresses and phone numbers are reported, never blocking.
Findings carry kind, count and paths, never the matched text. The same scan runs before a run is
merged into the work branch (`project_workspaces.finalize`), so a secret never reaches the integration
branch; the run fails with `secret_detected` / `protected_path` / `workspace_too_large` (permanent) and
its branch is deleted, not kept. Commits made by people on GitHub are excluded from the scan. Very
long lines are scanned in overlapping windows, and a push over the size limit is refused without
reading all of it. Limits of the scan: it is pattern-based (an unknown secret shape that is not a
stored credential passes), binary files are checked by name and size only, and it cannot see what an
agent sends to a model provider.

**System GitHub operations are guardrail actions.** Push, PR open/update, merge and (WP-A) branch
delete are `kind="system"` actions checked with `guardrails.engine.check` under the project lead's
context (else the Co-founder's): the emergency stop, human deny/approval rules, rate limits and the
guardrail log all apply, and an approval binds to the exact payload (for a merge: repository, number,
head SHA, method). While the emergency stop is on, a sync makes no network call at all; while the
company is paused it only fetches and integrates locally.

**Residual risk (documented, not removed).** The token is in the environment of the short-lived git
child. Agents run as the same operating-system user: a process that can execute arbitrary code (the M10
CLIs as configured cannot; the company agent with a local terminal could) could read the child's
environment (`/proc/<pid>/environ` on Linux) during that window. Mitigations: a fine-grained token
limited to the bound repositories, the push gate, no shell for the CLIs, and the token never being
reachable from agent-visible files. Other limits: LFS is not supported; GitHub content (issues,
comments, CI logs) is untrusted input and is handed to agents only in `<untrusted>` blocks; a stolen
token is as powerful as its permissions, so rotate it from Integrations if in doubt (a revoked token
puts the repository in `auth_failed`, local work continues and nothing is lost).

## 8. Market radar (M16)

The radar reads the open internet, the most hostile input the company has. Layers, each enforced in code:

* **Fetching** is done by the control plane, never by an agent: `GuardedTransport(PUBLIC_ONLY)` (no private, link-local,
  metadata or reserved address; every redirect re-checked, at most 3, no https to http), robots.txt honoured per host with a
  24 h cache, per-host spacing, size and time caps, XML through `defusedxml`, a daily request budget
  (`radar_max_fetches_per_day`), the emergency stop and company pause checked before each request.
* **Page reading** (`company_radar_read`) uses the same fetcher for any agent with the web toolset (and the Co-founder): HTML
  only, 1 MB, 12,000 characters of plain text back, https image URLs only. A page that belongs to a radar item flagged for
  instruction-like text is read only when the agent names that item.
* **Text** is stored plain (tags, control and bidi characters stripped; `<untrusted` neutralised) and flagged. In the agent
  API it appears only in `content` fields; the runtime wraps each in `<untrusted source=...>` and reports flags. It never
  rides in a task description, a schedule template or the M12 digest, which carry counts only. Ideas are agent-written from
  outside text, so they are wrapped the same way when another agent reads them, and an idea whose text trips the detector
  can only be accepted by a person.
* **Authority.** Agents cannot change `radar_*` policy, owner or derived keywords, or owner sources (403, audited); agent
  feeds are validated by a test fetch, labelled "Added by an agent", capped at 20 and stored disabled when the owner has
  turned `radar_agent_sources` off. Accepting an idea creates tasks through the M12 core (all caps); a new goal needs the
  Co-founder, `radar_auto_accept` and the weekly cap. Search: 4 `web_search` queries a day in the radar routine, enforced
  at `/guardrails/check-tool`.
* **Privacy.** `company_radar_ui_search` sends the query and the server's IP to One Page Love (fallback Siteinspire), which
  say they log them. A guardrail `deny` rule on `radar.ui_search` turns it off. The Content-Security-Policy still blocks
  third-party images in the UI. `web_search` through DuckDuckGo has no API agreement.
* **Residual risk.** Detection is pattern based; a model can still be persuaded by text it reads, which is why nothing a
  radar item says can reach a permission, budget or approval, and why every effect is bounded by caps. Terms of service of
  sites differ from robots.txt: the seed enables only sources with an allowing robots.txt and a public feed or documented API.
