"""Cloudflare: account, zones, DNS records, tunnels and Email Routing (API v4, bearer token).

The token the Founder connects needs, depending on what they want: Zone:Read, DNS:Edit, Account
Cloudflare Tunnel:Edit, Email Routing Rules:Edit. ``test_connection`` reports what it could see.
"""

from __future__ import annotations

from typing import Any

from app.integrations.base import ApiClient, ProviderError, ProviderNotConfigured


class CloudflareClient(ApiClient):
    provider = "cloudflare"
    base_url = "https://api.cloudflare.com/client/v4"

    def __init__(self, http, token: str) -> None:
        super().__init__(http, token)
        self._token = token

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _result(self, method: str, path: str, *, what: str, **kwargs: Any) -> Any:
        body = self.json(method, path, what=what, **kwargs)
        if isinstance(body, dict) and body.get("success") is False:
            errors = body.get("errors") or []
            text = "; ".join(str(e.get("message", e)) for e in errors[:3] if isinstance(e, dict))
            raise self._fail(200, text or "unsuccessful", what)
        return body.get("result") if isinstance(body, dict) else body

    # -- connection
    def verify_token(self) -> dict[str, Any]:
        return self._result("GET", "/user/tokens/verify", what="token verification") or {}

    def accounts(self) -> list[dict[str, Any]]:
        return list(
            self._result("GET", "/accounts", what="list accounts", params={"per_page": 50}) or []
        )

    # -- zones
    def zones(self, name: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"per_page": 50}
        if name:
            params["name"] = name
        return list(self._result("GET", "/zones", what="list zones", params=params) or [])

    def zone_for(self, hostname: str) -> dict[str, Any] | None:
        """The zone that contains ``hostname`` (longest matching suffix the token can see)."""
        labels = hostname.lower().strip(".").split(".")
        for i in range(len(labels) - 1):
            candidate = ".".join(labels[i:])
            found = self.zones(candidate)
            if found:
                return found[0]
        return None

    # -- DNS records
    def dns_list(
        self, zone_id: str, *, type_: str | None = None, name: str | None = None
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"per_page": 100}
        if type_:
            params["type"] = type_
        if name:
            params["name"] = name
        return list(
            self._result(
                "GET", f"/zones/{zone_id}/dns_records", what="list DNS records", params=params
            )
            or []
        )

    def dns_create(self, zone_id: str, record: dict[str, Any]) -> dict[str, Any]:
        return self._result(
            "POST", f"/zones/{zone_id}/dns_records", what="create a DNS record", json=record
        )

    def dns_update(self, zone_id: str, record_id: str, record: dict[str, Any]) -> dict[str, Any]:
        return self._result(
            "PUT",
            f"/zones/{zone_id}/dns_records/{record_id}",
            what="update a DNS record",
            json=record,
        )

    def dns_delete(self, zone_id: str, record_id: str) -> None:
        self._result(
            "DELETE", f"/zones/{zone_id}/dns_records/{record_id}", what="delete a DNS record"
        )

    # -- tunnels
    def tunnel_create(self, account_id: str, name: str) -> dict[str, Any]:
        return self._result(
            "POST",
            f"/accounts/{account_id}/cfd_tunnel",
            what="create a tunnel",
            json={"name": name, "config_src": "cloudflare"},
        )

    def tunnel_list(self, account_id: str, name: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"is_deleted": "false"}
        if name:
            params["name"] = name
        return list(
            self._result(
                "GET", f"/accounts/{account_id}/cfd_tunnel", what="list tunnels", params=params
            )
            or []
        )

    def tunnel_token(self, account_id: str, tunnel_id: str) -> str:
        token = self._result(
            "GET", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/token", what="read tunnel token"
        )
        if not isinstance(token, str) or not token:
            raise ProviderError("cloudflare: the tunnel token was not returned")
        self._secrets = (*self._secrets, token)  # never echo it in an error
        return token

    def tunnel_configure(
        self, account_id: str, tunnel_id: str, hostname: str, service: str
    ) -> dict[str, Any]:
        config = {
            "ingress": [{"hostname": hostname, "service": service}, {"service": "http_status:404"}]
        }
        return self._result(
            "PUT",
            f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations",
            what="configure the tunnel",
            json={"config": config},
        )

    # -- Email Routing
    def email_routing_rules(self, zone_id: str) -> list[dict[str, Any]]:
        return list(
            self._result(
                "GET", f"/zones/{zone_id}/email/routing/rules", what="list email routing rules"
            )
            or []
        )

    def email_routing_create(
        self, zone_id: str, address: str, *, forward_to: str | None, worker: str | None
    ) -> dict[str, Any]:
        if worker:
            action = {"type": "worker", "value": [worker]}
        elif forward_to:
            action = {"type": "forward", "value": [forward_to]}
        else:
            raise ProviderNotConfigured(
                "cloudflare: an inbound route needs a destination: set `email_worker` or "
                "`forward_to` on the Cloudflare account"
            )
        rule = {
            "name": f"company-os {address}",
            "enabled": True,
            "matchers": [{"type": "literal", "field": "to", "value": address}],
            "actions": [action],
        }
        return self._result(
            "POST", f"/zones/{zone_id}/email/routing/rules", what="create an email route", json=rule
        )


# ---------------------------------------------------------------------- DNS plan helpers


def plan_dns(desired: list[dict[str, Any]], existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What to do to make ``existing`` (Cloudflare records) match ``desired`` for the names and
    types in ``desired`` only. Never touches other records. Each step: ``{op: create|update|noop,
    type, name, value, ttl, proxied?, id?}``. ``update`` happens when a record of the same type
    and name has another value (CNAME/A/AAAA are single-valued per name; TXT/MX match on exact
    content)."""
    steps: list[dict[str, Any]] = []
    for want in desired:
        typ, name = want["type"].upper(), want["name"].lower().rstrip(".")
        value = str(want["value"]).strip()
        same = [
            r
            for r in existing
            if str(r.get("type", "")).upper() == typ
            and str(r.get("name", "")).lower().rstrip(".") == name
        ]
        base = {
            "type": typ,
            "name": name,
            "value": value,
            "ttl": want.get("ttl", 1),
            "proxied": want.get("proxied"),
        }
        if any(_norm(r.get("content")) == _norm(value) for r in same):
            hit = next(r for r in same if _norm(r.get("content")) == _norm(value))
            steps.append({**base, "op": "noop", "id": hit.get("id")})
        elif same and typ in ("A", "AAAA", "CNAME"):
            steps.append(
                {
                    **base,
                    "op": "update",
                    "id": same[0].get("id"),
                    "previous": same[0].get("content"),
                }
            )
        else:
            steps.append({**base, "op": "create"})
    return steps


def _norm(value: Any) -> str:
    return str(value or "").strip().strip('"').lower().rstrip(".")


def record_body(step: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": step["type"],
        "name": step["name"],
        "content": step["value"],
        "ttl": step.get("ttl") or 1,
    }
    if step.get("proxied") is not None and step["type"] in ("A", "AAAA", "CNAME"):
        body["proxied"] = bool(step["proxied"])
    return body


def apply_dns_plan(
    client: CloudflareClient, zone_id: str, steps: list[dict[str, Any]]
) -> list[dict]:
    done: list[dict[str, Any]] = []
    for step in steps:
        if step["op"] == "create":
            res = client.dns_create(zone_id, record_body(step))
        elif step["op"] == "update":
            res = client.dns_update(zone_id, step["id"], record_body(step))
        else:
            res = {"id": step.get("id")}
        done.append(
            {
                "op": step["op"],
                "type": step["type"],
                "name": step["name"],
                "id": (res or {}).get("id"),
            }
        )
    return done
