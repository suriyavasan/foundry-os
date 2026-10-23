# runtime/ — Company OS agent runtime (Hermes Agent fork)

A lean fork of [Hermes Agent](https://github.com/NousResearch/hermes-agent) (MIT) turned into an
"AI employee" runtime. The control plane (`backend/`) never imports it: it spawns
`python -m company_agent.run`, writes **one JSON run spec to stdin** and reads **JSONL events from stdout**.

* What was kept/dropped/changed, and what was verified: **[FORK.md](FORK.md)**
* Spec: `../docs/specs/m3-company-agent-runtime.md` (M3a = this directory; M3b = backend integration)
* Our code: `company_agent/` (launcher), `tools/company_tools.py` (the `company` toolset), `scripts/`

## 1. Set up the virtualenv

Needs `uv` (tested 0.6.14) and Python 3.14. The venv is ~180 MB; check free disk first (`df -h .`).

```bash
cd runtime
uv sync --frozen --no-install-project --extra anthropic --extra ddgs --python 3.14
uv pip install --python .venv/bin/python pytest==9.1.1      # test runner only
```

* `--frozen` installs Hermes' exact pins from `uv.lock` (no re-resolution).
* `--no-install-project`: the tree is run from source (`python -m company_agent.run` with `cwd=runtime/`);
  upstream's packaging (`setup.py`, setuptools package list) was dropped with the parts we do not ship.
* Extras: `ddgs` = keyless web search, `anthropic` = native Anthropic provider. No browser, media or
  messaging extras. `.venv/` is git-ignored.

## 2. Import check (run after any prune or upstream sync)

```bash
.venv/bin/python scripts/check_imports.py          # exit 0 = OK
.venv/bin/python scripts/scan_dropped_imports.py ../vendor/hermes-agent   # informational
```

Imports `run_agent`, `model_tools`, `tools.registry`, `tools.company_tools`, the launcher modules, fails on
any tool module that does not import, and resolves tool definitions for the company toolsets (it uses a
throw-away `HERMES_HOME`; it never touches `~/.hermes`).

## 3. Tests

```bash
.venv/bin/python -m pytest              # ~3 minutes; launcher tests start real subprocesses
.venv/bin/python -m pytest company_agent/tests/test_file_jail.py -q    # fast, in-process
```

`company_agent/tests/`: spec validation + exit code 3, protocol writer (seq, single terminal, redaction), company
tools against a stub HTTP API (paths, bearer token, error mapping, secrecy), file jail through the real
file tools, toolset/config/env/approval policy, context-floor patch, and `test_run_loop.py` which runs the real
launcher against a deterministic fake OpenAI-compatible model (`fake_openai.py`) and a stub agent API
(`stub_agent_api.py`): the full progress -> artifact -> complete loop, errors, reminders, iteration cap, hard
time limit, SIGTERM/SIGINT, workspace jail, secret redaction. No network and no real model needed.

## 4. Run a spec by hand

Terminal 1 — a stub of the agent API that prints every request it gets:

```bash
.venv/bin/python -m company_agent.tests.stub_agent_api --port 8765 --token my-run-token-123456
```

Terminal 2 — a spec (secrets go **in the spec**, never in argv/env) piped to the launcher:

```bash
mkdir -p /tmp/ca && cat > /tmp/ca/spec.json <<'EOF'
{
  "protocol": 1,
  "run_id": "run-1",
  "company": {"id": "c1", "name": "Acme", "mission": "Help small businesses", "strategic_goal": "Pilot", "constraints": []},
  "agent":   {"id": "a1", "name": "Ada", "role_title": "Research Analyst", "kind": "worker",
              "responsibilities": "Write short factual briefs", "instructions": ""},
  "task":    {"id": "task-1", "title": "Write a 5-bullet brief on why small businesses adopt AI assistants",
              "description": "Submit it with company_submit_artifact (inline content), then call company_complete_task.",
              "acceptance_criteria": "Exactly 5 bullets", "kind": "work", "priority": "normal", "input_refs": []},
  "model":   {"provider": "custom", "base_url": "http://127.0.0.1:11435/v1", "api_key": "ollama",
              "model": "qwen2.5:7b", "context_length": 32768, "is_local": true},
  "toolsets": ["company", "file", "web", "skills", "todo"],
  "limits":  {"max_iterations": 20, "max_run_seconds": 600},
  "agent_api": {"url": "http://127.0.0.1:8765/api/agent/v1", "token": "my-run-token-123456"},
  "workspace_dir": "/tmp/ca/workspace",
  "hermes_home": "/tmp/ca/home",
  "sandbox": {"backend": "none"}
}
EOF
.venv/bin/python -m company_agent.run < /tmp/ca/spec.json     # events on stdout, logs on stderr
echo "exit code: $?"
.venv/bin/python -m company_agent.run --version               # health probe, needs no stdin
```

`scripts/live_smoke.py` does all of this in one command (starts the stub API, builds the spec, runs the
launcher, prints events and a summary): `.venv/bin/python scripts/live_smoke.py --model qwen2.5:7b --base-url http://127.0.0.1:11435/v1`.

### Ollama needs a bigger context than its default (important)

Ollama's OpenAI-compatible endpoint **ignores per-request `num_ctx`**; it serves its own default (4096 here).
The company prompt + 18 tool schemas alone are ~4–5K tokens, so a default Ollama silently truncates the
*front* of every prompt (system prompt and tools vanish) and the model behaves erratically. Start the server
with the window you configure in the spec:

```bash
OLLAMA_CONTEXT_LENGTH=32768 ollama serve            # or a second instance: OLLAMA_HOST=127.0.0.1:11435 ...
curl -s localhost:11435/api/ps                      # "context_length": 32768 once the model is loaded
```

The launcher probes `/api/ps` after the first model call and emits a `progress` event starting with `WARNING:`
when the served window is smaller than `model.context_length`.

## 5. Contract

### Run spec (stdin; one JSON document)

Required: `protocol` (=1), `run_id`, `company{id,name,mission?,strategic_goal?,constraints[]}`,
`agent{id,name,role_title,kind?,responsibilities?,instructions?,manager?{id,name,role_title},direct_reports?[]}`,
`task{id,title,description?,acceptance_criteria?,kind?,priority?,input_refs[]}`,
`model{provider: custom|openrouter|anthropic, base_url, api_key, model, context_length>=8192, is_local}`,
`toolsets[]`, `limits{max_iterations 1-500, max_run_seconds 1-86400}`, `agent_api{url,token}`,
absolute `workspace_dir` and `hermes_home` (must not nest), `sandbox{backend: none|docker, docker_image?, network?}`.
Optional: `company_tools[]` — restrict the company toolset to a subset of the ten tools (small models do
noticeably better with fewer tools; recommended worker subset: `get_context, report_progress,
submit_artifact, complete_task, update_task, create_task`).

Allowed toolsets: `company file web search skills skills_manage todo vision terminal code_execution`.
`terminal`/`code_execution` are removed (with a `progress` warning) unless `sandbox.backend == "docker"`;
`web` runs as `search` (keyless ddgs cannot extract pages); `skills` is read-only (`skills_manage` grants
`skill_manage`, for the HR/skill-author role in M4).

### Events (stdout, JSONL)

`{"seq": n, "type": ..., "ts": "...Z", "payload": {...}}`, `seq` = 1,2,3... Types: `run.started`, `message`
(`{text, final}`), `reasoning`, `tool.started` (`{tool_call_id, name, args_preview}`), `tool.completed`
(`{tool_call_id, name, duration_ms, is_error, result_preview}`), `progress` (launcher notices only: warnings,
reminders, denied actions — `company_report_progress` is visible as a tool call and is recorded by the server),
`usage` (cumulative), `artifact` (`{artifact_id, title}`), and exactly one terminal event:

| terminal | exit code | meaning |
|---|---|---|
| `run.completed` | 0 | the agent completed (or blocked) **its task through the API**; payload has `task_disposition: completed\|blocked`, `summary` = the `result_summary` it gave the API |
| `run.failed` | 1 | model/transport error (`model_error`), `task_not_completed` (stopped without completing even after 2 reminders), `setup_failed`, `bad_spec` (3), ... |
| `run.failed` | 2 | **partial**: `exit_reason` `max_iterations` or `time_limit`, with `partial: true` |
| `run.cancelled` | 130 | SIGTERM/SIGINT; `exit_reason: cancelled` |
| `run.failed` | 3 | `bad_spec`: nothing ran; the event lists every problem (no secrets echoed) |

Terminal payload: `{summary, exit_reason, error?, partial?, task_disposition?, duration_ms, usage{input_tokens,
output_tokens, cache_read_tokens, cache_write_tokens, reasoning_tokens, api_calls, estimated_cost_usd,
cost_status, cost_source, model, provider}}`. Previews are redacted and <= 2000 chars; the run token and
model key never appear in any event or log line.

### For the process that spawns the launcher (M3b)

`cwd` = `runtime/` (so `company_agent`, `tools`, `agent` import), interpreter = `runtime/.venv/bin/python`,
write the spec to stdin (EOF optional), pass a minimal environment (the launcher rebuilds its own anyway),
read stdout line by line, **SIGTERM to cancel** (SIGKILL after ~10 s). The launcher exits with `os._exit` right
after the terminal event. The run ends as soon as the agent's `company_complete_task` (or a successful
block of its task) returns — the launcher stops the model loop itself.

## 6. Live smoke results (qwen2.5:7b via real Ollama, served with a 32 768 window, 2026-10-24)

Task: "Write a 5-bullet brief on why small businesses adopt AI assistants; submit it with
`company_submit_artifact` (inline content), then call `company_complete_task`", default toolsets
(`company file web skills todo`, 18 effective tools), stub agent API printing requests.

* 7 runs with the final prompt/toolset design: all exited 0 with `run.completed`, an `artifact` event and a
  `POST /artifacts` + `POST /tasks/task-1/complete` against the stub API; 27-85 s wall time, 3-9 model calls,
  ~4-5K uncached input tokens + 25-40K cache-read tokens, 0.5-1.2K output tokens, cost `unknown` (local).
* The model is not reliable on its own: it passed `result_summary` to `company_submit_artifact`, omitted `title`,
  invented artifact-id argument names or stopped with prose mid-flow in most runs. What fixed those in
  practice (all in the launcher/tool layer, never faked): actionable tool errors, the one-time
  `no_deliverable` challenge on `company_complete_task`, alias/stringified `output_artifact_ids` parsing with
  the run's own artifact ids as default, inline-`content`-wins on a bogus `path`, up to two launcher reminders
  when the model stops without completing, a lean ~2.3K-char system prompt, `tool_search` off, `web_extract`
  and `skill_manage` removed.
* With Ollama at its **default 4096-token window** (the first attempts) the same model flailed (16 repeated
  searches, hallucinated completion): the front of the prompt was silently truncated. See the Ollama note in
  section 4.
* Not verified: any model other than qwen2.5:7b, docker sandbox, the real backend.

Reproduce: `.venv/bin/python scripts/live_smoke.py --model qwen2.5:7b --base-url http://127.0.0.1:11435/v1`.
