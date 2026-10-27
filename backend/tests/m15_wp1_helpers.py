"""Shared helpers for the M15 purchasing tests: a company with a fake card account, a small team
and the agent-API calls of a purchase request. Nothing here reaches a real card provider."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.integrations.cards.fake import FakeCardProvider
from app.models.company import CompanyMember
from app.models.purchasing import CardAccount
from app.services import wallet
from tests.guardrail_helpers import make_team, run_as
from tests.helpers import api, set_policy

USD = 1_000_000
JUSTIFICATION = (
    "We need privacy-friendly analytics for the launch page; the free options lack an API."
)


def purchase_body(task_id: str, **over: Any) -> dict:
    body: dict[str, Any] = {
        "item": "Plausible Analytics Growth plan",
        "vendor": "Plausible Insights OU",
        "url": "https://plausible.io/pricing",
        "amount_usd": "19.00",
        "recurring": "none",
        "justification": JUSTIFICATION,
        "task_id": task_id,
    }
    body.update(over)
    return body


class World:
    def __init__(self, app, client, cid: str, team, fake: FakeCardProvider | None, account_id):
        self.app, self.client, self.cid, self.team = app, client, cid, team
        self.fake, self.account_id = fake, account_id

    def agent(self, who: str = "worker", title: str = "work"):
        """An agent mid-run on a fresh task of its own (its run token)."""
        return run_as(self.app, self.client, self.cid, getattr(self.team, who), title)

    def buy(self, session, **over):
        return session.post("/purchases", json=purchase_body(session.task_id, **over))

    def policy(self, **fields) -> dict:
        return set_policy(self.client, self.cid, **fields)

    def purchase(self, purchase_id: str) -> dict:
        r = self.client.get(api(self.cid, f"/purchases/{purchase_id}"))
        assert r.status_code == 200, r.text
        return r.json()

    def owner_member_id(self) -> str:
        with self.app.state.session_factory() as db:
            return db.scalar(
                select(CompanyMember.id).where(
                    CompanyMember.company_id == self.cid, CompanyMember.role == "owner"
                )
            )


def make_world(
    app,
    client,
    company,
    *,
    provider: str = "fake",
    funded: int = 1000 * USD,
    purchasing: bool = True,
    **policy_fields,
) -> World:
    """A company with a team, a card account (a stateful fake, or a manual card) and purchasing
    switched on. Every band stays at 0 unless the test passes it."""
    cid = company["id"]
    team = make_team(client, cid)
    fake = None
    account_id = None
    if provider == "fake":
        fake = FakeCardProvider(funded_micros=funded)
        with app.state.session_factory() as db:
            account = CardAccount(
                company_id=cid, provider="fake", label="Test issuing", status="active",
                mode="test", is_default=True,
            )  # fmt: skip
            db.add(account)
            db.commit()
            account_id = account.id
        wallet.PROVIDER_OVERRIDES[account_id] = fake
    elif provider == "manual":
        r = client.post(
            api(cid, "/wallet/accounts"),
            json={
                "provider": "manual",
                "label": "Company Visa",
                "manual_brand": "visa",
                "manual_last4": "1234",
            },
        )
        assert r.status_code == 201, r.text
        account_id = r.json()["id"]
    if purchasing:
        set_policy(client, cid, purchasing_enabled=True, **policy_fields)
    elif policy_fields:
        set_policy(client, cid, **policy_fields)
    return World(app, client, cid, team, fake, account_id)
