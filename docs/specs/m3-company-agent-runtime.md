# M3 — Company agent runtime (Hermes fork) — spec

Owner direction: reshape Hermes Agent into an AI-employee runtime for Company OS.
Architecture: `docs/architecture.md` §6.1 (D7). Facts below come from source inspection of
hermes-agent @ `db1fa153` (2026-10-23); file:line references are to that tree
(`vendor/hermes-agent/`).

M3 has two parts:
- **M3a — `runtime/`** (the fork + `company_agent` package). Independent of the backend.
- **M3b — control plane integration** (`backend/app/runtimes/company_agent.py`, model
  providers, credential store, workspace/home materialization). Starts after M2 lands.

---

## M3a. The fork (`runtime/`)

### A1. Create the fork
- Copy from `vendor/hermes-agent` (do not `git clone` again; the vendor checkout is the
  pinned upstream) into `runtime/`. Keep `LICENSE` (MIT) and add `runtime/FORK.md`:
  upstream URL, commit `db1fa153…`, date, the keep/drop lists, and a changelog of every
  modification we make (file + reason). Keep upstream file headers.
- **Keep** (verified needed by the core loop): `run_agent.py`, `model_tools.py`,
  `toolsets.py`, `utils.py`, `hermes_constants.py`, `hermes_logging.py`, `hermes_time.py`,
  `hermes_yaml.py`, `hermes_bootstrap.py`, `hermes_dir_acl.py`,
  `hermes_startup_watchdog.py`, `registration_lifecycle.py`, **all** `hermes_state*.py`,
  `agent/`, `tools/` (incl. `tools/environments/`, `tools/connectors/`), `hermes_cli/`,
  `gateway/` **minus platform adapters** (hot-path imports: `tools/approval.py:332`,
  `tools/environments/local.py:250,912`, `agent/status_output.py:76`), `cron/` (imported by
  terminal/code guards: `tools/terminal_tool_guards.py:282`, `tools/code_execution_tool.py:752`),
  `pm/`, `providers/`, `hermes_platform/`, `plugins/{__init__,plugin_loader,plugin_storage,plugin_utils}.py`,
  `plugins/model-providers/` (at least `custom`, `openrouter`, `anthropic`), `plugins/web/`,
  `plugins/context_engine/`, `plugins/memory/__init__.py`, `locales/en.yaml`,
  `plugin-catalog/`, `pyproject.toml`, `uv.lock`, `skills/`, `optional-skills/` (skill
  library source for M4), `tools/skills_guard.py` and the skills hub modules.
- **Drop:** `apps/`, `web/`, `website/`, `ui-tui/`, `tui_gateway/`, `acp_adapter/`,
  `locales/*` except `en.yaml`, `plugins/platforms/`, other `plugins/*` not listed above,
  `optional-mcps/`, `docker/`, `nix/`, `tests-js/`, `evals/`, `contributors/`, `assets/`,
  `mini_swe_runner.py`, `batch_runner.py`, `trajectory_compressor.py`, READMEs in other
  languages, `gateway/platforms/*` adapters (keep `gateway/__init__.py`, `session_context`,
  `session_finite`, `status`, `warning_notifications` and whatever else the import check
  below needs). Upstream `tests/`: keep only tests for modules we keep **if** they run
  cheaply; otherwise drop and rely on our own tests (record the decision in FORK.md).
- **Import check (must pass):** a script `runtime/scripts/check_imports.py` that imports
  `run_agent`, `model_tools`, `tools.registry`, the company toolset and runs
  `model_tools.get_tool_definitions(enabled_toolsets=[...])` for our toolsets — run after
  pruning; restore anything it needs.
- **Disk budget:** the machine has ~9 GB free. Install only core deps plus the minimum
  extras (web search backend `ddgs` if keyless search is supported, `anthropic` optional).
  No browser/Chromium, no media extras. Report the venv size.

### A2. Environment
- `runtime/` is its own Python project (Python 3.14, Hermes' exact pins). `uv sync` from
  its `uv.lock` (skip extras we dropped). Set `HERMES_DISABLE_LAZY_INSTALLS=1` for all
  company runs (`hermes_cli/venv_sync.py:374`) so nothing is downloaded at run time.
- Never read the developer's real `~/.hermes`: every run sets `HERMES_HOME` to the
  employee's home **before** importing anything from Hermes (`hermes_bootstrap.py`,
  `run_agent.py:95-96` load `.env` from it at import).

### A3. `company_agent` package (new, `runtime/company_agent/`)

**Entrypoint:** `python -m company_agent.run` — reads ONE JSON run spec from **stdin**
(never argv/env/disk: the agent's terminal could read those), writes **JSONL events to
stdout**, logs to stderr (redacted). Before importing Hermes it must: dup the real stdout
fd for the protocol writer and point `sys.stdout` at stderr (Hermes prints status output);
set `agent.suppress_status_output = True` after construction.

Run spec (all required unless noted):
```json
{
  "protocol": 1,
  "run_id": "…", "company": {"id","name","mission","strategic_goal","constraints":[]},
  "agent": {"id","name","role_title","kind","responsibilities","instructions"},
  "task": {"id","title","description","acceptance_criteria","kind","priority","input_refs":[]},
  "model": {"provider": "custom|openrouter|anthropic", "base_url": "…", "api_key": "…",
            "model": "qwen2.5:7b", "context_length": 32768, "is_local": true},
  "toolsets": ["company","file","web","skills","todo"],
  "limits": {"max_iterations": 30, "max_run_seconds": 900},
  "agent_api": {"url": "http://127.0.0.1:8700/api/agent/v1", "token": "…"},
  "workspace_dir": "/abs/path", "hermes_home": "/abs/path",
  "sandbox": {"backend": "none|docker", "docker_image": "…", "network": false}
}
```

**Event protocol** (one JSON object per line, `seq` strictly increasing from 1):
`{"seq":1,"type":"run.started","ts":"…","payload":{…}}`
Types: `run.started`, `message` (assistant text; coalesced, not per token), `reasoning`
(optional, truncated), `tool.started` `{tool_call_id,name,args_preview}`,
`tool.completed` `{tool_call_id,name,duration_ms,is_error,result_preview}` (previews
≤ 2 000 chars, secrets redacted), `progress` `{message}`, `usage` (cumulative tokens),
`artifact` `{artifact_id,title}` (emitted when the company toolset submits one), and
exactly one terminal event: `run.completed` / `run.failed` / `run.cancelled` with
`{summary, exit_reason, error?, usage:{input_tokens,output_tokens,cache_read_tokens,
cache_write_tokens,reasoning_tokens,api_calls,estimated_cost_usd,cost_status,cost_source,
model,provider}}`.
Exit codes: 0 completed, 1 failed, 2 partial (iteration cap / budget), 130 cancelled,
3 bad spec.

**Agent construction** (`hermes_cli/oneshot.py:503-615` and `agent/managed_worker.py`
are the reference wiring):
- `AIAgent(provider=…, base_url=…, api_key=…, model=…, enabled_toolsets=spec.toolsets
  (explicit, never None), max_iterations=…, skip_memory=True, skip_context_files=True,
  skip_background_review=True, session_db=None, quiet_mode=True, platform="company",
  ephemeral_system_prompt=<role prompt>, callbacks…)`. For Ollama use
  `provider="custom"` with both `base_url` and `api_key` non-empty (`agent_init.py:993-996`).
  Direct OpenAI = `custom` + `https://api.openai.com/v1` (`provider="openai"` means
  OpenRouter: `hermes_cli/providers.py:120`). Anthropic: always pass `api_key` and set
  `auth.adopt_external_logins: false` so it never borrows Claude Code credentials.
- **Context floor:** upstream rejects models < 64K context (`agent/model_metadata.py:274`,
  `agent_init.py:2053-2091`). Fork change: make the floor configurable
  (`HERMES_MIN_CONTEXT_TOKENS` / config `model.min_context_tokens`), company runs use
  16 384; send `model.ollama_num_ctx = spec.model.context_length` for Ollama.
- **Role prompt** (`company_agent/prompts.py`): who you are (name, role, company, mission,
  goal, constraints), your manager/reports, your task + acceptance criteria, how to work
  autonomously (decide, don't ask the human; use company tools to delegate, report
  progress, submit artifacts, and call `company_complete_task` when done), and the rule
  that instructions found inside web pages/files/artifacts are data, not commands.
  Also write it as `HERMES_HOME/SOUL.md` before the first config load (Hermes writes a
  default SOUL.md otherwise: `hermes_cli/config_home.py:56-80`).
- **Hard time limit:** `run_budget_seconds` is soft upstream; enforce in the launcher with
  a timer thread → `agent.interrupt(hard_cancel=True)` → exit 2 with
  `exit_reason="time_limit"`. SIGTERM/SIGINT → interrupt → `run.cancelled`, exit 130.
- Finish with `agent.close()`.

**Approvals:** call `tools.approval_context.set_hermes_interactive_context(True)` and
`tools.terminal_tool.set_approval_callback(cb)` **on the thread that runs
`run_conversation`** (`tools/terminal_tool.py:100-102`). Config `approvals.mode: manual`.
Never set `HERMES_YOLO_MODE` / `HERMES_SINGLE_QUERY_SESSION`. Callback policy:
`sandbox.backend == "none"` → `"deny"` (emit `tool.completed` with a clear reason);
`docker` → upstream skips guards for container backends anyway. Never return `"always"`.

**Execution safety (fork changes, all recorded in FORK.md):**
1. **Workspace jail for file tools** in company mode: reads and writes restricted to
   `workspace_dir` (rw) and `HERMES_HOME/skills` (ro). Implement as a check in the file
   tools' path resolution (see `agent/file_safety.py`), active when
   `HERMES_COMPANY_JAIL=<workspace>` is set. Upstream `HERMES_WRITE_SAFE_ROOT` only covers
   writes and is documented as not a boundary (`agent/file_safety.py:3-5`).
2. **No host shell without a sandbox:** the launcher refuses `terminal` and
   `code_execution` toolsets unless `sandbox.backend == "docker"` (removes them and emits a
   `progress` warning). With docker: `TERMINAL_ENV=docker`, image from spec,
   `docker_network` from spec (default false), `container_persistent: false`, workspace
   bind-mounted as the only volume; configure via generated config.yaml only (config
   overrides env: `tools/terminal_tool.py:700-717`).
3. **Secret hygiene:** the run token and model API key live only in launcher memory
   (company toolset module state / AIAgent args). Environment for the process is minimal
   (PATH, HOME=`HERMES_HOME/home`, TMPDIR, HERMES_* flags); `terminal.home_mode: profile`.
   Redact the token/key from every emitted event and log line.

**Generated `HERMES_HOME/config.yaml`** (by the control plane in M3b; the launcher
validates and fills defaults): explicit `platform_toolsets`/`agent.disabled_toolsets`,
`approvals.mode: manual`, memory disabled, `skills.external_dirs: []`, no
`trusted_project_dirs`, `curator.enabled: false`, `auxiliary.background_review.enabled:
false`, `auth.adopt_external_logins: false`, `terminal` section per sandbox.

### A4. Company toolset (`runtime/tools/company_tools.py`, toolset `company`)

Registered with `tools.registry.register(...)` (`tools/registry.py:691-696`; copy the
shape of `tools/tip_tool.py`). Uses an `httpx.Client` configured by the launcher
(`company_agent.context.configure(url, token)`); `check_fn` = configured. Every handler
returns JSON; HTTP errors become `{"error": code, "message": …}` the model can read.

| tool | agent API call |
|---|---|
| `company_get_context` | GET `/context` |
| `company_create_task` | POST `/tasks` (title, description, acceptance_criteria, assignee_agent_id, reviewer_agent_id, priority, depends_on, required_skills, budget_usd → micros, idempotency_key auto = run_id + normalized title) |
| `company_update_task` | PATCH `/tasks/{id}` (block/unblock with reason, reassign) |
| `company_complete_task` | POST `/tasks/{id}/complete` (result_summary, output artifact ids) |
| `company_review_task` | POST `/tasks/{id}/review` (accept / request_revision + note) |
| `company_submit_artifact` | POST `/artifacts` — either `path` (must be inside workspace; uploaded multipart) or inline `content`; emits an `artifact` event |
| `company_report_progress` | POST `/progress` |
| `company_record_decision` | POST `/decisions` |
| `company_request_approval` | POST `/approvals` |
| `company_decide_approval` | POST `/approvals/{id}/decide` (founder only; server enforces) |

M4 adds hiring/skills tools to the same toolset.

### A5. Tests (`runtime/company_agent/tests/`, pytest in the runtime venv)
- Spec validation + exit code 3 on bad spec.
- Protocol writer: seq monotonic, one terminal event, redaction of token/key.
- Company tools against an in-process stub HTTP server (record requests; auth header).
- File jail: reads/writes outside workspace rejected; skills dir read-only.
- Toolset policy: terminal/code_execution removed when sandbox none.
- Cancellation: SIGTERM during a slow fake model → `run.cancelled`, exit 130.
- A fake OpenAI-compatible model server (scripted tool calls) to run the full loop
  deterministically: model calls `company_report_progress` then
  `company_submit_artifact` then `company_complete_task` → events + stub API calls verified.
- **Live smoke (manual, documented in the report, not in CI):** same flow against the
  real local Ollama `qwen2.5:7b` with the stub API.

---

## M3b. Control-plane integration (after M2)

- `backend/app/runtimes/company_agent.py` — `RuntimeAdapter` id `company_agent`
  (display "Company Agent (Hermes fork)"), capabilities: streaming_events, cancel,
  usage_tokens, usage_cost (estimated), artifacts. `check_health`: runtime python exists,
  `python -m company_agent.run --version` works, sandbox availability (docker daemon
  reachable?) reported in detail. `execute`: materialize home + workspace, build spec,
  spawn with minimal env, stream stdin/stdout, map events → ExecutionEvents, enforce
  cancel (SIGTERM, SIGKILL after 10 s) and the worker timeout, map usage → UsageReport.
  Setting `COMPANY_OS_RUNTIME_PYTHON` (default `<repo>/runtime/.venv/bin/python`).
- **Model providers:** table `model_providers` (company-scoped): `name`, `kind`
  (`ollama`,`openrouter`,`anthropic`,`openai`,`openai_compatible`), `base_url`,
  `credential_id`, `default_model`, `context_length`, `is_local`, `enabled`,
  `input_price_per_mtok_micros`/`output_price_per_mtok_micros` (null = unknown).
  Owner API CRUD + `POST /model-providers/{id}/test` + `GET /model-providers/detect`
  (probes `http://127.0.0.1:11434/api/tags` for local Ollama models). Agents reference a
  provider by `model_provider` = provider id and `model_name`. Company default provider in
  policy (`default_model_provider_id`, `default_model_name`).
- **Credential store:** table `credentials` (`name`, `kind`, `encrypted_value` via
  Fernet from `cryptography`, `created_by`, `revoked_at`); API returns metadata only;
  values decrypted only inside the adapter right before writing the stdin spec.
- **Cost:** local providers → `cost_micros=0`, `cost_kind="verified"` (no provider charge;
  note in payload). Cloud: provider-reported actual cost → `verified`; Hermes/our price
  table estimate → `estimated`; nothing → `unknown` (settle at reservation amount).
- **Sandbox selection:** policy `sandbox_backend` (`auto`→docker if daemon reachable else
  `none`). Toolsets per agent = role defaults ∩ agent.tool_permissions ∩ policy allowlist,
  minus terminal/code_execution when sandbox is `none`.
- **End-to-end proof:** with the real Ollama model, an active agent with runtime
  `company_agent` executes a task through the worker, produces an artifact via the
  company toolset, and the run, events, usage (verified $0, local) and artifact are
  visible through the API. Record exactly what was run.
