"""Porkbun: domain availability, pricing and registration (API v3, key + secret in headers)."""

from __future__ import annotations

from typing import Any

from app.integrations.base import ApiClient, ProviderError, micros_to_cents, usd_to_micros


class PorkbunClient(ApiClient):
    provider = "porkbun"
    base_url = "https://api.porkbun.com/api/json/v3"

    def __init__(self, http, api_key: str, secret_key: str) -> None:
        super().__init__(http, api_key, secret_key)
        self._key, self._secret = api_key, secret_key

    def auth_headers(self) -> dict[str, str]:
        return {"X-API-Key": self._key, "X-Secret-API-Key": self._secret}

    def _ok(self, body: Any, what: str) -> dict[str, Any]:
        if not isinstance(body, dict) or body.get("status") != "SUCCESS":
            msg = body.get("message") if isinstance(body, dict) else None
            raise self._fail(200, str(msg or "unsuccessful"), what)
        return body

    def ping(self) -> dict[str, Any]:
        return self._ok(
            self.json("POST", "/ping", what="credential check", json={}), "credential check"
        )

    def check(self, domain: str) -> dict[str, Any]:
        """``{domain, available, price_micros, renewal_price_micros, premium, min_years}``."""
        body = self._ok(
            self.json("POST", f"/domain/checkDomain/{domain}", what="availability check", json={}),
            "availability check",
        )
        res = body.get("response") or {}
        renewal = ((res.get("additional") or {}).get("renewal") or {}).get("price")
        return {
            "domain": domain,
            "available": str(res.get("avail", "")).lower() == "yes",
            "price_micros": usd_to_micros(res.get("price")),
            "renewal_price_micros": usd_to_micros(renewal),
            "premium": str(res.get("premium", "no")).lower() == "yes",
            "min_years": int(res.get("minDuration") or 1),
        }

    def register(self, domain: str, cost_micros: int, *, dry_run: bool = False) -> dict[str, Any]:
        """Register ``domain`` for exactly ``cost_micros`` (Porkbun refuses when the price differs).
        Only ever called after a human approval consumed for this domain and price ceiling."""
        payload: dict[str, Any] = {"cost": micros_to_cents(cost_micros), "agreeToTerms": "yes"}
        if dry_run:
            payload["dryRun"] = True
        body = self._ok(
            self.json("POST", f"/domain/create/{domain}", what="registration", json=payload),
            "registration",
        )
        if not dry_run and "cost" not in body:
            raise ProviderError("porkbun: the registration response carried no cost")
        charged = body.get("cost")
        return {
            "domain": domain,
            "dry_run": bool(body.get("dryRun")),
            "charged_micros": int(charged) * 10_000 if isinstance(charged, int) else None,
            "order_id": body.get("orderId"),
        }
