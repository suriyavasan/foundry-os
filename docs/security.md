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
| **Private-URL blocking** | agent web tools use Hermes' `tools/url_safety.py` (private ranges, cloud metadata hosts, redirect and DNS-rebinding checks), kept from upstream | runtime tests of the fork cover the loader, not this module's behaviour independently - **inherited, not re-verified in M9**. Control-plane outbound HTTP goes to fixed provider hosts; see G3 for user-supplied base URLs |
| **Prompt-injection posture** | containment, not prevention: untrusted content (web/file/mail/other agents' artifacts) is wrapped and flagged; no flag blocks automatically; real authority never comes from model output (all enforcement is code: tiers, approvals, budgets) | T `test_m8_email.py::test_the_owner_reads_inbound_mail_wrapped_as_untrusted_data_and_injections_are_flagged`; **L finding**: asked to request an approval, the local 7B model *invented an approval id in its summary* without calling the tool. An agent's own statement is therefore never treated as evidence - the approval, run and ledger records in the control plane are |
| **DLP (outbound)** | credential shapes, our own tokens, stored credentials, internal memory are blocked before anything leaves | T `test_guardrails_engine.py::test_dlp_blocks_*`, `test_m8_email.py::test_outbound_dlp_blocks_secrets_and_internal_tokens`, `test_accounts_auth.py::test_api_tokens_are_caught_by_the_outbound_dlp` |
| **Rate limits** | login (per user and per IP, exponential backoff, same response for unknown users); per-agent action limits; wake limits; outbound mail | T `test_accounts_auth.py` (throttling tests), `test_guardrails_registry.py::test_effective_permissions_show_tiers_tools_and_rate_limits`, `test_autonomy_notes.py::test_wake_rate_limit_per_hour`, `test_m8_email.py::test_outbound_is_rate_limited_per_agent`. No generic per-IP limit on every HTTP route (G4) |
| **CSRF / session / cookies** | server-side sessions (only a SHA-256 stored), HttpOnly `SameSite=Lax` cookie, signed double-submit CSRF header on cookie-authenticated writes, Secure over https; the owner cookie is `SameSite=Strict`; Bearer requests have no ambient credential | T `test_accounts_auth.py::test_cookie_auth_needs_a_matching_signed_csrf_token`, `::test_csrf_token_of_one_session_is_useless_in_another`, `::test_setup_sets_httponly_session_and_readable_csrf_cookies`, `::test_session_cookie_is_secure_over_https`, `::test_session_secret_is_stored_only_as_a_hash`, `test_auth.py::test_login_sets_an_httponly_samesite_strict_cookie_and_redirects` |
| **Exposure gate** | the server will not bind to the network, run a tunnel or deploy the hosted UI unless password sign-in is on and the owner account passed the password policy; `local` mode refuses non-loopback binds and clients | T `test_m8_reachability_api.py::test_lan_needs_the_exposure_gate_and_reports_restart_required`, `::test_local_auth_mode_never_allows_exposure`, `test_m8_provider_actions.py::test_hosted_ui_is_refused_without_the_exposure_gate`, `test_accounts_auth.py::test_local_mode_refuses_a_non_loopback_bind`, `::test_a_proxied_request_is_not_loopback`; `companyos up` goes through the same `decide_host` gate (T `test_m9_up.py::test_up_passes_a_decide_host_warning_through`) |
| **Webhook signatures** | inbound mail webhooks verify an HMAC and fail closed | T `test_m8_email.py::test_webhook_signatures_are_enforced_and_fail_closed` |
| **HTTP security headers** (new in M9) | see section 2 | T `test_m9_serving.py` (12 tests) + **L** in a real browser |
| **Local file permissions** (CLI, M9) | token file and config are created 0600 atomically; `doctor` fails if `owner.token`/`secret.key` are group/world readable; `owner.token` is tightened on read | T `test_m9_cli.py::test_secret_files_are_created_private`, `test_m9_doctor.py::test_secret_file_permissions` (the server also tightens a loose `owner.token` to 0600 when it reads it) |
| **Local-only commands** (M9) | `auth reset-password` / `auth setup-token` need to read `owner.token` (exit 4 otherwise) and are not reachable over HTTP; the reset signs the account out everywhere and audits it | T `test_m9_cli.py::test_reset_password_needs_the_server_machine_and_revokes_sessions`, `::test_setup_token_is_shown_only_while_setup_is_pending` |
| **Backup / restore** (M9) | archive verified (SHA-256 per file, safe paths, no links, nothing outside the manifest) before anything is touched; refuses a running install; replacing needs `--force` plus confirmation and moves the old data aside | T `test_m9_backup.py` (14 tests incl. tamper detection) |

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
  untrusted proxy).
* **G2 PostgreSQL unverified** (no server available): migrations are written to be portable, never run there.
* **G3 Server-side requests to owner-supplied URLs** (model-provider `base_url`, provider "test", `detect --base-url`)
  are not filtered for private ranges: an owner/admin can make the server call internal addresses. A localhost Ollama
  is the intended use, so a blanket block is wrong; an allow-list/warning for non-loopback private targets would be the fix.
* **G4 No generic per-IP rate limit** on arbitrary routes (only login, agent actions, mail, wakes). Behind a public
  tunnel, put a WAF/rate limit in front or rely on the tunnel provider.
* **G5 `style-src 'unsafe-inline'`** (section 2).
* **G6 Private-URL blocking for agent web tools is inherited from Hermes** and was not exercised by M9.
* **G7 The default CLI token scope is `admin`** (needed for policy/emergency-stop/reachability); use `login --scope read|write` for
  least privilege. Tokens do not expire (revoke them in Account > API tokens; a token cannot revoke itself).
* **G8 The backup archive holds the secret key and owner token.** It is mode 0600 but not encrypted by Company OS.
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
| Dangerous permission flags (`--dangerously-skip-permissions`, `bypassPermissions`, OpenCode `--auto`, Codex `danger-full-access`) never built; spawn refused if present | enforced | `backend/tests/test_m10_cli_*` argv assertions |
| Shell only inside an OS sandbox (Codex `--sandbox workspace-write`); Claude Bash and OpenCode bash denied | enforced | adapter tests |
| `api_key` mode: isolated HOME per agent, key only in the child env | enforced | adapter tests |
| `own_login` mode: uses the real HOME — the CLI can read the user's files within its own tool permissions | **accepted risk, warned in UI** | docs/cli-runtimes.md |
| Company actions only via the MCP server → agent API (tiers, rate limits, DLP) | enforced | MCP server tests, live OpenCode run |
| Run token in a 0600 file deleted after the run (never argv/config); a CLI able to read outside its workspace could read it | partial | docs/cli-runtimes.md |
| Install runs only with explicit confirmation, never sudo | enforced (fake npm in tests; real npm not exercised) | `test_m10_cli_install*` |
| Claude Code and Codex executed for real | **not verified** (Claude Code not run under the owner's login; Codex not installed) | — |
