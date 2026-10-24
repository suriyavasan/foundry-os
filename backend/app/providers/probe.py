"""Network probes for model providers (no database, injectable transport).

Ollama is probed in depth because of one trap: its OpenAI-compatible endpoint ignores
per-request ``num_ctx`` and serves whatever context the *server* was started with
(``OLLAMA_CONTEXT_LENGTH``; 4096 by default). Company-agent prompts (role prompt + ~20 tool
schemas) need ~5K tokens, so a default Ollama silently truncates the front of every prompt. We
therefore load the model with a tiny request and read the really-served window from ``/api/ps``.

Other providers get a cheap authenticated model-list call. NOT verified against real cloud
accounts in M3b (no keys available): those paths are covered by mocked transports only.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from app.core.time import utcnow

OLLAMA_DEFAULT_URL = "http://127.0.0.1:11434"
FIX_CONTEXT = (
    "restart Ollama with OLLAMA_CONTEXT_LENGTH={n} "
    "(for example: OLLAMA_CONTEXT_LENGTH={n} ollama serve)"
)
# Preferred families for tool calling on modest hardware, best first.
PREFERRED_FAMILIES = ("qwen2.5", "qwen3", "llama3.3", "llama3.2", "llama3.1", "mistral", "granite")
_SIZE_RE = re.compile(r"([\d.]+)\s*([BM])", re.I)


def ollama_root(base_url: str | None) -> str:
    """Normalize to the Ollama server root (no trailing slash, no ``/v1``)."""
    url = (base_url or OLLAMA_DEFAULT_URL).strip().rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3].rstrip("/")
    return url


def validate_http_url(url: str) -> str | None:
    """Error message, or None if acceptable. URL credentials are refused (they would be echoed)."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return "not a valid URL"
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "must be an http(s) URL"
    if parsed.username or parsed.password:
        return "must not contain credentials; use the credential store"
    return None


def _client(transport: httpx.BaseTransport | None, timeout: float) -> httpx.Client:
    # trust_env=False: a local model endpoint must not be routed through an env proxy.
    return httpx.Client(
        transport=transport, timeout=timeout, trust_env=False, follow_redirects=False
    )


@dataclass
class OllamaModel:
    name: str
    parameter_size: str | None = None
    quantization: str | None = None
    context_length_native: int | None = None
    capabilities: list[str] | None = None  # None = server did not say (old Ollama)

    @property
    def tool_calling(self) -> bool | None:
        return None if self.capabilities is None else "tools" in self.capabilities

    @property
    def can_chat(self) -> bool:
        return self.capabilities is None or "completion" in self.capabilities


@dataclass
class OllamaScan:
    base_url: str
    reachable: bool = False
    models: list[OllamaModel] = field(default_factory=list)
    served_context: dict[str, int] = field(default_factory=dict)  # loaded model -> context length
    error: str | None = None


def _param_billions(size: str | None) -> float:
    m = _SIZE_RE.search(size or "")
    if not m:
        return 0.0
    value = float(m.group(1))
    return value / 1000 if m.group(2).upper() == "M" else value


def scan_ollama(
    base_url: str | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
    max_models: int = 30,
) -> OllamaScan:
    root = ollama_root(base_url)
    scan = OllamaScan(base_url=root)
    try:
        with _client(transport, 4.0) as http:
            resp = http.get(f"{root}/api/tags")
            if resp.status_code != 200:
                scan.error = f"Ollama answered HTTP {resp.status_code} on /api/tags"
                return scan
            scan.reachable = True
            for item in (resp.json().get("models") or [])[:max_models]:
                name = str(item.get("name") or item.get("model") or "")
                if not name:
                    continue
                details = item.get("details") or {}
                model = OllamaModel(
                    name=name,
                    parameter_size=details.get("parameter_size"),
                    quantization=details.get("quantization_level"),
                )
                try:
                    show = http.post(f"{root}/api/show", json={"model": name})
                    if show.status_code == 200:
                        body = show.json()
                        caps = body.get("capabilities")
                        model.capabilities = (
                            [str(c) for c in caps] if isinstance(caps, list) else None
                        )
                        for key, value in (body.get("model_info") or {}).items():
                            if key.endswith(".context_length") and isinstance(value, int):
                                model.context_length_native = value
                                break
                except (httpx.HTTPError, ValueError):
                    pass
                scan.models.append(model)
            try:
                ps = http.get(f"{root}/api/ps")
                if ps.status_code == 200:
                    for item in ps.json().get("models") or []:
                        ctx = item.get("context_length")
                        if isinstance(ctx, int):
                            scan.served_context[str(item.get("name") or item.get("model"))] = ctx
            except (httpx.HTTPError, ValueError):
                pass
    except httpx.HTTPError as exc:
        scan.error = f"Cannot reach Ollama at {root} ({type(exc).__name__})"
    except ValueError:
        scan.error = f"Ollama at {root} returned an unreadable answer"
    return scan


def recommend(models: list[OllamaModel]) -> OllamaModel | None:
    """Best model for an AI employee on modest hardware: tool-capable chat models only,
    non-reasoning first, preferred families first, then a mid size (7-14B), then smaller."""
    candidates = [m for m in models if m.can_chat and m.tool_calling]
    if not candidates:
        return None

    def rank(m: OllamaModel) -> tuple:
        thinking = bool(m.capabilities and "thinking" in m.capabilities)
        family = next((i for i, f in enumerate(PREFERRED_FAMILIES) if m.name.startswith(f)), 99)
        size = _param_billions(m.parameter_size)
        size_rank = abs(size - 8) if 0 < size <= 15 else 100 + size
        return (thinking, family, size_rank, m.name)

    return min(candidates, key=rank)


# The management model must be served with at least this many tokens of context (spec M10 section 1).
MIN_MANAGEMENT_CONTEXT = 32768
CHECK_NAMES = ("reachable", "auth", "tool_calling", "context", "model_exists")
_PROBE_TOOL = "report_status"
_PROBE_PROMPT = "Call the report_status tool with status set to ok. Do not answer in text."


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class CheckOutcome:
    status: str  # ok | warning | error
    message: str
    checked_at: object = field(default_factory=utcnow)
    model_available: bool | None = None
    tool_calling: bool | None = None
    context_length_configured: int | None = None
    context_length_served: int | None = None
    warnings: list[str] = field(default_factory=list)
    latency_ms: int | None = None
    checks: list[Check] = field(default_factory=list)
    model: str | None = None

    @property
    def ok(self) -> bool:
        """Every check passed: the provider can manage a company (spec M10 section 1)."""
        return bool(self.checks) and all(c.ok for c in self.checks)

    def as_dict(self) -> dict:
        from app.core.time import format_ts

        stamp = format_ts(self.checked_at)  # type: ignore[arg-type]
        by_name = {c.name: c for c in self.checks}
        return {
            "status": self.status,
            "message": self.message,
            "checked_at": stamp,
            "tested_at": stamp,
            "model_available": self.model_available,
            "tool_calling": self.tool_calling,
            "context_length_configured": self.context_length_configured,
            "context_length_served": self.context_length_served,
            "served_context": self.context_length_served,
            "warnings": list(self.warnings),
            "latency_ms": self.latency_ms,
            "ok": self.ok,
            "model": self.model,
            "checks": [
                {"name": n, "ok": by_name[n].ok, "detail": by_name[n].detail}
                for n in CHECK_NAMES
                if n in by_name
            ],
        }


def _finish(out: CheckOutcome, checks: dict[str, Check]) -> CheckOutcome:
    """Fill the five spec checks in order (a check that never ran is a failed, 'not checked' one)."""
    for name in CHECK_NAMES:
        out.checks.append(checks.get(name) or Check(name, False, "not checked"))
    if not out.ok:
        out.status = "error"
    elif out.warnings:
        out.status = "warning"
    else:
        out.status = "ok"
    return out


def _context_check(served: int | None, source: str, out: CheckOutcome) -> Check:
    if served is None:
        return Check("context", False, f"The served context window is unknown ({source})")
    if served < MIN_MANAGEMENT_CONTEXT:
        return Check(
            "context",
            False,
            f"{served} tokens served ({source}); at least {MIN_MANAGEMENT_CONTEXT} are needed",
        )
    return Check("context", True, f"{served} tokens ({source})")


def _openai_probe_body(model: str) -> dict:
    return {
        "model": model,
        "messages": [{"role": "user", "content": _PROBE_PROMPT}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": _PROBE_TOOL,
                    "description": "Report a status word",
                    "parameters": {
                        "type": "object",
                        "properties": {"status": {"type": "string"}},
                        "required": ["status"],
                    },
                },
            }
        ],
        "max_tokens": 256,
        "stream": False,
    }


def _anthropic_probe_body(model: str) -> dict:
    return {
        "model": model,
        "max_tokens": 256,
        "messages": [{"role": "user", "content": _PROBE_PROMPT}],
        "tools": [
            {
                "name": _PROBE_TOOL,
                "description": "Report a status word",
                "input_schema": {
                    "type": "object",
                    "properties": {"status": {"type": "string"}},
                    "required": ["status"],
                },
            }
        ],
        "tool_choice": {"type": "any"},
    }


def _called_the_tool(kind: str, data: object) -> bool:
    if not isinstance(data, dict):
        return False
    if kind == "anthropic":
        return any(
            isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == _PROBE_TOOL
            for b in data.get("content") or []
        )
    for choice in data.get("choices") or []:
        message = (choice or {}).get("message") or {}
        for call in message.get("tool_calls") or []:
            if ((call or {}).get("function") or {}).get("name") == _PROBE_TOOL:
                return True
    return False


def check_ollama(
    base_url: str | None,
    model: str,
    configured_context: int,
    *,
    transport: httpx.BaseTransport | None = None,
    chat_timeout: float = 240.0,
) -> CheckOutcome:
    root = ollama_root(base_url)
    out = CheckOutcome(
        status="error", message="", context_length_configured=configured_context, model=model
    )
    checks: dict[str, Check] = {}
    scan = scan_ollama(root, transport=transport)
    if not scan.reachable:
        out.message = scan.error or f"Cannot reach Ollama at {root}"
        checks["reachable"] = Check("reachable", False, out.message)
        return _finish(out, checks)
    checks["reachable"] = Check("reachable", True, f"Ollama answered at {root}")
    checks["auth"] = Check("auth", True, "Ollama needs no credential")
    found = next((m for m in scan.models if m.name == model), None)
    out.model_available = found is not None
    if found is None:
        out.message = f"Model {model!r} is not available in Ollama (run `ollama pull {model}`)"
        checks["model_exists"] = Check("model_exists", False, out.message)
        return _finish(out, checks)
    checks["model_exists"] = Check("model_exists", True, f"{model} is installed")
    out.tool_calling = found.tool_calling
    if found.tool_calling is False:
        out.message = f"Model {model!r} does not support tool calling; AI employees need it"
        checks["tool_calling"] = Check("tool_calling", False, out.message)
        return _finish(out, checks)
    if found.can_chat is False:
        out.message = f"Model {model!r} is not a chat model"
        checks["tool_calling"] = Check("tool_calling", False, out.message)
        return _finish(out, checks)
    if found.context_length_native and found.context_length_native < configured_context:
        out.warnings.append(
            f"The model's native window is {found.context_length_native} tokens, below the "
            f"configured {configured_context}; lower the provider's context_length."
        )
    started = time.monotonic()
    try:
        with _client(transport, chat_timeout) as http:
            # Exactly what the runtime uses: the OpenAI-compatible endpoint with a tool. Loads the model.
            resp = http.post(
                f"{root}/v1/chat/completions",
                headers={"Authorization": "Bearer ollama"},
                json=_openai_probe_body(model),
            )
            out.latency_ms = int((time.monotonic() - started) * 1000)
            if resp.status_code != 200:
                out.message = f"Ollama could not run {model!r} (HTTP {resp.status_code})"
                checks["tool_calling"] = Check("tool_calling", False, out.message)
                return _finish(out, checks)
            try:
                called = _called_the_tool("openai", resp.json())
            except ValueError:
                called = False
            ps = http.get(f"{root}/api/ps")
            if ps.status_code == 200:
                for item in ps.json().get("models") or []:
                    name = str(item.get("name") or item.get("model") or "")
                    if name == model or name.split(":")[0] == model.split(":")[0]:
                        served = item.get("context_length")
                        if isinstance(served, int):
                            out.context_length_served = served
                            break
    except httpx.TimeoutException:
        out.message = (
            f"Ollama did not answer within {int(chat_timeout)}s (is {model!r} still loading?)"
        )
        checks["tool_calling"] = Check("tool_calling", False, out.message)
        return _finish(out, checks)
    except (httpx.HTTPError, ValueError) as exc:
        out.message = f"Ollama request failed ({type(exc).__name__})"
        checks["tool_calling"] = Check("tool_calling", False, out.message)
        return _finish(out, checks)

    out.tool_calling = called
    checks["tool_calling"] = Check(
        "tool_calling",
        called,
        "The model called the test tool"
        if called
        else "The model answered without calling the test tool",
    )
    served = out.context_length_served
    if served is None:
        out.warnings.append("Could not read the served context window from /api/ps.")
    elif served < configured_context:
        out.warnings.append(
            f"Ollama serves only {served} tokens of context but this provider is configured for "
            f"{configured_context}. Its OpenAI-compatible endpoint ignores per-request num_ctx, so "
            "agent prompts would be silently truncated from the front. Fix: "
            + FIX_CONTEXT.format(n=max(configured_context, MIN_MANAGEMENT_CONTEXT))
            + "."
        )
    ctx_check = _context_check(served, "from /api/ps", out)
    if not ctx_check.ok and served is not None:
        ctx_check.detail += ". Fix: " + FIX_CONTEXT.format(n=MIN_MANAGEMENT_CONTEXT)
    checks["context"] = ctx_check
    _finish(out, checks)
    if out.ok:
        out.message = f"{model} answered in {out.latency_ms} ms; tool calling works" + (
            f"; served context {served}" if served else ""
        )
    else:
        failed = next(c for c in out.checks if not c.ok)
        out.message = f"{failed.name}: {failed.detail}"
    return out


def check_http_provider(
    kind: str,
    base_url: str,
    api_key: str | None,
    model: str,
    *,
    configured_context: int | None = None,
    transport: httpx.BaseTransport | None = None,
) -> CheckOutcome:
    """Authenticated model-list call plus one tiny tool-calling request (a few dozen tokens).
    Never echoes keys or response bodies."""
    out = CheckOutcome(
        status="error", message="", context_length_configured=configured_context, model=model
    )
    checks: dict[str, Check] = {}
    base = base_url.rstrip("/")
    headers: dict[str, str] = {}
    if kind == "anthropic":
        list_url = f"{base}/v1/models" if not base.endswith("/v1") else f"{base}/models"
        chat_url = f"{base}/v1/messages" if not base.endswith("/v1") else f"{base}/messages"
        if api_key:
            headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    else:
        list_url = f"{base}/models"
        chat_url = f"{base}/chat/completions"
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
    started = time.monotonic()
    ids: dict[str, int | None] = {}
    auth_failed: int | None = None
    try:
        with _client(transport, 30.0) as http:
            resp = http.get(list_url, headers=headers)
            checks["reachable"] = Check(
                "reachable", True, f"{kind} answered (HTTP {resp.status_code})"
            )
            if resp.status_code in (401, 403):
                auth_failed = resp.status_code
            elif resp.status_code == 200:
                try:
                    for m in resp.json().get("data") or []:
                        if isinstance(m, dict) and m.get("id"):
                            ctx = m.get("context_length") or m.get("context_window")
                            ids[str(m["id"])] = ctx if isinstance(ctx, int) else None
                except (ValueError, AttributeError):
                    ids = {}
            if kind == "openrouter" and auth_failed is None:
                # OpenRouter's model list is public: its key endpoint is what proves the key.
                key_resp = http.get(f"{base}/auth/key", headers=headers)
                if key_resp.status_code in (401, 403):
                    auth_failed = key_resp.status_code
            if auth_failed is None:
                body = (
                    _anthropic_probe_body(model)
                    if kind == "anthropic"
                    else _openai_probe_body(model)
                )
                chat_started = time.monotonic()
                chat = http.post(chat_url, headers=headers, json=body, timeout=120.0)
                out.latency_ms = int((time.monotonic() - chat_started) * 1000)
                if chat.status_code in (401, 403):
                    auth_failed = chat.status_code
                else:
                    try:
                        called = chat.status_code == 200 and _called_the_tool(kind, chat.json())
                    except ValueError:
                        called = False
                    if chat.status_code == 200:
                        detail = (
                            "The model called the test tool"
                            if called
                            else "The model answered without calling the test tool"
                        )
                    else:
                        detail = f"The test request failed (HTTP {chat.status_code})"
                    checks["tool_calling"] = Check("tool_calling", called, detail)
                    out.tool_calling = called
                    in_list = model in ids if ids else None
                    if in_list is False and chat.status_code != 200:
                        checks["model_exists"] = Check(
                            "model_exists", False, f"{model!r} is not in the provider's model list"
                        )
                    elif in_list is False:
                        checks["model_exists"] = Check(
                            "model_exists",
                            True,
                            "Not in the model list, but the test request worked",
                        )
                    elif chat.status_code == 200 or in_list:
                        checks["model_exists"] = Check(
                            "model_exists",
                            True,
                            "Found in the provider's model list"
                            if in_list
                            else "The test request worked",
                        )
                    else:
                        checks["model_exists"] = Check(
                            "model_exists",
                            False,
                            f"The test request failed (HTTP {chat.status_code})",
                        )
                    out.model_available = checks["model_exists"].ok
    except httpx.TimeoutException:
        out.message = f"{kind} did not answer in time"
        checks["reachable"] = Check("reachable", False, out.message)
        return _finish(out, checks)
    except httpx.HTTPError as exc:
        out.message = f"Cannot reach {kind} ({type(exc).__name__})"
        checks["reachable"] = Check("reachable", False, out.message)
        return _finish(out, checks)
    if out.latency_ms is None:
        out.latency_ms = int((time.monotonic() - started) * 1000)
    if auth_failed is not None:
        checks["auth"] = Check(
            "auth", False, f"{kind} rejected the credential (HTTP {auth_failed})"
        )
        out.message = checks["auth"].detail
        return _finish(out, checks)
    checks["auth"] = Check(
        "auth", True, "The credential was accepted" if api_key else "No credential needed"
    )
    served = ids.get(model)
    if served is not None:
        checks["context"] = _context_check(served, "declared by the provider", out)
        out.context_length_served = served
    elif configured_context is not None:
        checks["context"] = _context_check(configured_context, "declared in this provider", out)
        out.context_length_served = configured_context
    _finish(out, checks)
    if out.ok:
        out.message = (
            f"{kind} reachable; credential accepted; tool calling works ({out.latency_ms} ms)"
        )
    else:
        failed = next(c for c in out.checks if not c.ok)
        out.message = f"{failed.name}: {failed.detail}"
    return out


@dataclass
class ModelListing:
    items: list[dict]  # [{id, name, context_length}]
    available: bool  # False: the provider offers no list we can read
    detail: str | None = None


def list_models(
    kind: str,
    base_url: str,
    api_key: str | None,
    *,
    transport: httpx.BaseTransport | None = None,
    limit: int = 500,
) -> ModelListing:
    """The provider's own model list (for pickers). Never echoes keys or response bodies."""
    if kind == "ollama":
        scan = scan_ollama(base_url, transport=transport)
        if not scan.reachable:
            return ModelListing([], False, scan.error or "Ollama is not reachable")
        return ModelListing(
            [
                {
                    "id": m.name,
                    "name": m.name,
                    "context_length": m.context_length_native,
                    "tool_calling": m.tool_calling,
                }
                for m in scan.models
            ],
            True,
        )
    base = base_url.rstrip("/")
    headers: dict[str, str] = {}
    if kind == "anthropic":
        url = f"{base}/v1/models" if not base.endswith("/v1") else f"{base}/models"
        if api_key:
            headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    else:
        url = f"{base}/models"
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
    try:
        with _client(transport, 20.0) as http:
            resp = http.get(
                url, headers=headers, params={"limit": 1000} if kind == "anthropic" else None
            )
    except httpx.HTTPError as exc:
        return ModelListing([], False, f"Cannot reach {kind} ({type(exc).__name__})")
    if resp.status_code in (401, 403):
        return ModelListing([], False, f"{kind} rejected the credential (HTTP {resp.status_code})")
    if resp.status_code != 200:
        return ModelListing(
            [], False, f"{kind} has no readable model list (HTTP {resp.status_code})"
        )
    items: list[dict] = []
    try:
        for m in resp.json().get("data") or []:
            if not isinstance(m, dict) or not m.get("id"):
                continue
            ctx = m.get("context_length") or m.get("context_window") or m.get("max_input_tokens")
            items.append(
                {
                    "id": str(m["id"]),
                    "name": str(m.get("name") or m.get("display_name") or m["id"]),
                    "context_length": ctx if isinstance(ctx, int) else None,
                    "tool_calling": None,
                }
            )
    except (ValueError, AttributeError):
        return ModelListing([], False, f"{kind} returned an unreadable model list")
    items.sort(key=lambda i: i["id"])
    return ModelListing(items[:limit], True)
