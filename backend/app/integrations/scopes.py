"""What an approver sees for a provider action: deterministic display fields derived from the
action's own parameters (no I/O). They are added to the approval ``scope`` next to the payload
digest, so the Founder reads the exact PR file / price ceiling / DNS changes / address, and the
scope hash still binds the approval to the identical call."""

from __future__ import annotations

from typing import Any

M8_APPROVAL_ACTIONS = frozenset(
    {"domain.register_pr", "domain.purchase", "dns.change", "mailbox.create", "hosted_ui.deploy"}
)


def display_scope(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    if action not in M8_APPROVAL_ACTIONS:
        return {}
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    pick = lambda *keys: {k: params[k] for k in keys if params.get(k) not in (None, "")}  # noqa: E731
    if action == "domain.register_pr":
        return pick(
            "fqdn",
            "repo",
            "branch",
            "file_path",
            "file_content",
            "pr_title",
            "pr_body",
            "publishes_as",
        )
    if action == "domain.purchase":
        return pick("domain", "registrar", "years", "max_price_micros")
    if action == "dns.change":
        out = pick("zone", "domain", "provider")
        changes = params.get("changes")
        if isinstance(changes, list):
            out["changes"] = [
                {k: c.get(k) for k in ("op", "type", "name", "value", "ttl") if isinstance(c, dict)}
                for c in changes[:50]
            ]
        return out
    if action == "mailbox.create":
        return pick("address", "owner_agent_id", "owner_agent_name", "provider")
    return pick("provider", "project", "proxy_target")
