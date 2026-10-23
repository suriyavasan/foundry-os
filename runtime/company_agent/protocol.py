"""JSONL event protocol writer (launcher -> control plane, on the real stdout).

* one JSON object per line: ``{"seq": n, "type": ..., "ts": ..., "payload": {...}}``;
* ``seq`` starts at 1 and strictly increases (assigned under a lock, so concurrent tool threads
  cannot reorder or duplicate);
* exactly one terminal event (``run.completed`` / ``run.failed`` / ``run.cancelled``); anything
  emitted after it is dropped;
* every string in every payload is redacted (the run token, the model API key, bearer tokens and
  common key shapes) and long strings are truncated.

Stdlib only.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from typing import Any, Callable, IO, Iterable, Optional

TERMINAL_TYPES = frozenset({"run.completed", "run.failed", "run.cancelled"})
EVENT_TYPES = frozenset({
    "run.started", "message", "reasoning", "tool.started", "tool.completed", "progress", "usage", "artifact",
    *TERMINAL_TYPES,
})

PREVIEW_CHARS = 2000
MESSAGE_CHARS = 20_000
REASONING_CHARS = 2000
MIN_SECRET_LEN = 8
REDACTED = "[REDACTED]"

_PATTERNS = [
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\b(?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|secret|password)(\"?\s*[:=]\s*\"?)[^\s\"',}]{8,}"),
]


def _sub_key_value(m: "re.Match[str]") -> str:
    return f"{m.group(1)}{m.group(2)}{REDACTED}"


class Redactor:
    """Replaces known secret strings and common credential shapes."""

    def __init__(self, secrets: Iterable[str] = ()):
        self._secrets: list[str] = []
        self.add(secrets)

    def add(self, secrets: Iterable[str]) -> None:
        for s in secrets:
            if isinstance(s, str) and len(s) >= MIN_SECRET_LEN and s not in self._secrets:
                self._secrets.append(s)
        # longest first so a secret that contains another is replaced whole
        self._secrets.sort(key=len, reverse=True)

    def text(self, value: str) -> str:
        for s in self._secrets:
            if s in value:
                value = value.replace(s, REDACTED)
        for pat in _PATTERNS:
            value = pat.sub(_sub_key_value if pat.groups >= 2 else REDACTED, value)
        return value

    def obj(self, value: Any, *, max_chars: int = MESSAGE_CHARS) -> Any:
        """Deep copy with strings redacted and truncated; non-JSON values become strings."""
        if isinstance(value, str):
            return truncate(self.text(value), max_chars)
        if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
            return value
        if isinstance(value, dict):
            return {str(self.text(str(k))): self.obj(v, max_chars=max_chars) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self.obj(v, max_chars=max_chars) for v in value]
        return truncate(self.text(str(value)), max_chars)


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 15)] + "...[truncated]"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class EventWriter:
    """Thread-safe JSONL writer with sequence numbers, a single-terminal guarantee and redaction."""

    def __init__(
        self,
        stream: IO[str],
        secrets: Iterable[str] = (),
        on_broken: Optional[Callable[[], None]] = None,
    ):
        self._stream = stream
        self._lock = threading.Lock()
        self._seq = 0
        self._terminal: Optional[str] = None
        self._broken = False
        self._on_broken = on_broken
        self.redactor = Redactor(secrets)

    @property
    def seq(self) -> int:
        return self._seq

    @property
    def terminal_type(self) -> Optional[str]:
        return self._terminal

    @property
    def broken(self) -> bool:
        return self._broken

    def add_secrets(self, secrets: Iterable[str]) -> None:
        with self._lock:
            self.redactor.add(secrets)

    def preview(self, value: Any, limit: int = PREVIEW_CHARS) -> str:
        """Redacted, truncated string preview of any value (tool args/results)."""
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        return truncate(self.redactor.text(text), limit)

    def emit(self, type_: str, payload: Optional[dict[str, Any]] = None) -> bool:
        """Write one event. Returns False when dropped (after the terminal event, or stream gone)."""
        if type_ not in EVENT_TYPES:
            raise ValueError(f"unknown event type {type_!r}")
        is_terminal = type_ in TERMINAL_TYPES
        broken_cb = None
        with self._lock:
            if self._terminal is not None or self._broken:
                return False
            clean = self.redactor.obj(payload or {})
            self._seq += 1
            line = json.dumps(
                {"seq": self._seq, "type": type_, "ts": now_iso(), "payload": clean},
                ensure_ascii=False, separators=(",", ":"),
            )
            try:
                self._stream.write(line + "\n")
                self._stream.flush()
            except (BrokenPipeError, OSError, ValueError):
                self._broken = True
                broken_cb = self._on_broken
                ok = False
            else:
                ok = True
                if is_terminal:
                    self._terminal = type_
        if broken_cb is not None:
            try:
                broken_cb()
            except Exception:
                pass
        return ok
