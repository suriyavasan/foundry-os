"""Stub Company OS agent API (``/api/agent/v1``) for tests and manual smoke runs.

Records every request (method, path, auth header, parsed body) and answers with canned JSON in the
shape of the real backend (backend/app/schemas/agent_api.py). Not a model of server behaviour -
the real rules live in the backend; this only lets the runtime be exercised on its own.

Manual use (prints each request as it arrives)::

    .venv/bin/python -m company_agent.tests.stub_agent_api --port 8765 --token secret-run-token
"""

from __future__ import annotations

import argparse
import json
import threading
from dataclasses import dataclass, field
from email import message_from_bytes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Optional

PREFIX = "/api/agent/v1"


@dataclass
class Recorded:
    method: str
    path: str
    authorization: Optional[str]
    content_type: str
    body: Any  # dict for JSON, {"fields": {...}, "files": {...}} for multipart, str otherwise


@dataclass
class StubApi:
    token: str = "run-token-abcdef123456"
    run_id: str = "run-1"
    task_id: str = "task-1"
    requests: list[Recorded] = field(default_factory=list)
    # path-prefix -> (status, body) forced for the next matching request(s)
    forced: dict[str, tuple[int, Any]] = field(default_factory=dict)
    # What a task body's `error` field holds: None, or {"code", "message", "run_id"} after a failed attempt.
    task_error: Any = None
    on_request: Optional[Callable[[Recorded], None]] = None
    _server: Optional[ThreadingHTTPServer] = None
    _thread: Optional[threading.Thread] = None
    _counter: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # -- lifecycle ----------------------------------------------------------------
    def start(self, port: int = 0) -> "StubApi":
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:  # silence
                pass

            def _handle(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length) if length else b""
                ctype = self.headers.get("content-type", "")
                rec = Recorded(self.command, self.path, self.headers.get("authorization"), ctype,
                               _parse_body(ctype, raw))
                with outer._lock:
                    outer.requests.append(rec)
                if outer.on_request:
                    outer.on_request(rec)
                status, body = outer._respond(rec)
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = _handle

        self._server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()

    @property
    def url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}{PREFIX}"

    # -- helpers for assertions -----------------------------------------------------
    def calls(self, method: str, path_prefix: str) -> list[Recorded]:
        full = PREFIX + path_prefix
        return [r for r in self.requests if r.method == method and r.path.split("?")[0].startswith(full)]

    def _next_id(self, prefix: str) -> str:
        with self._lock:
            self._counter += 1
            return f"{prefix}-{self._counter}"

    # -- canned responses -------------------------------------------------------------
    def _respond(self, rec: Recorded) -> tuple[int, Any]:
        path = rec.path.split("?")[0]
        if not path.startswith(PREFIX):
            return 404, _err("not_found", "Not found")
        if rec.authorization != f"Bearer {self.token}":
            return 401, _err("unauthenticated", "Authentication required")
        sub = path[len(PREFIX):]
        for prefix, forced in list(self.forced.items()):
            if sub.startswith(prefix):
                return forced
        m, b = rec.method, rec.body if isinstance(rec.body, dict) else {}
        if m == "GET" and sub == "/context":
            return 200, {
                "company": {"id": "c1", "name": "Acme", "mission": "m", "strategic_goal": "g",
                            "constraints": [], "timezone": "UTC"},
                "run": {"id": self.run_id, "attempt_number": 1},
                "task": {"id": self.task_id, "title": "Test task", "status": "in_progress"},
                "agent": {"id": "a1", "name": "Ada", "role_title": "Analyst", "kind": "worker"},
                "manager": None, "direct_reports": [], "colleagues": [],
                "policy": {"autonomy_mode": "autonomous", "max_delegation_depth": 3},
            }
        if m == "POST" and sub == "/tasks":
            return 201, {"id": self._next_id("task"), "title": b.get("title"), "status": "backlog"}
        # Real Task bodies carry an `error` field (null, or the last failed attempt's object): the stub must too.
        if m == "PATCH" and sub.startswith("/tasks/"):
            return 200, {"id": sub.split("/")[2], "status": "blocked" if b.get("status") == "blocked" else "ready",
                         "error": self.task_error}
        if m == "POST" and sub.endswith("/complete"):
            return 200, {"id": sub.split("/")[2], "status": "done", "result_summary": b.get("result_summary"),
                         "error": self.task_error}
        if m == "POST" and sub.endswith("/review"):
            return 200, {"id": sub.split("/")[2], "status": "done" if b.get("decision") == "accept" else "revision_requested"}
        if m == "POST" and sub == "/artifacts":
            fields = b.get("fields", b) if isinstance(b, dict) else {}
            return 201, {"id": self._next_id("art"), "title": fields.get("title") or "untitled",
                         "kind": fields.get("kind", "document"), "status": "submitted", "current_version": 1}
        if m == "POST" and sub == "/progress":
            return 201, {"run_id": self.run_id, "seq": int(self._next_id("p").split("-")[1])}
        if m == "POST" and sub == "/decisions":
            return 201, {"id": self._next_id("dec"), "title": b.get("title")}
        if m == "POST" and sub == "/approvals":
            return 201, {"id": self._next_id("appr"), "status": "pending", "approver": "founder"}
        if m == "POST" and sub.endswith("/decide"):
            return 200, {"id": sub.split("/")[2], "status": "approved"}
        # ---- M4: colleagues, skills, hiring
        if m == "GET" and sub == "/agents":
            return 200, {"items": [{"id": "agent-9", "name": "Bo", "role_title": "Engineer", "role_family": "engineering",
                                    "skills": ["tdd"], "skill_tags": ["tdd", "python"], "open_tasks": 1}],
                         "next_cursor": None}
        if m == "POST" and sub == "/hiring-requests":
            return 201, {"hiring_task_id": self._next_id("task"), "created": True, "status": "backlog"}
        if m == "GET" and sub == "/skills/catalog":
            return 200, {"items": [{"slug": "arxiv", "name": "arXiv", "description": "d" * 600, "tags": ["research"],
                                    "license": "MIT", "source": "hermes", "source_kind": "hermes_optional",
                                    "source_ref": "hermes:arxiv", "item_type": "skill", "installed": False,
                                    "skill_id": None, "status": None, "stars": None}], "next_cursor": None}
        if m == "POST" and sub == "/skills/install":
            return 201, {"skill": {"slug": "arxiv", "status": "active"}, "outcome": "installed", "quarantined": False}
        if m == "POST" and sub == "/skills/author":
            return 201, {"skill": {"slug": b.get("slug"), "status": "active"}, "outcome": "installed", "quarantined": False}
        if m == "POST" and sub.startswith("/agents/") and sub.endswith("/skills"):
            return 200, {"items": [{"skill": {"slug": s}} for s in (b.get("slugs") or [])], "next_cursor": None}
        if m == "GET" and sub == "/role-templates":
            return 200, {"items": [{"slug": "software-engineer", "family": "engineering", "title": "Software Engineer",
                                    "description": "x" * 600, "default_kind": "specialist", "default_department": "Eng",
                                    "instructions": "LONG PLAYBOOK " * 200, "default_toolsets": ["company", "file"],
                                    "default_skill_slugs": ["tdd"], "required_skill_tags": ["python"], "builtin": True,
                                    "id": None}], "next_cursor": None}
        if m == "POST" and sub == "/role-templates":
            return 201, {"slug": b.get("slug"), "title": b.get("title"), "builtin": False}
        if m == "POST" and sub == "/hires":
            return 201, {"agent": {"id": self._next_id("agent"), "name": b.get("name"), "status": "active"},
                         "auto_activated": True, "approval_id": None, "approver": None,
                         "reasons_not_auto_activated": [], "skills": b.get("skill_slugs", []), "skipped_default_skills": []}
        return 404, _err("not_found", "Not found")


def _err(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": None}}


def _parse_body(ctype: str, raw: bytes) -> Any:
    if not raw:
        return None
    if ctype.startswith("application/json"):
        try:
            return json.loads(raw)
        except ValueError:
            return raw.decode("utf-8", "replace")
    if ctype.startswith("multipart/form-data"):
        msg = message_from_bytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + raw)
        fields: dict[str, str] = {}
        files: dict[str, dict[str, Any]] = {}
        for part in msg.get_payload() or []:
            name = part.get_param("name", header="content-disposition")
            filename = part.get_filename()
            payload = part.get_payload(decode=True) or b""
            if filename is not None:
                files[name] = {"filename": filename, "content_type": part.get_content_type(), "size": len(payload),
                               "content": payload.decode("utf-8", "replace")}
            else:
                fields[name] = payload.decode("utf-8", "replace")
        return {"fields": fields, "files": files}
    return raw.decode("utf-8", "replace")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--token", default="run-token-abcdef123456")
    args = ap.parse_args()
    api = StubApi(token=args.token)
    api.on_request = lambda r: print(f"[stub-api] {r.method} {r.path} auth_ok={r.authorization == 'Bearer ' + args.token} "
                                     f"body={json.dumps(r.body, ensure_ascii=False)[:600]}", flush=True)
    api.start(args.port)
    print(f"[stub-api] listening on {api.url}", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
