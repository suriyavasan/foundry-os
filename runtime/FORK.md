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
| `company_agent/` | The launcher: `run.py` (entrypoint, stdin spec -> JSONL stdout, signals, limits), `spec.py` (run spec validation), `protocol.py` (event writer: seq, single terminal, redaction), `runner.py` (builds `AIAgent`, callbacks -> events, time/cancel/finish handling), `prompts.py` (role prompt), `home.py` (HERMES_HOME, SOUL.md, enforced `config.yaml`, scrubbed environment), `toolpolicy.py` (toolset mapping/removal), `approvals.py` (deny-all approval callback), `context.py` (tool client config). |
| `company_agent/tests/` | 145 pytest tests incl. a deterministic fake OpenAI-compatible model server and a stub agent API. |
| `tools/company_tools.py` | The `company` toolset (10 tools) calling the Company OS agent API. Auto-discovered by Hermes' tool registry. |
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
| 4 | `toolsets.py` | Added static toolsets `company` (the 10 company tools) and `skills_read` (`skills_list`, `skill_view`). | `company`: explicit membership instead of registry-only toolset. `skills_read`: employees get read-only skills (no `skill_manage`, no self-authored skills); the launcher maps spec `skills` -> `skills_read`, `skills_manage` -> `skills`. |
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
* the launcher against a **real local Ollama `qwen2.5:7b`** (tool calling), including real DuckDuckGo
  (`ddgs`) web search; see `README.md` for the result and the Ollama context caveat;
* `web_search` (ddgs), `skills_list`, `todo_list`, `read_file`/`write_file`/`patch`/`search_files` through the
  real registry.

**Not** exercised: docker sandbox (`sandbox.backend == "docker"`: daemon not running; the generated
`terminal` config for it is unverified and the file jail is intentionally off there), Anthropic/OpenRouter
providers (no keys), the real Company OS backend agent API (the backend was still being built; the tools
follow `docs/specs/m2-work-engine.md` section 3 and `backend/app/schemas/agent_api.py`), Windows/Linux.

## Syncing with upstream

1. Check out the new upstream commit next to `vendor/hermes-agent`.
2. Re-apply the 8 changes above (each is small and marked `COMPANY FORK`); diff the dropped list for new
   top-level modules that the kept code now imports.
3. `uv sync --frozen --no-install-project --extra anthropic --extra ddgs`, then
   `python scripts/check_imports.py` and `python -m pytest`.
4. Update the commit hash here and in `company_agent/__init__.py` (`UPSTREAM_COMMIT`).
