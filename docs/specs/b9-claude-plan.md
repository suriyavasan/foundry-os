# B9 — "Your Claude plan" as a primary AI option (runs Claude Code)

Status: done (2026-10-28), folded into the AI stack (M17): the engine switch and the `/cli-runtimes/claude_code/login` routes were replaced by per-connection sign-in (`/ai-stack/{id}/login`). Backlog: `docs/backlog.md` B9. Rules: `AGENTS.md`.

## Goal

At "Connect your AI" (onboarding, AI panel, `foundryos setup`) the founder can pick **Your Claude plan** as a
peer of the model-API options (Anthropic, OpenAI, OpenRouter, Ollama, OpenAI-compatible). With it chosen, nothing
else is needed: every agent, management included, runs through the official Claude Code CLI signed in with the
founder's own Claude account. The API options stay available and can be added later.

## Compliance constraints (code.claude.com/docs/en/legal-and-compliance, /authentication, read 2026-10-28)

1. Claude Code is installed from Anthropic's official channel (npm `@anthropic-ai/claude-code` through the existing
   confirmed install endpoint, or Anthropic's installer shown for the user to run). Never bundled in our
   bundles / Docker image / Homebrew formula; never patched; no built-in auth method disabled.
2. Sign-in only through Claude Code's own flow: `claude auth login` (opens Anthropic's page; the callback goes to
   Claude Code's own localhost server). Foundry never sees a password, code or token.
   * The paste-the-code fallback (SSH, containers) happens **only in a terminal attached to Claude Code**
     (`foundryos claude login`), never through our web UI or API.
3. Foundry never reads `.credentials.json` or the Keychain, never runs `claude setup-token`, never stores or logs
   `CLAUDE_CODE_OAUTH_TOKEN`/any token. Login state comes only from `claude auth status` (JSON, exit 0/1).
4. Usage is the user's own plan. Runs are recorded as subscription (no charge), as today.
5. UI wording may say plainly "runs Claude Code" / "your Claude account". No Anthropic/Claude logos, and no
   product/feature names built from their marks (the option label is "Your Claude plan", described as
   "Sign in with your Claude account. Foundry runs the official Claude Code on this computer.").
6. Warn at selection: personal plans assume ordinary individual usage; an always-on company may hit plan limits or
   be restricted by Anthropic; an API key is the safe choice for heavy autonomy.
7. Owner action (not code): the publisher accepts Anthropic's Commercial Terms before shipping.

Verified on this machine (Claude Code 2.1.308) with an empty `CLAUDE_CONFIG_DIR`:
`claude auth status` → `{"loggedIn": false, "authMethod": "none", "apiProvider": "firstParty", ...,
"configDirectory": "<dir>"}`, exit 1; `--text` → "Not logged in. Run claude auth login to authenticate.".
`claude auth login [--claudeai|--console] [--email <e>] [--sso]`, `claude auth logout`.
A real sign-in was **not** performed during development.

## Data

* `policies.ai_engine` — `String(16)`, not null, server default `'api'`; values `api` | `claude_plan`.
  Migration `0016_ai_engine.py` (portable). Only humans (owner/admin) change it, via `PUT /ai-setup`.
* Claude Code's Foundry-owned config dir: `<data_dir>/claude-code` (0700), used as `CLAUDE_CONFIG_DIR` for every
  `own_login` Claude Code process (runs, tests, login, status, logout). The person's own `~/.claude` is never used.
  `HOME` stays the real home in own_login (macOS Keychain access). Existing own_login setups must sign in again.

## API (owner-only, like the other `/cli-runtimes` routes)

* `GET /api/cli-runtimes/claude_code/login` → `ClaudeLoginStatus`:
  `{signed_in: bool, auth_method: str|null, email: str|null, checked_at: iso, config_dir_ok: bool,
    login: {state: "idle"|"running"|"succeeded"|"failed", started_at, finished_at, url: str|null,
            message: str|null}}`
  `signed_in`/`auth_method`/`email` come from `claude auth status` (cached ≤ 30 s; `?refresh=1` forces).
  `url` is the sign-in link Claude Code printed (an authorization URL, not a credential) so the UI can offer
  "open the sign-in page" if no browser opened. 409 `cli_not_installed` when the binary is missing.
* `POST /api/cli-runtimes/claude_code/login` body `{method: "claudeai"|"console" = "claudeai"}` → 202 + status.
  Starts `claude auth login --claudeai|--console` with `CLAUDE_CONFIG_DIR`, stdin closed, 10-minute watchdog; one
  at a time (409 `login_in_progress`). Output is scanned only for the `https://` sign-in URL; it is never logged
  or stored beyond that URL. Also stores auth mode `own_login` for claude_code. Audited (no secrets).
* `DELETE /api/cli-runtimes/claude_code/login` → runs `claude auth logout`; audited.
* `CliRuntimeStatus` (existing `GET /api/cli-runtimes`) for claude_code gains `login: {signed_in, auth_method,
  email}` (cached). Health (`check_health`) for claude_code in own_login requires `signed_in`.

`GET /api/companies/{cid}/ai-setup` (`AiSetupStatus`) gains:
* `engine: "api"|"claude_plan"`
* `claude_plan: {installed, version, signed_in, auth_method, email, last_test, install{available, command}}`
* new `blocking_reason` values when `engine == claude_plan`: `claude_code_not_installed`,
  `claude_code_not_signed_in`, `claude_code_test_failed` (test must have passed with own_login; a sign-in or
  logout resets it).

`PUT /api/companies/{cid}/ai-setup` (`AiSetupUpdate`) gains `engine` (default `api`).
* `engine == "api"`: unchanged behaviour (management provider + model required).
* `engine == "claude_plan"`: `management_provider_id` / `management_model` optional and ignored when absent;
  requires Claude Code installed, signed in and tested (409 with the blocking reason otherwise); sets
  `work_mode="hybrid"`, `work_runtime="cli:claude_code"`. Audited.

## Behaviour with `engine == "claude_plan"`

* Readiness gate (`ai_setup.evaluate`) checks the Claude Code conditions instead of a tested ModelProvider.
* Routing (`routing.choose_runtime`): every task of every agent → `cli:claude_code` (reason
  "Your Claude plan: runs Claude Code"). Model routing returns an empty route (already true for CLI runtimes).
* Management through Claude Code: the CLI run gets the **role's** company tools (`agent_toolkit.company_tools_for`,
  same lists as `company_agent`) over the MCP server instead of the fixed worker subset, and the role/management
  instructions in its prompt. Enforcement stays server-side (agent API role checks, guardrails, budgets).
* Usage limits: Claude Code's usage-limit / rate-limit / login-expired results are classified explicitly.
  Usage limit → the CLI is marked unusable until the reset time (when Claude Code reports one, else 1 hour) and
  tasks wait as `waiting_for_runtime` with a clear reason; login expired → health false with
  "Sign in to Claude again". Neither is retried as a permanent failure.

## Terminal

* `foundryos claude login [--console]` → `claude auth login` with `CLAUDE_CONFIG_DIR`, inherited TTY (paste-code
  fallback works over SSH / `docker exec -it`). `foundryos claude status`, `foundryos claude logout`.
* `foundryos setup`, step "ai": first choice "Your Claude plan (runs Claude Code)" → install (confirm) → sign in
  (as above, in the terminal) → test → `PUT /ai-setup {engine: "claude_plan"}`; the "work" step is skipped.

## Web

"Connect your AI" shows the Claude-plan card first among the provider cards. Choosing it shows: install (existing
confirm dialog), "Sign in with Claude" (POST login; "finish signing in in the browser window that opened";
the link if one was printed; a terminal alternative `foundryos claude login` for remote servers), signed-in state
with method/email, Test, the usage warning, Save. Work-model and coding-CLI sections are hidden behind
"More options" when the Claude plan is chosen. Onboarding copy no longer says "the model API is required".
All states: loading, not installed, signing in, signed in, failed, not available (older backend → hide the card).
