#!/usr/bin/env python
"""Manual live smoke: run one task through the real launcher against a real model server.

Starts the stub Company OS agent API (printing every request it receives), builds a run spec for a
local Ollama model, runs ``python -m company_agent.run`` and prints the JSONL events.

    cd runtime
    .venv/bin/python scripts/live_smoke.py --model qwen2.5:7b

Not part of the test suite: it needs a running model server and takes minutes on a 7B model.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from company_agent.tests.stub_agent_api import StubApi  # noqa: E402

DEFAULT_TASK = {
    "title": "Write a 5-bullet brief on why small businesses adopt AI assistants",
    "description": (
        "Write a 5-bullet brief on why small businesses adopt AI assistants. Submit it as an artifact "
        "with company_submit_artifact (inline content) and then call company_complete_task."
    ),
    "acceptance_criteria": "Exactly 5 bullets, each one sentence.",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="qwen2.5:7b")
    ap.add_argument("--base-url", default="http://localhost:11434/v1")
    ap.add_argument("--api-key", default="ollama")
    ap.add_argument("--context-length", type=int, default=32768)
    ap.add_argument("--max-iterations", type=int, default=20)
    ap.add_argument("--max-seconds", type=int, default=600)
    ap.add_argument("--toolsets", default="company,file,web,skills,todo")
    ap.add_argument("--company-tools", default="", help="comma-separated subset of the ten company tools (default: all)")
    ap.add_argument("--keep", action="store_true", help="keep the temp home/workspace and print its path")
    args = ap.parse_args()

    api = StubApi(token="live-smoke-run-token-0123456789")
    api.on_request = lambda r: print(
        f"[stub-api] {r.method} {r.path} auth_ok={r.authorization == 'Bearer ' + api.token} "
        f"body={json.dumps(r.body, ensure_ascii=False)[:500]}", flush=True)
    api.start()
    tmp = Path(tempfile.mkdtemp(prefix="company-agent-smoke-"))
    spec = {
        "protocol": 1, "run_id": api.run_id,
        "company": {"id": "c1", "name": "Acme Robotics", "mission": "Help small businesses work smarter",
                    "strategic_goal": "Grow the pilot customer base", "constraints": ["Be factual; no invented statistics"]},
        "agent": {"id": "a1", "name": "Ada", "role_title": "Research Analyst", "kind": "worker",
                  "responsibilities": "Research topics and write short, factual briefs.", "instructions": ""},
        "task": {"id": api.task_id, "kind": "work", "priority": "normal", "input_refs": [], **DEFAULT_TASK},
        "model": {"provider": "custom", "base_url": args.base_url, "api_key": args.api_key, "model": args.model,
                  "context_length": args.context_length, "is_local": True},
        "toolsets": [t for t in args.toolsets.split(",") if t],
        "limits": {"max_iterations": args.max_iterations, "max_run_seconds": args.max_seconds},
        "agent_api": {"url": api.url, "token": api.token},
        "workspace_dir": str(tmp / "workspace"), "hermes_home": str(tmp / "home"),
        "sandbox": {"backend": "none"},
    }
    if args.company_tools:
        spec["company_tools"] = [t if t.startswith("company_") else f"company_{t}" for t in args.company_tools.split(",") if t]
    started = time.time()
    proc = subprocess.Popen([sys.executable, "-m", "company_agent.run"], cwd=ROOT, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            env={"PATH": os.environ.get("PATH", "")})
    assert proc.stdin and proc.stdout
    proc.stdin.write(json.dumps(spec))
    proc.stdin.close()
    events = []
    for line in proc.stdout:
        ev = json.loads(line)
        events.append(ev)
        payload = json.dumps(ev["payload"], ensure_ascii=False)
        print(f"[event {ev['seq']:>3}] {ev['type']:<15} {payload[:400]}", flush=True)
    err = proc.stderr.read() if proc.stderr else ""
    code = proc.wait()
    wall = time.time() - started
    types: dict[str, int] = {}
    for e in events:
        types[e["type"]] = types.get(e["type"], 0) + 1
    tools = [e["payload"]["name"] for e in events if e["type"] == "tool.started"]
    last = events[-1] if events else {}
    print("\n==== summary ====")
    print("exit code      :", code)
    print("wall time      : %.1fs" % wall)
    print("events         :", len(events), types)
    print("tools called   :", tools)
    print("final event    :", last.get("type"), json.dumps(last.get("payload", {}).get("usage", {})))
    print("api requests   :", [(r.method, r.path.replace('/api/agent/v1', '')) for r in api.requests])
    if err.strip() and code != 0:
        print("\n---- stderr (tail) ----\n" + err[-3000:])
    if args.keep:
        print("kept:", tmp)
    api.stop()
    return code


if __name__ == "__main__":
    sys.exit(main())
