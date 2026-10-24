"""Resend: sending domains and outbound mail (https://resend.com/docs, bearer key)."""

from __future__ import annotations

from typing import Any

from app.integrations.base import ApiClient


class ResendClient(ApiClient):
    provider = "resend"
    base_url = "https://api.resend.com"

    def __init__(self, http, api_key: str) -> None:
        super().__init__(http, api_key)
        self._key = api_key

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}"}

    def domains(self) -> list[dict[str, Any]]:
        body = self.json("GET", "/domains", what="list domains")
        return list(body.get("data", [])) if isinstance(body, dict) else []

    def create_domain(self, name: str) -> dict[str, Any]:
        return self.json("POST", "/domains", what="add the domain", json={"name": name})

    def domain(self, domain_id: str) -> dict[str, Any]:
        return self.json("GET", f"/domains/{domain_id}", what="read the domain")

    def verify_domain(self, domain_id: str) -> dict[str, Any]:
        return self.json("POST", f"/domains/{domain_id}/verify", what="verify the domain")

    def send(
        self,
        *,
        sender: str,
        to: list[str],
        subject: str,
        text: str,
        reply_to: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"from": sender, "to": to, "subject": subject, "text": text}
        if reply_to:
            body["reply_to"] = reply_to
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        return self.json("POST", "/emails", what="send a message", json=body, headers=headers)
