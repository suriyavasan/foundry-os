"""Approval policy callback for Hermes' dangerous-action gates.

Hermes asks an approval callback before things like writing agent-instruction files or (with a
sandbox-less terminal, which company runs never get) running risky commands. Company OS never
lets a model self-approve and never has a human at this terminal, so the answer is always
``"deny"`` - never ``"once"``/``"session"``/``"always"``. Real approvals go through the Company OS
approval workflow (``company_request_approval``), decided by the founder or a human.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

DENIED_REASON = (
    "Denied by Company OS policy: employees cannot approve risky actions themselves. "
    "Use company_request_approval for anything that needs sign-off."
)


def make_approval_callback(
    sandbox_backend: str,
    on_denied: Optional[Callable[[str, str], None]] = None,
) -> Callable[..., str]:
    """Build the callback ``(command, description, **kwargs) -> "deny"``.

    ``on_denied(command, reason)`` lets the launcher surface the denial as a progress event."""

    def callback(command: str = "", description: str = "", *args: Any, **kwargs: Any) -> str:
        if on_denied is not None:
            try:
                on_denied(str(command), f"{DENIED_REASON} ({description})" if description else DENIED_REASON)
            except Exception:
                pass
        return "deny"

    callback.sandbox_backend = sandbox_backend  # type: ignore[attr-defined]
    return callback
