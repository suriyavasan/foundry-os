# M10 — AI setup in onboarding, coding-CLI workforce, Reflections, Command Center — spec

Owner direction (2026-10-24):
- Onboarding must make the user connect a **working AI API** (OpenRouter, Anthropic, OpenAI,
  or any OpenAI-compatible API). No working API → the platform cannot be used.
- Optionally connect/install **coding CLIs** (Claude Code, Codex CLI, OpenCode, Antigravity CLI, …) so
  employees can do their work through them.
- Two ways to run: **API only**, or **API for managing the company + CLIs for the work**.
  Company activity (AI Co-founder, HR, leads, reviews, planning) always uses the API; the
  CLIs are used for employees' work.
- Onboarding as individual animated screens (Aceternity-style effects).
- **Reflections** next to the org chart: the AI Co-founder posts reflections; the human
  reacts/responds, which sets the company's top standards.
- The thoughts widget becomes a **Command Center**: every input gets an output.

## 0. Facts verified on this machine (2026-10-24)
- Installed: Claude Code 2.1.292 (`/opt/homebrew/bin/claude`), OpenCode 1.18.3
  (`~/.opencode/bin/opencode`). Not installed: Codex CLI. Node 23 / npm 10 available.
- Claude Code headless: `claude -p "<prompt>" --output-format stream-json --verbose`
  (`--include-partial-messages` optional), `--permission-mode` ∈ {acceptEdits, auto,
  bypassPermissions, manual, dontAsk, plan}, `--allowedTools/--disallowedTools`,
  `--mcp-config <json|file>` + `--strict-mcp-config`, `--model`, `--max-budget-usd`,
  `--bare` (skip hooks/plugins/auto-memory), `--setting-sources`, `--settings`,
  `--append-system-prompt`, `--no-session-persistence`, `--restricted` (removes command/
  code-running tools and WebFetch). Auth: own login, or `ANTHROPIC_API_KEY` env.
- OpenCode headless: `opencode run "<msg>" --format json -m provider/model --dir <path>
  [--agent X] [--pure]`; `--auto` auto-approves (dangerous — never use); permissions via
  `opencode.json` (`permission` map); providers via `opencode auth login` or env keys;
  MCP servers via config (`mcp` section).
- Codex CLI (docs): `codex exec "<task>" --json` (JSONL events `thread.started`,
  `turn.started`, `turn.completed` (usage), `item.*`, `error`), `--sandbox workspace-write`
  (OS sandbox: Seatbelt on macOS / Landlock on Linux), `-C <dir>`, `-m <model>`,
  `--skip-git-repo-check`, `--ephemeral`, `--ignore-user-config`, `-o <file>`; auth: saved
  `codex login` or `CODEX_API_KEY` for the invocation; MCP via `config.toml`
  (`-c mcp_servers.<name>.command=…` overrides). Install: `npm install -g @openai/codex`.
- The implementer must re-verify every flag against `--help` / official docs before use and
  record the verified flag set (with versions) in `docs/cli-runtimes.md`.

## 1. AI readiness gate
- **Ready** = the company has a *management model*: an enabled model provider whose latest
  test passed all checks — reachable, authenticated, **tool calling works**, served
  context ≥ 32 768 tokens (or provider-declared for cloud) — selected as
  `policy.default_model_provider_id` + `default_model_name`.
- `GET /api/companies/{cid}/ai-setup` →
  ```json
  { "ready": false, "blocking_reason": "no_tested_provider" | "provider_test_failed" | "no_default_model" | null,
    "management": { "provider_id": "…|null", "provider_name": "…", "kind": "openrouter", "model": "…", "last_test": ProviderTest|null },
    "work_mode": "api" | "hybrid",
    "work_runtime": "company_agent" | "cli:claude_code" | "cli:codex" | "cli:opencode" | "cli:antigravity" | null,
    "clis": [CliRuntimeStatus] }
  ```
- `PUT /api/companies/{cid}/ai-setup` `{management_provider_id, management_model, work_mode,
  work_runtime}` — owner/admin; validates readiness (409 `ai_not_ready` with reason).
- **Enforcement:** dispatcher never dispatches and the wake queue never wakes while not
  ready (tasks wait with `blocked_reason="ai_setup_required"`); company-scoped creation of
  notes/commands/tasks/hires returns 409 `ai_setup_required` until ready (owner settings,
  providers, credentials, ai-setup, auth, team stay available). Existing companies without
  a passing provider become not-ready until the owner completes AI setup (the demo company
  will show the gate).
- Provider test extended: `{ok, checks:[{name:"reachable"|"auth"|"tool_calling"|
  "context"|"model_exists", ok, detail}], served_context, latency_ms, tested_at}`. Supported
  kinds: `openrouter`, `anthropic`, `openai`, `openai_compatible` (any base URL incl. local
  Ollama, Groq, Together, DeepSeek…), `ollama` (kept). Model list endpoint
  `GET /model-providers/{id}/models` (provider's list API where available) for pickers.

## 2. Work modes
`policy.work_mode`:
- `api` — every agent runs on `company_agent` with API providers (today's behaviour).
- `hybrid` — **management roles** (kind founder; templates hr-partner, *-lead; any
  planning/review/hiring/routine task) always run on `company_agent` with the management
  model. **Work tasks** (`kind=work`) assigned to non-management agents run on
  `policy.work_runtime` (a CLI runtime). Per-agent `runtime` override still wins (owner only).
The dispatcher picks the runtime per run and records `routing_reason`.

## 3. CLI runtimes (`backend/app/runtimes/cli/`)
One `RuntimeAdapter` per CLI (`cli:claude_code`, `cli:codex`, `cli:opencode`, `cli:antigravity`), sharing a
base (process spawning, minimal env, JSON-lines parsing, cancel SIGTERM→SIGKILL, timeouts,
workspace, artifact collection).

**CliRuntimeStatus** (`GET /api/cli-runtimes`, installation-level):
`{id, display_name, installed, binary_path, version, auth_modes:["own_login","api_key"],
auth: {mode, configured: bool, credential_id|null, last_test: {ok, detail, tested_at}|null},
sandbox: {available: bool, kind: "os"|"docker"|"none", detail}, install: {available: bool,
command: "npm install -g @openai/codex", requires: ["npm"]}, login_hint: "Run `codex login`
in a terminal", warnings: [string]}`.

Endpoints (installation owner only; agents 403):
- `POST /api/cli-runtimes/{id}/install` `{confirm: true}` — runs the exact install command
  shown (npm global install) as a supervised child; streams/stores redacted log
  (`GET …/install-log`); never runs without `confirm`; never uses sudo.
- `PUT /api/cli-runtimes/{id}/auth` `{mode: "own_login"|"api_key", credential_id?}` — API
  key stored in the M3b credential store; never returned.
- `POST /api/cli-runtimes/{id}/test` — runs a tiny headless prompt (e.g. "Reply with OK")
  in a temp dir with a hard timeout (60 s) and budget cap (`--max-budget-usd 0.05` where
  supported); parses the JSON output; result stored.
- Interactive logins (`claude` /login, `codex login`, `opencode auth login`) are the
  user's step in their terminal — never automated.

**Isolation & safety:**
- `api_key` mode: isolated `HOME` per agent (`data/agents/<id>/cli-home`), key injected
  only into that process's env (`ANTHROPIC_API_KEY`, `CODEX_API_KEY`, provider env for
  OpenCode, `GEMINI_API_KEY` for Antigravity CLI). Strongest isolation — recommended.
  Antigravity CLI (`agy`) supports only this mode: its settings and MCP servers live under
  `$HOME/.gemini/`, which we never write in a person's real home. Not an npm package, so no
  automatic install (`install.manual` carries Google's `curl | bash` command). Details and
  verified/unverified flags: `docs/cli-runtimes.md`.
- `own_login` mode: the CLI needs the real `HOME` to find its login → weaker isolation; UI
  shows a warning (and a note to check the tool's terms for automated use). Company OS never
  reads, copies or logs CLI credential files.
- cwd = run workspace; no extra dirs; user hooks/plugins/memory disabled where supported
  (`claude --bare --setting-sources ""` or equivalent verified flags; codex
  `--ignore-user-config --ephemeral`; opencode `--pure` + per-run config).
- **Shell only inside a real sandbox:** Codex `--sandbox workspace-write` (OS sandbox, network
  off by default); Claude Code only when its sandbox setting is verified enabled for Bash
  (else Bash disallowed via `--disallowedTools`/`--restricted`); OpenCode bash permission
  `deny` unless Docker sandbox. Never `bypassPermissions`, `--dangerously-skip-permissions`,
  `--auto`, or `danger-full-access`. No WebFetch to private ranges.
- Budgets: reservation per run as today; Claude `--max-budget-usd` = reservation;
  others: watchdog on reported usage + hard timeout. Cost kinds: CLI-reported cost in
  api_key mode → `estimated`; own_login (subscription) → `unknown` with note
  `subscription`; local models → verified $0.
- Guardrails: every company action from a CLI goes through the agent API (tier checks,
  rate limits, DLP) exactly like `company_agent`.

**Company tools via MCP:** `runtime/company_agent/mcp_server.py` — a stdio MCP server
(runtime venv) exposing the role's company tools (get_context, report_progress,
submit_artifact, complete_task, update_task/block, request_help, find_colleagues) backed by
the existing `tools/company_tools.py` HTTP client. Per-run MCP config generated by the
adapter (Claude `--mcp-config` + `--strict-mcp-config`; Codex `-c mcp_servers.company.*`;
OpenCode per-run config). Run token passed only in the MCP server's env.

**Prompting:** role prompt + task + acceptance criteria + "use the `company` MCP tools to
report progress, save deliverables as artifacts and complete the task" + untrusted-content
rule. If the CLI ends without calling `complete_task`, the adapter collects new/changed
files in the workspace and the final message, submits them as artifacts, and maps the run to
the M5 rules (not_completed → retry/fail).

## 4. Reflections
Data: `reflections` (`company_id`, `author_agent_id` (AI Co-founder), `kind` ∈
`observation`,`standard_proposal`,`retro`,`risk`,`praise`; `title`, `body` (markdown ≤ 6 KB),
`evidence` (json list of `{type, id, label}` refs to tasks/artifacts/runs/projects),
`proposed_standard` (text ≤ 500, optional), `status` ∈ `open`,`endorsed`,`revised`,
`rejected`,`adopted`,`archived`, timestamps). `reflection_responses` (`reflection_id`,
`author_type` human/agent, `author_id`, `reaction` ∈ `endorse`,`challenge`,`refine`,
`comment`, `body`, created). `company_standards` (`title`, `statement`, `source_reflection_id`,
`adopted_by_user_id`, `adopted_at`, `status` active/retired, `applies_to` families/tags).
Behaviour:
- AI Co-founder posts reflections: `daily-reflection` routine (enabled by default), after a
  project completes/fails, after repeated revision requests (pattern), after Founder input
  that implies a standard. Tools: `company_post_reflection`, `company_reply_reflection`,
  `company_revise_reflection`.
- Human reacts: endorse / challenge / refine (+ text). Any human response wakes the AI
  Co-founder (coalesced) to reply/revise.
- **Standards are set by the human:** a `standard_proposal` becomes an adopted
  `company_standard` only when a human endorses it (or endorses a revision). The AI
  Co-founder can never adopt or retire standards. Active standards are injected into every
  agent's context ("Company standards") and shown in a Standards list.
- Owner API: `GET /reflections?status=`, `GET /reflections/{id}` (+responses),
  `POST /reflections/{id}/responses` `{reaction, body}`, `POST /reflections/{id}/archive`,
  `GET /standards`, `POST /standards` (human-authored), `PATCH /standards/{id}` (retire/edit).
  Stream events: `reflection.created`, `reflection.updated`, `reflection.responded`,
  `standard.adopted`, `standard.updated`.

## 5. Command Center (replaces the Founder's-notes widget; notes remain the storage)
Every input produces an output.
- `POST /api/companies/{cid}/command` `{input}` →
  - **Slash command** (deterministic, instant, executed by the control plane with the
    human's permissions): `/help`, `/status`, `/goals`, `/agents`, `/tasks [status]`,
    `/spend [day|month]`, `/approvals`, `/pause`, `/resume`, `/goal <text>`,
    `/task <title> [@agent]`, `/hire <template> [reason]` (hiring request → HR),
    `/approve <id>`, `/reject <id> [note]`, `/standards`. Output = structured
    `{kind: "text"|"table"|"list"|"error", title, text, rows?, columns?, links?: [{label, panel}]}`.
  - **Free text** → stored as a Founder's note, wakes the AI Co-founder with a short
    debounce (5 s, priority); output starts as `pending`.
- `CommandExchange` `{id, input, input_kind: "command"|"note", created_at, status:
  "done"|"pending"|"processing"|"answered"|"failed", output: CommandOutput|null, note_id,
  run_id, actions: [{type, ref_id, label}], updated_at}`. `GET /command-center/feed?cursor=`
  (newest first, paginated) merges commands and notes.
- **Guaranteed output:** free-text exchanges move `pending → processing` (Co-founder run
  started; live progress lines from its run events) → `answered` (reply + action chips).
  If the run fails/times out, or nothing answers within 15 min, the system writes an
  output explaining what happened and retries once automatically; `POST
  /command-center/{id}/retry` for manual retry. Stream events: `command.updated`.
- Command history is per company, visible to owner/admin/member (viewers read-only).

## 6. Onboarding (individual animated screens)
Full-screen steps, one decision per screen, keyboard friendly, `prefers-reduced-motion`
respected, effects implemented in-house with `motion` (MIT) in the style of Aceternity UI
(spotlight, background beams, text-generate reveal, multi-step loader, moving-border CTA,
placeholders-and-vanish input, card hover). Do not paste Aceternity source unless its
license clearly permits it — record the decision in the web README.
1. **Welcome** — spotlight + text reveal ("A company that runs itself."), Start.
2. **Your account** — username + password (+ setup token when required).
3. **Your company** — name, then one-line idea (vanish-input), timezone.
4. **Connect your AI** (mandatory, one screen, one save) — two labelled sections:
   - **Model API** (required; runs the company: AI Co-founder, HR, leads, reviews) — provider
     cards: OpenRouter · Anthropic · OpenAI · OpenAI-compatible (base URL; local Ollama
     detected and offered here). Key input write-only. "Test" runs the multi-step loader
     (Reachable → Authenticated → Tool calling → Context window → Ready) with real results;
     model picker from `/models`. Testing stores nothing but the provider and its test.
   - **Coding CLIs** (optional; hands-on work) — always visible, so people see what is on the
     computer: Claude Code, Codex, OpenCode tiles with live status (installed/version, auth
     mode, sandbox availability, test); Install (confirm dialog showing the exact command),
     auth mode choice (API key recommended vs own login + warning), Test. One radio group
     "Who does the hands-on work": "Model API only" (default) or a CLI that tested OK.
   One button saves both (a single `PUT /ai-setup`); it is disabled until the model API
   passed its test and the CLI choice is valid.
5. **Meet your team** — AI Co-founder and HR appear (beams), summary of choices, Launch
   (moving-border) → canvas with the Command Center focused.
Existing users whose company is not AI-ready see step 4 as a full-screen gate after
login. Settings → **AI** panel shows the same form (one Save) any time.

(Revised 2026-10-24: steps 4 and 5 were merged after user feedback — two separate saves hid
the coding CLIs behind a mode card, so people could not tell whether a CLI was installed.)

## 7. Canvas layout
Row 1 (tall): **Organization** (span 6) · **Reflections** (span 3) · **Command Center**
(span 3). Below 1440 px: Organization full width, Reflections + Command Center side by side;
mobile stacked. Reflections widget: latest reflections (kind chip, title, excerpt, evidence
chips), reaction buttons (Endorse / Challenge / Refine) with inline response composer,
status chips, "Standards (n)" link; panel `?panel=reflections`, `?panel=reflection:<id>`,
`?panel=standards`. Command Center widget: exchange cards (input line, output card with
pending/processing animation and live Co-founder progress, answered output with action
chips), input with slash-command autocomplete + ↑/↓ history, ⌘↵ send; panel
`?panel=command-center` for full history.

## 8. Migration & tests
Migration `0009_ai_setup_cli_reflections`. Tests: readiness gate (dispatch/wake/creation
blocked, settings allowed), provider test checks, work-mode routing, each CLI adapter with
fake binaries emitting recorded JSON streams (success, failure, cancel, timeout, malformed,
budget cap, no complete → artifacts collected), MCP server tool calls against a stub API,
isolation (api_key HOME isolation, env minimal, credentials never logged, dangerous flags
never present — assert on the built argv), install requires confirm and never sudo,
reflections lifecycle + standards adoption only by humans + injection into context,
command parsing/permissions/outputs, guaranteed-output fallback and retry. Live checks
(manual, honest): Claude Code and OpenCode tests on this machine (Codex only if the owner
installs it), one hybrid-mode work task executed through a CLI.
