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

    def as_dict(self) -> dict:
        from app.core.time import format_ts

        return {
            "status": self.status,
            "message": self.message,
            "checked_at": format_ts(self.checked_at),  # type: ignore[arg-type]
            "model_available": self.model_available,
            "tool_calling": self.tool_calling,
            "context_length_configured": self.context_length_configured,
            "context_length_served": self.context_length_served,
            "warnings": list(self.warnings),
            "latency_ms": self.latency_ms,
        }


def check_ollama(
    base_url: str | None,
    model: str,
    configured_context: int,
    *,
    transport: httpx.BaseTransport | None = None,
    chat_timeout: float = 240.0,
) -> CheckOutcome:
    root = ollama_root(base_url)
    out = CheckOutcome(status="error", message="", context_length_configured=configured_context)
    scan = scan_ollama(root, transport=transport)
    if not scan.reachable:
        out.message = scan.error or f"Cannot reach Ollama at {root}"
        return out
    found = next((m for m in scan.models if m.name == model), None)
    out.model_available = found is not None
    if found is None:
        out.message = f"Model {model!r} is not available in Ollama (run `ollama pull {model}`)"
        return out
    out.tool_calling = found.tool_calling
    if found.tool_calling is False:
        out.message = f"Model {model!r} does not support tool calling; AI employees need it"
        return out
    if found.can_chat is False:
        out.message = f"Model {model!r} is not a chat model"
        return out
    if found.context_length_native and found.context_length_native < configured_context:
        out.warnings.append(
            f"The model's native window is {found.context_length_native} tokens, below the "
            f"configured {configured_context}; lower the provider's context_length."
        )
    started = time.monotonic()
    try:
        with _client(transport, chat_timeout) as http:
            # Exactly what the runtime uses: the OpenAI-compatible endpoint. Loads the model.
            resp = http.post(
                f"{root}/v1/chat/completions",
                headers={"Authorization": "Bearer ollama"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
                    "max_tokens": 8,
                    "stream": False,
                },
            )
            out.latency_ms = int((time.monotonic() - started) * 1000)
            if resp.status_code != 200:
                out.message = f"Ollama could not run {model!r} (HTTP {resp.status_code})"
                return out
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
        return out
    except (httpx.HTTPError, ValueError) as exc:
        out.message = f"Ollama request failed ({type(exc).__name__})"
        return out

    served = out.context_length_served
    if served is None:
        out.warnings.append("Could not read the served context window from /api/ps.")
    elif served < configured_context:
        out.warnings.append(
            f"Ollama serves only {served} tokens of context but this provider is configured for "
            f"{configured_context}. Its OpenAI-compatible endpoint ignores per-request num_ctx, so "
            "agent prompts would be silently truncated from the front. Fix: "
            + FIX_CONTEXT.format(n=configured_context)
            + "."
        )
    out.status = "warning" if out.warnings else "ok"
    out.message = (
        f"{model} answered in {out.latency_ms} ms; tool calling "
        f"{'supported' if found.tool_calling else 'unknown'}"
        + (f"; served context {served}" if served else "")
    )
    return out


def check_http_provider(
    kind: str,
    base_url: str,
    api_key: str | None,
    model: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> CheckOutcome:
    """Cheap authenticated model-list call (no tokens spent). Never echoes keys or bodies."""
    out = CheckOutcome(status="error", message="")
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
    started = time.monotonic()
    try:
        with _client(transport, 15.0) as http:
            resp = http.get(url, headers=headers)
    except httpx.TimeoutException:
        out.message = f"{kind} did not answer within 15s"
        return out
    except httpx.HTTPError as exc:
        out.message = f"Cannot reach {kind} ({type(exc).__name__})"
        return out
    out.latency_ms = int((time.monotonic() - started) * 1000)
    if resp.status_code in (401, 403):
        out.message = f"{kind} rejected the credential (HTTP {resp.status_code})"
        return out
    if resp.status_code != 200:
        out.message = f"{kind} answered HTTP {resp.status_code} on the model list"
        return out
    try:
        data = resp.json()
        ids = {str(m.get("id")) for m in (data.get("data") or []) if isinstance(m, dict)}
    except ValueError:
        ids = set()
    if ids:
        out.model_available = model in ids
        if model not in ids:
            out.warnings.append(f"Model {model!r} was not in the provider's model list.")
    out.status = "warning" if out.warnings else "ok"
    out.message = (
        f"{kind} reachable; credential accepted ({out.latency_ms} ms). Tool calling not probed."
    )
    return out
