# FORK.md — Company OS agent runtime

This directory is a **pruned fork of [Hermes Agent](https://github.com/NousResearch/hermes-agent)** by
Nous Research (MIT, see `LICENSE`), reshaped into a "company employee" runtime for Company OS
(architecture decision D7, `docs/architecture.md` section 6.1; spec `docs/specs/m3-company-agent-runtime.md`).

| | |
|---|---|
| Upstream | https://github.com/NousResearch/hermes-agent |
| Upstream commit | `db1fa153fe24a051af44ef8ee862b93e27998060` (committed 2026-10-23, pinned in `vendor/hermes-agent`) |
| Forked | 2026-10-23 (milestone M3a) |
| Python | 3.14 (Hermes' exact pins, `uv.lock` untouched) |
| License | MIT (Nous Research, 2025) — `LICENSE` kept verbatim; upstream file headers kept |

Rules for this tree: upstream files are modified **only** when unavoidable; each modification carries a
`COMPANY FORK` comment and an entry in the changelog below. Everything we own lives in
`company_agent/`, `tools/company_tools.py`, `scripts/` and the docs in this directory.

## What we added (new files, not in upstream)

| Path | Purpose |
|---|---|
| `company_agent/` | The launcher: `run.py` (entrypoint, stdin spec -> JSONL stdout, signals, limits), `spec.py` (run spec validation), `protocol.py` (event writer: seq, single terminal, redaction), `runner.py` (builds `AIAgent`, callbacks -> events, time/cancel/finish handling), `prompts.py` (role prompt), `home.py` (HERMES_HOME, SOUL.md, enforced `config.yaml`, scrubbed environment), `toolpolicy.py` (toolset mapping/removal), `approvals.py` (deny-all approval callback), `context.py` (tool client config), `guard.py` (M6: tool registry check, untrusted-content tagging, injection flagging). |
| `company_agent/tests/` | pytest suite incl. a deterministic fake OpenAI-compatible model server and a stub agent API (counts in the milestone report). |
| `tools/company_tools.py` | The `company` toolset (39 tools: work tools, M4 colleague/hiring/HR tools, M5 autonomy tools, M6 `company_decide_peer_review` and `company_provider_action`, M10 `company_post_reflection` / `company_reply_reflection` / `company_revise_reflection`, M12 `company_plan_project`, M14 `company_review_pull_request`, M15 `company_request_purchase` / `company_review_subscription`) calling the Company OS agent API. Auto-discovered by Hermes' tool registry. |
| `company_agent/mcp_server.py` | M10: a hand-written stdio MCP server (JSON-RPC, no new dependency) that exposes a subset of the company tools to coding CLIs (Claude Code, Codex, OpenCode). Config from the environment; the run token is read from a 0600 file. Tests: `company_agent/tests/test_mcp_server.py`. |
| `scripts/check_imports.py` | Import check for the pruned tree (run after any prune / upstream sync). |
| `scripts/scan_dropped_imports.py` | Static list of imports of dropped modules still present in kept code. |
| `scripts/live_smoke.py` | Manual live smoke against a real local model with a printing stub API. |
| `.gitignore`, `README.md`, `AGENTS.md`, `FORK.md` | Ours (upstream's `README.md`, `AGENTS.md`, `CONTRIBUTING.md` were replaced/removed). |

## Modifications to upstream files (changelog)

Every row is also marked `COMPANY FORK` in the source.

| # | File | Change | Why |
|---|---|---|---|
| 1 | `agent/model_metadata.py` | `MINIMUM_CONTEXT_LENGTH` is now computed by `_resolve_minimum_context_length()`: env `HERMES_MIN_CONTEXT_TOKENS`, else `model.min_context_tokens` in `$HERMES_HOME/config.yaml`, else upstream's 64 000. Values < 2048 / non-integers are ignored. Added `import os`. | Upstream rejects models with < 64K context (`agent_init.py` `_enforce_minimum_context`, `conversation_loop.py`). Company OS runs 32K local models (16 GB machine); runs use a floor of `min(16384, context_length)`. |
| 2 | `agent/file_safety.py` | New workspace jail: `company_jail_root()`, `get_company_jail_error(path, write=)`; hooked into `get_read_block_error` (reads), `_classify_write_denial` / `get_write_denied_error` / `is_write_denied` (writes, new denial class `company_jail`). Active only when `HERMES_COMPANY_JAIL=<workspace>`. Paths are `realpath`ed (symlinks cannot escape). Read-only exceptions: `$HERMES_HOME/skills`, `$HERMES_HOME/cache/spillover` (Hermes tells the model to read spilled tool output there). | Upstream's `HERMES_WRITE_SAFE_ROOT` covers writes only and is documented as not a boundary. The file tools, vision/transcription loaders and search-result filtering all reach `get_read_block_error` / `get_write_denied_error`, so one check covers them. |
| 3 | `tools/file_tools_write_guards.py` | `_check_sensitive_path` calls `get_company_jail_error(..., write=True)` first on every candidate path. Import added. | The `patch` tool's V4A mode reads a target (`read_file_raw`) before any write-denial check runs; vetting every header path up front means an out-of-workspace file is never read or leaked through a failed diff. |
| 4 | `toolsets.py` | Added static toolsets `company` (the 19 company tools; 9 were added in M3b for M4: `find_colleagues`, `request_hire`, `search_skills`, `install_skill`, `author_skill`, `assign_skills`, `list_role_templates`, `create_role_template`, `hire_agent`) and `skills_read` (`skills_list`, `skill_view`). | `company`: explicit membership instead of registry-only toolset. `skills_read`: employees get read-only skills (no `skill_manage`, no self-authored skills); the launcher maps spec `skills` -> `skills_read`, `skills_manage` -> `skills`. |
| 5 | `model_tools.py` | `_pre_dispatch_guards`: `ImportError` for `acp_adapter.edit_approval` no longer falls into the blanket `except Exception` (which returned "Edit approval denied: approval guard failed" for every `write_file` / `patch`). | `acp_adapter/` is not shipped. Found by the end-to-end test (a plain write inside the workspace failed). |
| 6 | `agent/system_prompt.py` | `build_system_prompt_parts` returns `_build_company_prompt_parts()` when `agent.platform == "company"`: identity (SOUL.md) + one-paragraph tool-use guidance + the stock tool-use-enforcement block for models that need it (qwen, gpt, gemini...) + skills index + date. | The stock prompt is ~1.5K tokens of Hermes-product text (docs pointer, steering protocol, coding posture, profile/toolchain probes, "Finishing the job" for coding) that is irrelevant to an employee and hurts 7B models. Measured: 5 771 -> 2 344 characters for the same run. |
| 7 | `pyproject.toml` | `[tool.pytest.ini_options] testpaths = ["company_agent/tests"]` (was `["tests"]`). Nothing else: dependency tables and `uv.lock` are untouched. | Upstream's `tests/` is not shipped (see "Tests" below). |
| 8 | `tools/xai_video_tools.py` | **Deleted.** | Imports `plugins.video_gen`, which is dropped; discovery logged a warning on every start. Video generation is out of scope. |

Removed from the top level because nothing imports them or they would mislead: `README.md`, `CONTRIBUTING.md`
(upstream docs), `hermes` (CLI wrapper), `toolset_distributions.py` (unreferenced).

## Keep / drop

**Kept (verified needed by the core loop and its import graph):** `run_agent.py`, `model_tools.py`,
`toolsets.py`, `utils.py`, `hermes_constants.py` (+ `_scratch*`), `hermes_logging.py`, `hermes_time.py`,
`hermes_yaml.py`, `hermes_bootstrap.py`, `hermes_dir_acl.py`, `hermes_startup_watchdog.py`,
`registration_lifecycle.py`, `cli.py` (imported by `hermes_cli`/`tools`), all `hermes_state*.py`, `agent/`,
`tools/` (incl. `environments/`, `connectors/`), `hermes_cli/`, `gateway/` **without** `platforms/*`
(`platforms/__init__.py` only), `cron/`, `pm/`, `providers/`, `hermes_platform/`,
`plugins/{__init__,plugin_loader,plugin_storage,plugin_utils}.py`, `plugins/model-providers/` (all 42; the
spec's minimum is `custom`, `openrouter`, `anthropic`), `plugins/web/`, `plugins/context_engine/`,
`plugins/memory/{__init__,config_schema,query_rewrite}.py`, `locales/en.yaml`, `plugin-catalog/`, `skills/`,
`optional-skills/` (skill-library source for M4), `pyproject.toml`, `uv.lock`, `ruff.toml`, `.python-version`,
`cli-config.yaml.example`, `SOUL.md`, `SECURITY.md`, `LICENSE`.

**Dropped:** `apps/`, `web/`, `website/`, `ui-tui/`, `tui_gateway/`, `acp_adapter/`, `optional-mcps/`,
`docker/`, `nix/`, `tests-js/`, `evals/`, `contributors/`, `assets/`, `native/`, `scripts/` (upstream's),
`tests/` (75 MB), `.github/`, `.git`, `locales/*` except `en.yaml`, `plugins/platforms/`,
`plugins/{browser,computer_use,cron_providers,dashboard_auth,delegation,disk-cleanup,google_meet,
hermes-achievements,image_gen,kanban,security-guidance,teams_pipeline,video_gen}/`,
`plugins/memory/{byterover,retaindb}/`, `gateway/platforms/*` adapters, `mini_swe_runner.py`, `batch_runner.py`,
`trajectory_compressor.py`, translated READMEs/CONTRIBUTING/SECURITY, `Dockerfile`, compose files, `flake.*`,
`justfile`, `lefthook.yml`, `package*.json`, `setup*.{py,sh,ps1}`, `activate*`, JS/lint config files.

Size: 63 MB of source (upstream checkout without `.git`: ~265 MB), virtualenv 181 MB.

### Dangling imports (known, accepted)

Kept modules still contain *lazy* imports of dropped surfaces (`gateway.platforms.*`, `tui_gateway.*`,
`plugins.platforms.*`, `plugins.{browser,computer_use,image_gen,cron_providers,...}`, `acp_adapter.*`). They sit
in code paths the company runtime never reaches (messaging delivery, dashboard/web server, desktop/TUI,
gateway startup, browser/image/TTS providers) or are already wrapped in `try/except` (e.g.
`tools/process_registry_wait_policy.py`, `tools/skills_tool_setup.py`). The one that was **not** guarded and sat
on the hot path (`acp_adapter.edit_approval`) is fixed in change #5. `scripts/check_imports.py` imports every
module the runtime needs and fails on any `Could not import tool module` warning.
A static scan of imports of dropped modules is reproducible with `scripts/scan_dropped_imports.py ../vendor/hermes-agent`.

## Execution safety summary (what is enforced in code, not prompts)

* **Environment** (`company_agent/home.py`): the process env is rebuilt from an allow-list
  (PATH, locale, proxy/CA vars, DOCKER_HOST) + our `HERMES_*` flags; `HOME=$HERMES_HOME/home`; no `.env`
  ever exists in the employee home; `HERMES_DISABLE_LAZY_INSTALLS=1`; `HERMES_YOLO_MODE`,
  `HERMES_SINGLE_QUERY_SESSION`, API keys etc. cannot leak in. The run token and model key exist only in the
  launcher's memory (stdin spec -> `AIAgent` args / the company tool client).
* **Generated `config.yaml`** (always overwritten for these keys): `approvals.mode: manual`, memory off,
  `skills.external_dirs: []`, no trusted project dirs, curator off, background review off,
  `auth.adopt_external_logins: false`, `security.allow_lazy_installs: false`, `tools.tool_search: off`,
  `web.backend: ddgs` with `keyless_fallback: false`, `terminal` per sandbox, model/context settings.
* **Private URLs** (M3b): `security.allow_private_urls: false` is written to the generated config and `HERMES_ALLOW_PRIVATE_URLS=false` is forced in the process env (an inherited opt-in cannot apply); Hermes' URL guard blocks loopback, RFC1918, link-local and cloud-metadata targets (tested for the control plane, Ollama and LAN addresses). The only network tool the company toolsets expose is search-only `web_search` (ddgs), which cannot be pointed at a URL.
* **Secrets outside the jail** (M3b): the control plane's `owner.token`, `secret.key` and database sit in the data dir above the workspace; tests assert the file tools cannot read, search or overwrite them (absolute path, `..`, symlink, `~`).
* **Toolsets**: only `company file web search skills skills_manage todo vision terminal code_execution` are
  accepted in a spec; `terminal` and `code_execution` are removed unless `sandbox.backend == "docker"`
  (progress warning emitted); spec `web` -> `search` (ddgs cannot extract); `skills` -> read-only.
* **Approvals**: `set_hermes_interactive_context(True)` + a callback that always answers `"deny"`.
* **Workspace jail** for file tools (change #2/#3) when `sandbox.backend == "none"`.
* **Limits**: iteration cap (Hermes `max_iterations`, exit 2), wall-clock cap enforced by the launcher
  (hard interrupt, exit 2 `time_limit`), SIGTERM/SIGINT -> hard interrupt -> `run.cancelled` (exit 130).
  The process always ends with `os._exit(code)` after the terminal event.

## Tests

Upstream's `tests/` (75 MB, thousands of tests tied to modules and platforms we dropped, run in
per-file subprocess isolation) is **not shipped**; keeping a subset would test mostly code we do not
run. Decision: rely on our own suite in `company_agent/tests/` (see `README.md`), which exercises the
patched upstream code paths through the real tools and a real launcher subprocess.

## Verification status (what was and was not exercised)

Exercised against the real systems on 2026-10-24 (macOS arm64, Python 3.14.7):
* the launcher end to end with a deterministic fake model server (JSON and SSE) and a stub agent API:
  full loop, errors, nudges, iteration cap, time limit, SIGTERM/SIGINT, jail, redaction, bad specs;
* M3b: the launcher driven by the **real Company OS backend** (embedded worker, real agent API, real Ollama `qwen2.5:7b` on a temporary 32K server): see the M3b section of `../docs/api.md` and the milestone report. That run exposed a launcher/tool bug the stub API could not (a Task body's own `error` field was mistaken for a tool failure, so the agent was never marked done); fixed in `tools/company_tools.py` and `runner.tool_result_is_error`, with regression tests.
* the launcher against a **real local Ollama `qwen2.5:7b`** (tool calling), including real DuckDuckGo
  (`ddgs`) web search; see `README.md` for the result and the Ollama context caveat;
* `web_search` (ddgs), `skills_list`, `todo_list`, `read_file`/`write_file`/`patch`/`search_files` through the
  real registry.

**Not** exercised: docker sandbox (`sandbox.backend == "docker"`: daemon not running; the generated
`terminal` config for it is unverified and the file jail is intentionally off there), Anthropic/OpenRouter
providers (no keys), the HR/hiring tools against a real model (they are covered against the stub API and the real endpoints' schemas; the HR flow driven by a local model is untested), Windows/Linux.

## Syncing with upstream

1. Check out the new upstream commit next to `vendor/hermes-agent`.
2. Re-apply the 8 changes above (each is small and marked `COMPANY FORK`); diff the dropped list for new
   top-level modules that the kept code now imports.
3. `uv sync --frozen --no-install-project --extra anthropic --extra ddgs`, then
   `python scripts/check_imports.py` and `python -m pytest`.
4. Update the commit hash here and in `company_agent/__init__.py` (`UPSTREAM_COMMIT`).


## Milestone 5 additions (company layer only, no upstream file touched)

* `tools/company_tools.py`: 11 new company tools (30 in total): `company_get_state`, `company_list_notes`, `company_triage_note`,
  `company_reply_to_note`, `company_manage_goal`, `company_manage_project`, `company_manage_schedule`, `company_escalate`,
  `company_request_help`, `company_propose_memory`, `company_verify_memory`; `company_create_task` gained `project_id`,
  `company_update_task` gained `retry` / `cancel` and hands a manager's own running task to a colleague.
  Small-model scaffolding applied to the new tools as to the old ones: lenient argument shapes (triage `actions` as objects, JSON,
  `type:id` strings or a mapping), empty-string ids dropped, `next` hints after goal/project/hire/triage calls, recovery `hint`s on known
  errors, state/notes output compacted.
* `company_agent/prompts.py`: one short playbook per role (AI Co-founder, project lead, specialist, manager, HR) selected by
  `spec.agent.role`; verified company memory in the identity prompt; review / failure-analysis tasks render the work under review;
  role-aware kickoff and completion nudges. `company_agent/context.py`: `handed_off` disposition. `spec.py`: tool names.
* Tests: `company_agent/tests/test_company_tools.py` (M5 tools, hints), `test_policy.py` (playbooks, memory, review prompts).

## Milestone 6 additions (company layer only, no upstream file touched)

* `company_agent/guard.py`: two hooks registered on Hermes' in-process plugin manager (`_hooks`; Hermes has no public API
  for first-party in-process hooks, so this touches a private attribute -- covered by tests that go through the real
  `model_tools.handle_function_call`; remember it when syncing upstream):
  `pre_tool_call` refuses any tool that is not in the run spec's `guardrails.tool_tiers` (fail closed; `company_*` tools are
  enforced by the agent API instead) and asks the control plane (`POST /guardrails/check-tool`) before running a tool of an
  outside-reaching tier (a control plane that cannot be reached means "no"); `transform_tool_result` wraps the results of
  web/file/command tools in `<untrusted source="...">` (the content cannot close or fake the tag) and flags common
  injection phrases (progress event + `POST /guardrails/flags`, categories only; never blocks). A refused call leaves a
  `[guardrail]` progress event because Hermes emits no start/complete callbacks for it.
* `tools/company_tools.py`: `company_decide_peer_review` (the reviewer's decision; success ends the run), `company_provider_action`
  (the control plane acts with a stored credential; the tool never carries one), a `pending_approval` answer is never treated as a
  completed task, `company_get_context` tags other agents' artifact excerpts as untrusted. 32 company tools in total.
* `company_agent/spec.py`: optional `guardrails` block and the two tool names; `prompts.py`: untrusted-content rule, peer-review
  task rendering (the exact outbound draft in an untrusted block, DLP findings, checklist, company constraints) and kickoff/nudge.
* The tool -> tier table lives in `backend/app/guardrails/tools.json` (single source of truth, sent in every run spec);
  `company_agent/tests/test_guard_registry.py` compares it with the tools Hermes really resolves.
* Tests: `test_guard.py`, `test_guard_registry.py`, `test_m6_runtime.py` (incl. a real launcher subprocess run).

## Milestone 12 additions (company layer only, no upstream file touched)

* `tools/company_tools.py`: `company_plan_project` (36 company tools in total): a lead or manager creates a project's first
  3-6 tasks in one call (at most 8; `depends_on_keys` resolved to the ids of tasks created earlier in the call, with a stable
  idempotency key per run and title). It stops at the first limit error (`limit_exceeded`, rate limit, budget, guardrail) and
  returns the created ids plus the error. No new backend route: it calls `POST /tasks` per task.
* `company_agent/prompts.py`: Co-founder goal-closure step, initiative wake handling (at most one goal and one project, or a
  decision), routines that take actions; leads plan once with `company_plan_project` and never re-plan a project that has
  tasks; the lead nudge no longer asks for `required_skills`; specialists rarely create subtasks; a "PROJECT WORKSPACE"
  section is rendered from the optional `spec["workspace"]`. `spec.py`: that optional `workspace` object and the tool name.
* Prompt caching (checked against `agent/prompt_caching.py`, `agent/system_prompt.py`): for `platform == "company"` the system
  prompt is `stable` (SOUL.md = our identity prompt + tool guidance; marked as the static prefix) then `volatile` (skills index and
  a date-only line), and the per-run task prompt is `ephemeral_system_prompt`, appended after both at call time. Our identity
  prompt therefore carries no task id, run id, workspace path or timestamp (a test pins this); everything that changes per
  run (including the new workspace section) is in the task prompt or the first user message, so the stable prefix and the tools
  array are byte-identical across runs of the same agent and the Anthropic early breakpoint can hit across runs.
* Tests: `test_company_tools.py` (`company_plan_project`), `test_policy.py` (M12 prompts, stable prefix), `test_spec.py` (`workspace`).

## Milestone 14 additions (company layer only, no upstream file touched)

* `tools/company_tools.py`: `company_review_pull_request` (37 company tools after M14): the assignee of a pull request review
  task records its one verdict (`accept` | `request_changes`, a note, an optional `head_sha`) with `POST /repo/pulls/{pull_id}/review`.
  Decision words models use (`approve`, `LGTM`, `reject` ...) are normalised; `request_changes` needs a note. A recorded verdict
  marks the task completed (the control plane completes the review task itself); a `pull_request_changed` error carries a hint
  not to retry. `company_provider_action`'s description now lists the bound-repository GitHub actions (read, comment, create/update
  issue, draft release) instead of `github.pr.open`: pushes, pull requests and merges are made by the control plane, never by an agent.
* `company_agent/spec.py`: the tool name (`COMPANY_TOOL_NAMES`; it must equal the keys of `backend/app/guardrails/tools.json`
  `company_tools`, which `test_guard_registry.py` checks). `company_agent/prompts.py`: the Co-founder playbook says to link an
  existing project that already fits the idea (the Founder created it during setup, shown with its repository in the state) to the
  new goal instead of creating a duplicate.
* Tests: `test_company_tools.py` (the new tool, the provider-action description), `stub_agent_api.py` (the review route).

## Milestone 15 additions (company layer only, no upstream file touched)

* `tools/company_tools.py`: `company_request_purchase` (`POST /purchases`) and `company_review_subscription`
  (`POST /subscriptions/{id}/review`), 39 company tools in total with M14. The purchase tool validates before any HTTP call (https
  vendor URL without credentials, `recurring` in none/monthly/yearly, justification of at least 40 characters, positive amount,
  whole-number quantity, ISO currency with `original_amount`), sends `amount_usd` / `original_amount` as decimal strings rounded
  up to the cent (never floats), and defaults `task_id` to the run's task. It refuses any text that holds a Luhn-valid 13-19 digit
  number (card-number shape) without echoing it, in the purchase fields and in a subscription review reason. It commits nothing:
  the control plane decides, rate limits, dedupes and a person completes any checkout; the agent never sees card data.
* `company_agent/spec.py`: the two tool names. `company_agent/prompts.py`: a "BUYING THINGS" paragraph (prefer free tools; request
  purchases with the tool; never ask for, type, store or repeat card numbers; the human completes the checkout; answer renewal
  reviews), shown only to an employee whose `company_tools` subset contains one of the two tools (all non-Co-founder roles when
  the subset is absent). It is part of the stable identity prompt, so the cached prefix stays identical per agent.
* Tests: `test_company_tools.py` (request shape, money handling, validation, card-number refusal, server errors, review path
  quoting, tool gating), `test_policy.py` (purchasing paragraph per role), `test_guard_registry.py` (both tools classified).
* Not verified: nothing here was exercised against the real backend or a live model; the tests use the stub agent API.

## Milestone 18 additions, Part A (company layer only, no upstream file touched)

* `tools/company_tools.py`: `company_ask_founder` (`POST /questions`; 48 company tools at that time): a question only the Founder can
  answer, always with the default the agent will use, `options` (<= 6), `blocking` (default false) and `deadline_hours` (1-24, server
  clamps). A blocking question marks the task "blocked" for this run (the run ends like a block; the control plane parks the task and
  resumes it with the answer in its context); a refused one (`question_limit`, 429) does not. `company_update_task action=block` now
  requires `unblock_condition` (checked before any HTTP call) and `action=retry` takes `new_instructions` ("retry with a new approach",
  once per task on the server).
* `company_agent/spec.py`: the tool name (also in `backend/app/guardrails/tools.json`, action `question.ask`). `company_agent/runner.py`:
  `company_ask_founder` ends the run when it set a disposition (blocking ask). `company_agent/mcp_server.py`: `ask_founder` is in the
  default tool list. `company_agent/prompts.py`: `DECISION_RULES` replace "never ask a human for input" and "anything that spends money,
  deploys or contacts outside people needs company_request_approval first" (guardrails gate those in code): decide what you can undo and
  record it, ask the Founder only for preferences only they know, keep going with the default; the manager playbook no longer says
  "company_request_approval first"; playbooks tell an employee to give `unblock_condition` when blocking.
* Tests: `test_company_tools.py` (ask, blocking, refused, unblock_condition), `test_m6_runtime.py` (the doctrine in every role's prompt),
  `test_mcp_server.py`, `stub_agent_api.py` (`/questions`).
* Not verified: nothing here was exercised against a live model; the tests use the stub agent API and the backend's own tests cover the server.


## Milestone 18 additions, Part B (company layer only, no upstream file touched)

* `company_agent/spec.py`: optional `mode` (`work`, the default, or `verify`). `company_agent/prompts.py`: in `verify` mode the system
  prompt is a short independent-verifier prompt and the first user turn is `task.description` (the control plane's verification
  prompt). `company_agent/runner.py`: in `verify` mode there is no task to complete: the model's final answer is the run's result
  (`run.completed` summary, <= 4000 chars). This is the work graph's self-check; the control plane sends `toolsets: ["todo"]` (an
  in-memory list; the spec needs one) and `company_tools: []`, so the call has no way to write anything.
* No change to `company_complete_task`: the control plane answers `{ok: true, status: "proposed"}` inside a graph run, which is neither an
  error nor a pending approval, so the existing hard-stop on completion applies unchanged.
* Tests: `company_agent/tests/test_verify_mode.py` (spec validation, a real launcher run with no company tools whose answer is the result).
* Not verified: nothing here was exercised against a live model; an empty model answer is replaced by Hermes with a placeholder text,
  which the control plane's verdict parser treats as inconclusive.

## Milestone 21 additions (agent meetings; company layer only, no upstream file touched)

* `tools/company_tools.py`: `company_schedule_meeting` (`POST /meetings`), `company_meeting_say` (`POST /meetings/{id}/say`; success sets the
  task disposition "completed", so the run ends: the turn is over) and `company_cancel_meeting` (51 company tools in total). A refused
  say (wrong turn, validation) does not end the run.
* `company_agent/runner.py`: `company_meeting_say` hard-stops the run like `company_complete_task` (the summary is the message).
  `company_agent/prompts.py`: a `meeting` task renders the control plane's meeting section (`spec.task.meeting`) plus `MEETING_RULES`
  (they replace the role playbook for the turn), its own kickoff and completion nudge, and `cli_brief` returns just the rules for CLIs.
  `company_agent/spec.py`: the three tool names (also in `backend/app/guardrails/tools.json`, actions `meeting.schedule|say|cancel`).
* Verified: stub agent API and scripted model tests only (`test_company_tools.py`, `test_policy.py`, `test_run_loop.py`); never run with a live model.

## Milestone 22 additions (human work and outsourcing; company layer only, no upstream file touched)

* `tools/company_tools.py`: `company_request_human_work` (`POST /human-work`; with `blocks_my_task` (default) a success sets the task
  disposition "blocked", so the run ends and the task waits for the person; a refused request, e.g. `human_work_limit`, does not end the
  run) and `company_draft_outsource_job` (`POST /outsource-jobs`, HR only on the server; it only drafts: a person approves, posts and
  pays). 53 company tools in total. `company_agent/runner.py`: `company_request_human_work` hard-stops the run like
  `company_ask_founder`. `company_agent/spec.py`: both tool names (also in `backend/app/guardrails/tools.json`, actions
  `human_work.request` and `outsource.draft`). `company_agent/prompts.py`: one decision rule (ask a person for what only a person can
  do) and an HR playbook step (draft a freelance job; never post, message or pay).
* Tests: `test_company_tools.py` (both tools, argument checks, refusals do not end the run), `stub_agent_api.py` (`/human-work`,
  `/outsource-jobs`).
* Verified: stub agent API only; never run with a live model or a marketplace.

