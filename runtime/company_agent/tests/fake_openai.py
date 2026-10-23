"""Deterministic OpenAI-compatible model server for tests.

Serves ``POST /v1/chat/completions`` (JSON and ``stream: true`` SSE), ``GET /v1/models`` and
returns 404 for everything else (e.g. Ollama's ``/api/show``). Responses come from a script: a
list of steps, consumed one per chat request. A step is a dict::

    {"tool_calls": [{"name": "company_report_progress", "arguments": {"message": "hi"}}], "content": ""}
    {"content": "final text"}
    {"delay": 30.0, "content": "slow"}            # sleep before answering (cancellation tests)
    {"status": 500, "error": "boom"}              # HTTP error
    callable(request_body) -> step                # computed from the request

When the script is exhausted the last step repeats. All request bodies are recorded in ``requests``.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional


@dataclass
class FakeOpenAI:
    script: list[Any] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)
    # When set, GET /api/ps reports the fake model as loaded with this context (Ollama behaviour).
    ps_context: Optional[int] = None
    prompt_tokens: int = 120
    completion_tokens: int = 30
    _server: Optional[ThreadingHTTPServer] = None
    _index: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def start(self, port: int = 0) -> "FakeOpenAI":
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a: Any) -> None:
                pass

            def _send_json(self, status: int, body: Any) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                if self.path.rstrip("/") == "/api/ps" and outer.ps_context:
                    self._send_json(200, {"models": [{"name": "fake-model", "model": "fake-model",
                                                      "context_length": outer.ps_context}]})
                elif self.path.rstrip("/") == "/v1/models":
                    self._send_json(200, {"object": "list", "data": [{"id": "fake-model", "object": "model"}]})
                else:
                    self._send_json(404, {"error": {"message": "not found"}})

            def do_POST(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                if not self.path.endswith("/chat/completions"):
                    self._send_json(404, {"error": {"message": "not found"}})
                    return
                body = json.loads(raw)
                body["_authorization"] = self.headers.get("authorization")
                with outer._lock:
                    outer.requests.append(body)
                    step = outer._next_step(body)
                if step.get("delay"):
                    time.sleep(float(step["delay"]))
                if step.get("status"):
                    self._send_json(int(step["status"]), {"error": {"message": step.get("error", "error"), "type": "server_error"}})
                    return
                msg = outer._message(step)
                finish = "tool_calls" if msg.get("tool_calls") else "stop"
                usage = {"prompt_tokens": outer.prompt_tokens, "completion_tokens": outer.completion_tokens,
                         "total_tokens": outer.prompt_tokens + outer.completion_tokens}
                if body.get("stream"):
                    self.send_response(200)
                    self.send_header("content-type", "text/event-stream")
                    self.send_header("transfer-encoding", "chunked")
                    self.end_headers()

                    def chunk(payload: Any) -> None:
                        data = ("data: " + (payload if isinstance(payload, str) else json.dumps(payload)) + "\n\n").encode()
                        self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")

                    delta: dict[str, Any] = {"role": "assistant"}
                    if msg.get("content"):
                        delta["content"] = msg["content"]
                    if msg.get("tool_calls"):
                        delta["tool_calls"] = [{**tc, "index": i} for i, tc in enumerate(msg["tool_calls"])]
                    base = {"id": "chatcmpl-fake", "object": "chat.completion.chunk", "created": 0, "model": "fake-model"}
                    chunk({**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]})
                    chunk({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
                    chunk({**base, "choices": [], "usage": usage})
                    chunk("[DONE]")
                    self.wfile.write(b"0\r\n\r\n")
                    return
                self._send_json(200, {
                    "id": "chatcmpl-fake", "object": "chat.completion", "created": 0, "model": "fake-model",
                    "choices": [{"index": 0, "message": msg, "finish_reason": finish}], "usage": usage,
                })

        self._server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()

    @property
    def base_url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    def _next_step(self, body: dict[str, Any]) -> dict[str, Any]:
        if not self.script:
            return {"content": "(no script)"}
        step = self.script[min(self._index, len(self.script) - 1)]
        self._index += 1
        return step(body) if callable(step) else step

    def _message(self, step: dict[str, Any]) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": "assistant", "content": step.get("content") or None}
        calls = step.get("tool_calls") or []
        if calls:
            msg["tool_calls"] = [
                {"id": f"call_{self._index}_{i}", "type": "function",
                 "function": {"name": c["name"], "arguments": json.dumps(c.get("arguments", {}))}}
                for i, c in enumerate(calls)
            ]
        return msg


def tool(name: str, **arguments: Any) -> dict[str, Any]:
    return {"name": name, "arguments": arguments}
