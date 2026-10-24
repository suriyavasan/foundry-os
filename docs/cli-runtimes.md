# Coding-CLI runtimes (Milestone 10)

Company OS can run an employee's *work* through a coding CLI instead of the built-in `company_agent` runtime: **Claude Code**, **Codex CLI** or **OpenCode**
(`cli:claude_code`, `cli:codex`, `cli:opencode`). Management — the AI Co-founder, HR, leads, planning, reviews, hiring — always uses the company agent with the API
model. API contract: `docs/api.md` ("Milestone 10"); spec: `docs/specs/m10-ai-setup-cli-reflections.md`; code: `backend/app/runtimes/cli/`, `backend/app/services/cli_runtimes.py`,
`runtime/company_agent/mcp_server.py`.

## Verified flags (2026-10-24)

"Verified" = read from the binary's own `--help` on this machine, or (OpenCode only) exercised live. Nothing else is claimed.

### Claude Code 2.1.292 (`/opt/homebrew/bin/claude`)

Checked with `claude --version` and `claude --help` only. **Claude Code was never started with a prompt during development** (it would use the owner's own login); the adapter is
tested against a fake binary that prints a stream in the documented `stream-json` shape.

| Flag used | In `--help` | Why |
|---|---|---|
| `-p <prompt>` (prompt = first positional) | yes | headless |
| `--output-format=stream-json` + `--verbose` | yes (`--verbose` is required by the documented stream-json print mode; not exercised) | JSON-lines events |
| `--no-session-persistence` | yes | no session files |
| `--restricted` | yes | removes Bash/PowerShell/REPL and WebFetch, ignores user/project/local settings, confines file tools to the working directories, refuses `bypassPermissions` |
| `--bare` (api_key mode only) | yes | no hooks, plugins, auto-memory, CLAUDE.md, keychain/OAuth: auth is `ANTHROPIC_API_KEY` only |
| `--disable-slash-commands` | yes | no skills |
| `--permission-mode=dontAsk` | yes (choices: acceptEdits, auto, bypassPermissions, manual, dontAsk, plan) | anything not pre-approved is denied; **never** `bypassPermissions`/`auto` |
| `--tools=Read,Write,Edit,Glob,Grep` | yes | the built-in set, nothing else |
| `--allowedTools=mcp__company,Read,Write,Edit,Glob,Grep` / `--disallowedTools=Bash,WebFetch,WebSearch,Task,…` | yes | explicit allow/deny |
| `--mcp-config=<file>` + `--strict-mcp-config` | yes | only the per-run `company` MCP server |
| `--max-budget-usd=<reservation>` | yes (print mode) | hard cap = the run's budget reservation (omitted when there is none) |
| `--model=<name>` | yes | from the agent's `runtime_config.model` |

Variadic options are written `--flag=value` so they cannot swallow the prompt. Environment: `DISABLE_AUTOUPDATER=1`, `DISABLE_TELEMETRY=1`; api_key adds `ANTHROPIC_API_KEY` and `CLAUDE_CONFIG_DIR=<isolated home>/.claude`.
Stream events parsed: `assistant` (`text`, `tool_use`), `user` (`tool_result`), `result` (`is_error`, `subtype`, `result`, `total_cost_usd`, `usage`).

### OpenCode 1.18.3 (`~/.opencode/bin/opencode`)

`opencode run --help` plus **a live run** (see below).

| Flag / setting | Verified | Why |
|---|---|---|
| `opencode run <message>` | `--help` + live | headless |
| `--format json` | `--help` + live | raw JSON events |
| `--dir <path>` | `--help` + live | working directory = the run workspace |
| `--pure` | `--help` + live | no external plugins |
| `-m <provider/model>` | `--help` + live | model |
| **never** `--auto` | — | "auto-approve permissions … (dangerous!)" |
| env `OPENCODE_CONFIG=<file>` | live (strings in the binary + the run used it) | per-run provider, MCP and permissions |
| env `OPENCODE_DISABLE_AUTOUPDATE`, `…_PROJECT_CONFIG`, `…_CLAUDE_CODE`, `…_EXTERNAL_SKILLS`, `…_LSP_DOWNLOAD` | names found in the binary; effect not individually tested | no auto-update, no project config, no Claude Code/skill imports |
| config `provider.<id>.npm = "@ai-sdk/openai-compatible"` + `options.baseURL` | live | any OpenAI-compatible endpoint, **no account needed** |
| config `permission` (`bash`, `webfetch`, `task`, `external_directory`, … = `deny`; `read/edit/glob/grep/list`, `company_*` = `allow`) | accepted by 1.18.3; `external_directory` denial observed live (the model tried a path above its run directory and was refused) | shell/web/subagents off, file tools inside the workspace |
| config `mcp.company = {type: "local", command, environment}` | live | the company tools |

Events seen live: `step_start`, `tool_use` (`part.tool`, `part.state.{status,input,output}`), `text` (`part.text`), `step_finish` (`part.tokens.{input,output,reasoning,cache.{read,write}}`, `part.cost`, `part.reason`).
The `error` event shape is taken from OpenCode's behaviour in general and has not been triggered.

On a fresh isolated `HOME` OpenCode installs `@ai-sdk/openai-compatible` with npm on the first run (network needed once per agent home).

### Codex CLI (not installed — coded from the official documentation)

Taken from developers.openai.com/codex (CLI reference and non-interactive mode), read 2026-10-24. **Codex is not installed here and was not installed for this work; nothing in the Codex adapter has run
against the real binary.** It is tested with a fake binary printing the documented JSONL shape.

`codex exec --json --sandbox workspace-write -C <workspace> --skip-git-repo-check --ephemeral --ignore-user-config -o <file> [-m <model>] -c <key=value>… <prompt>`;
`-c approval_policy="never"`, `-c sandbox_workspace_write.network_access=false`, `-c mcp_servers.company.{command,args,env}=…`. Sandbox values per the docs: `read-only | workspace-write | danger-full-access`
(`danger-full-access` is never used; the connectivity test uses `read-only`). Auth: `CODEX_API_KEY` for one invocation, `CODEX_HOME` for the config/credentials directory. Events: `thread.started`, `turn.started`,
`item.started|completed` (`agent_message`, `command_execution`, `mcp_tool_call`, `file_change`, `web_search`), `turn.completed` (`usage.{input_tokens,cached_input_tokens,output_tokens}`), `turn.failed`, `error`.
Unverified assumptions: that `approval_policy` and the `sandbox_workspace_write.network_access` keys are accepted via `-c`, and whether `--ignore-user-config` also skips `auth.json` in own-login mode.

## Install

`npm install -g @anthropic-ai/claude-code`, `npm install -g @openai/codex`, `npm install -g opencode-ai` (package names exist on the npm registry: `npm view` 2026-10-24). `POST /api/cli-runtimes/{id}/install`
runs exactly that command (no shell, no sudo; a global prefix that needs root fails with npm's own message and the log says so), only with `confirm: true`. **The install endpoint was exercised with a fake npm only; no real
install was run.** Interactive logins are the person's step in a terminal.

## Isolation model

| | `api_key` | `own_login` |
|---|---|---|
| `HOME` | `data/agents/<agent id>/cli-home` (per agent) | the real `HOME` (the CLI must find its own login) |
| credentials | the key from the credential store, in the child's environment only (`ANTHROPIC_API_KEY`, `CODEX_API_KEY`, `OPENROUTER_API_KEY`… or a custom-provider variable for OpenCode) | none: Company OS never reads, copies or logs a CLI's credential files |
| cost kind | `estimated` when the CLI reports a cost, else the reservation is charged as an `estimated`; a **local** model is `verified` $0 | `unknown`, nothing charged (`billing_note: subscription`) |
| UI | recommended | warning: weaker isolation, check the tool's terms |

Common to both: the child gets a minimal environment (`PATH`, locale, TLS and proxy variables, `HOME`, the CLI's own variables — no `COMPANY_OS_*`, no owner token, no secret key); `cwd` and `--dir`/`-C` = the run workspace;
its own process group (cancel = SIGTERM, SIGKILL of the group after `runtime_kill_grace_seconds`); user hooks/plugins/memory disabled where the CLI allows; every argv and generated config is scanned before the spawn and the
adapter refuses `--dangerously-skip-permissions`, `--allow-dangerously-skip-permissions`, `bypassPermissions`, `--auto`, `danger-full-access`, `--dangerously-bypass-approvals-and-sandbox`, `--yolo`, `--full-auto`
(`runtimes/cli/catalog.py::assert_safe_argv`; tests assert it for every CLI, mode and the connectivity test).

**Shell only inside a real sandbox.** Codex gets a shell only with `--sandbox workspace-write` (OS sandbox per the docs, network off). Claude Code's Bash is never enabled (its sandbox setting cannot be verified here; `--restricted`
removes it) and OpenCode's `bash` permission is `deny`. So in M10 Claude Code and OpenCode work with files in the workspace and the company tools.

**Company tools.** Each run gets a per-run directory `data/agents/<id>/cli-run/<run id>` (0700, removed afterwards) holding a 0600 token file and the generated MCP config. The `company` MCP server
(`python -m company_agent.mcp_server`, hand-written stdio JSON-RPC, no new dependency) exposes `get_context`, `report_progress`, `submit_artifact`, `complete_task`, `update_task`, `request_help`, `find_colleagues`
over the same agent API as `company_agent`, so risk tiers, rate limits, DLP and roles apply identically. The run token is **never** in argv or in a config file: the MCP server's environment carries only
`COMPANY_OS_RUN_TOKEN_FILE`. A CLI that can read files outside its workspace could read that file; the token only ever allows what the run itself could do (and dies with the run), and Claude Code (`--restricted`) and OpenCode (`external_directory: deny`) are confined to the workspace.

**Completion.** The task is complete when the CLI called `complete_task` (the adapter checks the task state, not the CLI's words). Otherwise the adapter keeps new/changed text files (≤ 20, ≤ 1 MB) and the final message as artifacts and
returns a retryable failure `task_not_completed`, so the M5 retry/fail rules apply.

## Terms of use — read before using `own_login`

Using a personal subscription login (Claude Pro/Max, ChatGPT plans) for automated, unattended work may not be permitted by the provider's terms; some providers require API keys for that. Company OS does not decide this for you:
`api_key` is the recommended mode, `own_login` shows a warning, and nothing in this repository reads or copies the CLIs' login files. Check the current terms of the tool you use.

## What was and was not verified

Verified by automated tests (`backend/tests/test_m10_cli_*.py`, 50 tests, fake binaries that record argv, environment and generated files): argv/flags per CLI and mode, the forbidden-flag guard, HOME isolation, minimal
environment, the key only in the child's environment, the token only in a 0600 file, success / failure / crash / malformed lines / cancel with SIGTERM ignored / budget flag, artifact collection when the task was not completed,
cost kinds, the install endpoint (needs `confirm`, exact argv, no shell/sudo, redacted log, fake npm), the connectivity test, hybrid routing. The MCP server: `runtime/company_agent/tests/test_mcp_server.py` (protocol, tool
subset, errors, secrecy).

Verified live (2026-10-24, macOS, temporary Ollama on :11435 with `qwen2.5:7b`, `OLLAMA_CONTEXT_LENGTH=32768`, no account): OpenCode 1.18.3 via the whole product path — connectivity test (`Answered: OK`), hybrid work mode, a work
task dispatched to `cli:opencode`, the model calling `company_get_context`, `company_submit_artifact` and `company_complete_task` through the MCP server, the task `done`, run recorded as `verified` $0 (50,730 input / 301 output tokens),
`external_directory` denial of a path above the workspace.

**Not verified:** Claude Code runs (never started), Codex (not installed), `own_login` runs of any CLI, the real `npm install`, Codex's sandbox, any cloud provider key, Docker.
