"""AgentMail: programmatic inboxes for agents (https://docs.agentmail.to, API v0, bearer key)."""

from __future__ import annotations

from typing import Any

from app.integrations.base import ApiClient


class AgentMailClient(ApiClient):
    provider = "agentmail"
    base_url = "https://api.agentmail.to/v0"

    def __init__(self, http, api_key: str) -> None:
        super().__init__(http, api_key)
        self._key = api_key

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}"}

    def inboxes(self, limit: int = 20) -> list[dict[str, Any]]:
        body = self.json("GET", "/inboxes", what="list inboxes", params={"limit": limit})
        return list(body.get("inboxes", [])) if isinstance(body, dict) else []

    def create_inbox(
        self,
        *,
        username: str,
        domain: str | None = None,
        display_name: str | None = None,
        client_id: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"username": username}
        if domain:
            body["domain"] = domain
        if display_name:
            body["display_name"] = display_name
        if client_id:  # idempotent: the same client_id returns the same inbox
            body["client_id"] = client_id
        return self.json("POST", "/inboxes", what="create an inbox", json=body)

    def send(
        self,
        inbox_id: str,
        *,
        to: list[str],
        subject: str,
        text: str,
        html: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"to": to, "subject": subject, "text": text}
        if html:
            body["html"] = html
        return self.json(
            "POST", f"/inboxes/{inbox_id}/messages/send", what="send a message", json=body
        )
