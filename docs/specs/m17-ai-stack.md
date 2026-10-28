# M17 — One AI stack

Status: done (2026-10-28); plan sign-ins and real CLI runs not exercised live. Replaces the split "management model / work model / coding CLI / AI engine"
setup (M10 AI setup, M12 work model, B9 engine) with one ordered list of AI connections the owner manages in
onboarding and in Settings. Subsumes backlog B6 (multiple keys + routing) and B9 (Claude plan). Rules: `AGENTS.md`.

## 1. Concepts

An **AI connection** is one way Foundry can think, owned by a company. A company has 0..n connections, any mix:

| Option (UI label) | `engine` | details | runs on |
| --- | --- | --- | --- |
| Claude — your plan (runs Claude Code) | `cli` | `cli_id=claude_code`, `auth=plan` | `cli:claude_code` |
| Anthropic API | `api` | ModelProvider kind `anthropic` + model | `company_agent` |
| OpenAI API | `api` | kind `openai` | `company_agent` |
| OpenRouter | `api` | kind `openrouter` | `company_agent` |
| OpenAI-compatible | `api` | kind `openai_compatible` | `company_agent` |
| Ollama (local) | `api` | kind `ollama` | `company_agent` |
| Cursor — your plan (runs Cursor CLI) | `cli` | `cli_id=cursor`, `auth=plan` | `cli:cursor` |
| Cursor API key | `cli` | `cli_id=cursor`, `auth=api_key` | `cli:cursor` |
| Codex (OpenAI key) | `cli` | `cli_id=codex`, `auth=api_key` | `cli:codex` |
| OpenCode | `cli` | `cli_id=opencode`, `auth=api_key` or `provider` | `cli:opencode` |
| Antigravity (Gemini key) | `cli` | `cli_id=antigravity`, `auth=api_key` | `cli:antigravity` |

Claude is offered in exactly two ways, both permitted: the plan through the official, unmodified Claude Code
(B9 compliance rules unchanged — `docs/specs/b9-claude-plan.md` §"Compliance constraints"), and the Anthropic API.

Each connection has:
* `position` — order in the stack. The first is "primary", the next "secondary", etc. (shown as such in the UI).
* `use_for` — `everything` | `heavy` | `everyday`.
  * **heavy** = management runs: the agent's role is Founder/HR/Lead/Manager, or the task kind is not `work`
    (planning, review, hiring, routine) — the existing `routing.is_management_run`.
  * **everyday** = all other runs (specialists' `work` tasks).
* `share` — importance, int 0–4: 4 "Most", 3 "More", 2 "Normal", 1 "Less", 0 "Backup only".
* `enabled`, `label` (owner-editable, default from the option), `model` (API: required; CLI: optional).
* status: last test, sign-in state (plan), `paused_until` + `pause_reason` (plan usage limit / login expired).

### Legal guardrails (enforced in code)
* **One plan sign-in per CLI per company** (at most one `claude_code`+`plan` and one `cursor`+`plan`). Anthropic's
  consumer terms forbid bypassing protective measures; rotating several personal plans around usage limits is
  not something we automate. Multiple API keys / accounts of any provider are fine (each is billed to its owner).
* Plan sign-in only through the CLI's own flow (`claude auth login`; `agent login`), status only through the CLI
  (`claude auth status`; `agent status`). Never read, copy, store or relay the CLIs' credential files, tokens or
  sign-in codes. Paste-code fallbacks happen only in a terminal attached to the CLI (`foundryos ai login`).
* Plan usage is recorded as subscription (no charge). Warnings in the UI: personal plans assume ordinary
  individual use (Claude); Cursor recommends API keys for automation (Cursor docs). API keys are the safe choice
  for heavy autonomy.

## 2. Data

Migration `0017_ai_stack.py` (portable SQLite/Postgres):
* table `ai_connections`: `id` (uuid str 36), `company_id` (FK, indexed), `position` int, `label` str(120),
  `engine` str(8) `api|cli`, `model_provider_id` (FK model_providers, nullable), `model` str(160) nullable,
  `cli_id` str(32) nullable, `auth_mode` str(16) nullable `plan|api_key|provider`, `credential_id` (FK
  credentials, nullable), `cli_provider_id` (FK model_providers, nullable; OpenCode `provider` auth),
  `use_for` str(16) default `everything`, `share` int default 2, `enabled` bool default true,
  `home_dir` str(255) nullable (plan connections: path relative to the data dir), `last_test` JSON nullable,
  `paused_until` datetime nullable, `pause_reason` str(200) nullable, `created_at`, `updated_at`.
  Unique (`company_id`, `position`) is NOT required (reorder writes all positions in one transaction).
* `task_runs.ai_connection_id` str(36) nullable (which connection a run used; shown in finance/run detail).
* Legacy policy fields (`default_model_*`, `work_mode`, `work_runtime`, `work_model_*`, `ai_engine`,
  `model_routing`, `allow_paid_fallback`) stay in the schema but are no longer read for routing once a company has
  a stack. **Bootstrap**: the first time a company's stack is read and it has no connections, build them from the
  legacy settings in one audited transaction (`ai_stack.bootstrapped`, system principal):
  management provider → api connection (`use_for` = `heavy` if a separate work model or hybrid CLI existed, else
  `everything`, share 2, position 0); work model → api `everyday`; hybrid `work_runtime=cli:x` with its stored
  installation-level auth (`system_state cli:<id>`: `own_login`→`plan`, `api_key` with a credential of this
  company, OpenCode `provider`) → cli `everyday`; `ai_engine=claude_plan` → claude plan `everything` reusing the B9
  dir `claude-code` as `home_dir`. Idempotent; never runs again once any connection exists (even after deletes).
  Track "bootstrapped" with a flag (policy column `ai_stack_bootstrapped` bool, in the same migration).

Plan homes (0700, under the data dir): Claude `claude-code/<connection_id>` (or the bootstrapped `claude-code`) as
`CLAUDE_CONFIG_DIR`; Cursor `cursor-home/<connection_id>` as the isolated `HOME` for `agent login/status/logout`
and its runs. Removing a plan connection runs the CLI's logout and deletes its home.

## 3. API (company-scoped; owners and admins write, members/viewers read, agents 403; non-members 404)

`GET /api/companies/{cid}/ai-stack` →
```
{ items: [AiConnection], ready: bool, blocking_reason: null|"no_ai"|"no_tested_ai"|"no_heavy_ai"|"no_everyday_ai",
  summary: { heavy: [{connection_id, label, percent}], everyday: [{connection_id, label, percent}] },
  options: [AiOption] }
```
`AiConnection`: `{id, position, rank_label ("Primary"|"Secondary"|"Third"|…), label, engine, option (e.g.
"claude_plan","anthropic_api","openai_api","openrouter","openai_compatible","ollama","cursor_plan","cursor_api",
"codex_api","opencode","antigravity_api"), provider_kind|null, model_provider_id|null, model|null, cli_id|null,
auth_mode|null, use_for, share, enabled, status: {state: "ready"|"needs_test"|"test_failed"|"needs_sign_in"|
"not_installed"|"paused"|"disabled", message|null, last_test|null, signed_in|null, account|null (email if the CLI
reports one), paused_until|null}, runs_24h: int, created_at}`.
`summary.percent` = the share-weighted split among enabled, ready, non-backup connections for that class
(backups listed with percent 0 and `backup: true`).
`options`: the catalog of addable options with `{option, label, group: "plan"|"api"|"local", engine, cli_id?,
provider_kind?, available: bool, reason?, needs_install: bool, install: {available, command}|null, warning?}`
(Ollama `available` only when detected; plan options unavailable when a plan connection of that CLI exists).

`POST /ai-stack` body `{option, label?, use_for?, share?, model?, model_provider_id?, credential_id?,
cli_provider_id?}` → 201 AiConnection, appended at the end. For API options the client first creates/tests the
ModelProvider with the existing `/model-providers` endpoints, then posts its id + model. For CLI api_key options
the client creates the credential with the existing credentials endpoint first. 409 `plan_already_connected`.
`PATCH /ai-stack/{id}` `{label?, use_for?, share?, enabled?, model?}`.
`PUT /ai-stack/order` `{ids: [...]}` (must be exactly the company's ids) → list.
`DELETE /ai-stack/{id}` → 204 (plan: logout + delete home; never deletes a ModelProvider/credential used elsewhere).
`POST /ai-stack/{id}/test` → AiConnection (API: provider live test for its model; CLI: adapter `run_test` with this
connection's auth). Plan sign-in: `GET|POST|DELETE /ai-stack/{id}/login` — same shapes and rules as B9's
`ClaudeLoginStatus` (`POST {method?: "claudeai"|"console"}` for Claude; Cursor ignores method).
All writes audited; changing `use_for`/`share`/order/enabled/model is a human-only action (agents never change
their own AI).

`GET /ai-setup` stays, derived from the stack (`ready`, `blocking_reason`, plus `stack_count`) so the gate keeps
working; `PUT /ai-setup` → 410 `gone` "Use /ai-stack". B9's `/cli-runtimes/claude_code/login` routes are removed
(never released). `/cli-runtimes` (install, versions) stays installation-level.

Readiness: ready iff at least one enabled connection with a passing test covers `heavy` and at least one covers
`everyday` (may be the same). A paused (usage-limited) connection still counts for readiness.

## 4. Routing (server-side, deterministic)

For each run (dispatcher → routing):
1. Owner pins (`runtime_config.pin_runtime` / `pin_model`) win, as today.
2. Class = heavy if `is_management_run` else everyday.
3. Candidates = connections that are enabled, cover the class, have a passing test, are usable now (installed CLI,
   plan signed in, not paused, provider enabled and allowed by `allowed_model_providers` for api ones).
4. Among candidates with `share > 0`, pick the one with the smallest `(runs started on it in the last 24 h + 1) /
   share`; ties → lower `position`. If none, the first backup (`share == 0`) by position.
5. No candidate → the task waits (`waiting_for_runtime`) with a reason naming why ("Claude plan paused until 3:45
   pm; no other AI covers heavy work").
6. The run records `ai_connection_id`, runtime, provider/model and `routing_reason` ("AI stack: Claude plan
   (primary, Most) for heavy work").
7. API connections run on `company_agent` with the connection's provider + model; CLI connections run on
   `cli:<id>` with the connection's auth (key from its credential, plan home), model if set, and the role-aware
   tools/brief from B9 for every CLI.
8. A plan usage limit pauses only that connection until reset (else +1 h) → its work moves to other candidates;
   login expired → that connection `needs_sign_in`. Never retried as permanent failures. Alerts as in B9.
Budgets, approvals and guardrails are unchanged and apply to every connection. Old routing modes
(`free_first`, `quality_first`, paid-fallback approvals) no longer apply to stack-routed runs: the owner chose each
connection and its share explicitly.

## 5. Terminal

`foundryos ai list | add | remove <id|label> | test <id|label> | login <id|label> [--console] | logout <id|label>`
(`login` execs the CLI's own login with the connection's env and inherited TTY). `foundryos setup` step "ai":
choose an option (same catalog), connect it, test it, then "Add another AI? [y/N]"; the first gets `everything`
/ "Most". Non-interactive: existing `--provider …` flags add one connection (`--provider claude-plan`,
`cursor-plan` require an already signed-in CLI). The separate "work" step is removed.

## 6. Web

One component, **AiStack**, used by onboarding ("Connect your AI") and the AI panel in Settings. No more
"Required / Optional / Coding CLIs / Work model / More options" sections.
* Ordered list of connection rows: rank label (Primary, Secondary…), label + option, model, status chip, "Use
  for" (Everything / Heavy lifting / Everyday work), "Importance" (Most / More / Normal / Less / Backup only),
  enabled switch, move up/down (keyboard accessible; drag optional), menu: Test, Sign in/out (plan), Change model
  (API), Rename, Remove (confirm).
* A plain-language summary under the list from `summary` ("Heavy lifting: Claude plan 67%, Anthropic API 33%.
  Everyday work: …").
* "Add AI" opens a picker grouped "Sign in with your plan — no API key" (Claude, Cursor), "API keys"
  (Anthropic, OpenAI, OpenRouter, OpenAI-compatible, Cursor, Codex, OpenCode, Antigravity), "On this computer"
  (Ollama). Each option runs a short flow inside the dialog: install if needed (existing confirm dialog), sign in
  (plan; B9 rules: no credential inputs) or key + model (reuse the provider test and model list), test, add.
* Onboarding can continue when `ready`; the gate shows `blocking_reason` copy with the next step.
* States everywhere: loading, empty ("No AI yet — add one to start"), error, not available (older backend).
