"""Stand-in for ``python -m company_agent.run`` used by the adapter tests.

Copied to ``<fake runtime>/company_agent/run.py`` and run with the backend's own interpreter. It
reads ONE JSON spec from stdin (like the real launcher) and emits scripted JSONL events chosen by
the task title: ``scenario:<name>``. It honours SIGTERM unless the scenario says otherwise.
"""

import json
import os
import signal
import sys
import time

PROTOCOL = 1


def emit(seq, type_, payload=None, raw=None):
    line = (
        raw
        if raw is not None
        else json.dumps(
            {"seq": seq, "type": type_, "ts": "2026-10-24T10:00:00.000Z", "payload": payload or {}}
        )
    )
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def usage(**kw):
    base = {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 400,
        "cache_write_tokens": 0,
        "reasoning_tokens": 0,
        "api_calls": 2,
        "estimated_cost_usd": None,
        "cost_status": "unknown",
        "cost_source": "none",
        "model": "m",
        "provider": "custom",
    }
    base.update(kw)
    return base


def read_spec():
    buf = b""
    decoder = json.JSONDecoder()
    while True:
        chunk = os.read(0, 65536)
        if not chunk:
            break
        buf += chunk
        try:
            obj, _ = decoder.raw_decode(buf.decode("utf-8"))
            return obj
        except ValueError:
            continue
    return json.loads(buf.decode("utf-8"))


def main():
    if "--version" in sys.argv:
        print(
            json.dumps(
                {"company_agent": "fake", "protocol": PROTOCOL, "upstream_hermes_commit": "x"}
            )
        )
        return 0
    spec = read_spec()
    title = spec["task"]["title"]
    scenario = title.split("scenario:", 1)[1].split()[0] if "scenario:" in title else "success"
    secret_key = spec["model"]["api_key"]
    token = spec["agent_api"]["token"]

    emit(1, "run.started", {"run_id": spec["run_id"], "toolsets": spec["toolsets"]})

    if scenario == "success":
        emit(2, "message", {"text": "working", "final": False})
        emit(3, "tool.started", {"tool_call_id": "t1", "name": "company_report_progress"})
        emit(
            4,
            "tool.completed",
            {"tool_call_id": "t1", "name": "company_report_progress", "is_error": False},
        )
        emit(5, "usage", usage())
        emit(
            6,
            "run.completed",
            {"summary": "all done", "exit_reason": "completed", "usage": usage()},
        )
        return 0
    if (
        scenario == "leak"
    ):  # a runtime that (wrongly) echoes secrets: the control plane must scrub them
        emit(2, "message", {"text": f"my key is {secret_key} and token {token}"})
        emit(3, "run.completed", {"summary": f"done with {token}", "usage": usage()})
        return 0
    if scenario == "failure":
        emit(
            2,
            "run.failed",
            {
                "summary": "",
                "exit_reason": "model_error",
                "error": f"boom {secret_key}",
                "usage": usage(),
            },
        )
        return 1
    if scenario == "not_completed":
        emit(
            2,
            "run.failed",
            {
                "summary": "",
                "exit_reason": "task_not_completed",
                "error": "stopped early",
                "usage": usage(),
            },
        )
        return 1
    if scenario == "partial":
        emit(
            2,
            "run.failed",
            {
                "summary": "ran out of steps",
                "exit_reason": "max_iterations",
                "partial": True,
                "usage": usage(),
            },
        )
        return 2
    if scenario == "time_limit":
        emit(
            2,
            "run.failed",
            {
                "summary": "",
                "exit_reason": "time_limit",
                "partial": True,
                "error": "too slow",
                "usage": usage(),
            },
        )
        return 2
    if scenario in ("cancel", "slow"):
        stop = {"flag": False}

        def on_term(signum, frame):
            stop["flag"] = True

        signal.signal(signal.SIGTERM, on_term)
        emit(2, "progress", {"message": "started working"})
        deadline = time.time() + 60
        while not stop["flag"] and time.time() < deadline:
            time.sleep(0.05)
        emit(
            3,
            "run.cancelled",
            {"summary": "Run cancelled.", "exit_reason": "cancelled", "usage": usage()},
        )
        return 130
    if scenario == "ignore_term":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        emit(2, "progress", {"message": "ignoring SIGTERM"})
        time.sleep(60)
        return 0
    if scenario == "child":  # leaves a grandchild behind that must die with the group
        import subprocess

        child = subprocess.Popen(["sleep", "60"])
        with open(spec["workspace_dir"] + "/child.pid", "w") as fh:
            fh.write(str(child.pid))
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        emit(2, "progress", {"message": "child started"})
        time.sleep(60)
        return 0
    if scenario == "malformed":
        emit(0, None, raw="this is not json")
        emit(0, None, raw="[1, 2, 3]")
        emit(0, None, raw='{"type": "message", "payload": {}}')  # no seq
        emit(0, None, raw='{"seq": "7", "type": "message", "payload": {}}')  # seq not int
        emit(0, None, raw='{"seq": true, "type": "message", "payload": {}}')  # bool seq
        emit(0, None, raw='{"seq": 0, "type": "message", "payload": {}}')  # seq 0
        emit(0, None, raw='{"seq": -4, "type": "message", "payload": {}}')
        emit(0, None, raw='{"seq": 2, "type": "rm_rf", "payload": {}}')  # unknown type
        emit(0, None, raw="x" * 1_100_000)  # oversized line
        emit(2, "message", {"text": "survivor"})
        emit(0, None, raw='{"seq": 3, "type": "message", "payload": "not a dict"}')
        emit(4, "run.completed", {"summary": "survived garbage", "usage": usage()})
        emit(5, "message", {"text": "after terminal"})  # dropped
        return 0
    if scenario == "collide":
        emit(2, "message", {"text": "second"})
        emit(2, "message", {"text": "REPLAY of second"})  # duplicate seq: first wins
        emit(1, "message", {"text": "REPLAY of first"})  # going backwards
        emit(1_000_000, "progress", {"message": "collides with server progress seq"})
        emit(7_777_777, "progress", {"message": "far in the server range"})
        emit(3, "message", {"text": "third"})
        emit(4, "run.completed", {"summary": "ok", "usage": usage()})
        return 0
    if scenario == "no_terminal":
        emit(2, "message", {"text": "dying"})
        sys.stderr.write(f"Traceback: something broke with key {secret_key}\n")
        return 7
    if scenario == "inspect":  # reports what the process was given
        emit(
            2,
            "progress",
            {
                "message": "inspect",
                "env_keys": sorted(os.environ),
                "argv": sys.argv,
                "cwd": os.getcwd(),
                "spec_keys": sorted(spec),
                "toolsets": spec["toolsets"],
                "company_tools": spec.get("company_tools"),
                "guardrails": spec.get("guardrails"),
                "sandbox": spec["sandbox"],
                "model": {k: v for k, v in spec["model"].items() if k != "api_key"},
                "has_api_key": bool(secret_key),
                "agent": spec["agent"],
                "company": {k: v for k, v in spec["company"].items()},
                "task": spec["task"],
                "limits": spec["limits"],
                "hermes_home": spec["hermes_home"],
                "workspace_dir": spec["workspace_dir"],
                "workspace": spec.get("workspace"),
                "skills": sorted(os.listdir(os.path.join(spec["hermes_home"], "skills")))
                if os.path.isdir(os.path.join(spec["hermes_home"], "skills"))
                else [],
                "home_env": os.environ.get("HOME"),
                "hermes_home_env": os.environ.get("HERMES_HOME"),
            },
        )
        emit(3, "run.completed", {"summary": "inspected", "usage": usage()})
        return 0
    if scenario == "cloud_actual":
        emit(
            2,
            "run.completed",
            {"summary": "paid", "usage": usage(estimated_cost_usd=0.0123, cost_status="actual")},
        )
        return 0
    if scenario == "cloud_estimated":
        emit(
            2,
            "run.completed",
            {"summary": "paid", "usage": usage(estimated_cost_usd=0.5, cost_status="estimated")},
        )
        return 0
    if scenario == "cloud_unknown":
        emit(2, "run.completed", {"summary": "unpriced", "usage": usage()})
        return 0
    emit(
        2,
        "run.failed",
        {"summary": "", "exit_reason": "bad_spec", "error": f"unknown scenario {scenario}"},
    )
    return 3


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
