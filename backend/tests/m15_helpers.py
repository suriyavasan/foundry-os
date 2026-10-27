"""Shared pieces for the M15 provider-side tests: the network guard (nothing in the suite may reach
Stripe or Mercury) and builders for a company with a card account, a purchase and its card."""

from __future__ import annotations

import socket
from dataclasses import dataclass
from datetime import datetime

import httpx
import pytest
from sqlalchemy.orm import Session

from app.integrations.cards.base import IssuedCard, MerchantLock
from app.integrations.cards.fake import FakeCardProvider
from app.models.purchasing import AgentCard, CardAccount, PurchaseRequest

BLOCKED_HOSTS = ("stripe.com", "mercury.com")
USD = 1_000_000


def _blocked(host: str | None) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == b or host.endswith("." + b) for b in BLOCKED_HOSTS)


@pytest.fixture(autouse=True)
def no_card_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fails any outbound connection to ``*.stripe.com`` / ``*.mercury.com``: at the httpx
    transport (real connections only; ``httpx.MockTransport`` is unaffected) and at the socket
    layer. Registered in ``conftest.py`` as an autouse fixture."""
    original_send = httpx.HTTPTransport.handle_request

    def guarded_send(self, request):
        if _blocked(request.url.host):
            raise AssertionError(f"the test suite must never call {request.url.host}")
        return original_send(self, request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", guarded_send)

    original_getaddrinfo = socket.getaddrinfo

    def guarded_getaddrinfo(host, *args, **kwargs):
        if isinstance(host, str) and _blocked(host):
            raise AssertionError(f"the test suite must never resolve {host}")
        return original_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)

    original_connect = socket.create_connection

    def guarded_connect(address, *args, **kwargs):
        if _blocked(address[0] if address else None):
            raise AssertionError(f"the test suite must never connect to {address[0]}")
        return original_connect(address, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", guarded_connect)


# ------------------------------------------------------------------ builders


def make_account(
    db: Session,
    company_id: str,
    *,
    provider: str = "fake",
    status: str = "active",
    label: str = "Test issuer",
    is_default: bool = True,
    created_at: datetime | None = None,
) -> CardAccount:
    account = CardAccount(
        company_id=company_id,
        provider=provider,
        label=label,
        status=status,
        mode="test",
        is_default=is_default,
    )
    if created_at is not None:
        account.created_at = created_at
    db.add(account)
    db.commit()
    return account


def make_purchase(
    db: Session,
    company_id: str,
    agent_id: str,
    task_id: str,
    *,
    amount: int = 20 * USD,
    recurrence: str = "one_time",
    vendor: str = "Plausible",
    domain: str = "plausible.io",
    status: str = "awaiting_checkout",
    held: int | None = None,
    checkout_task_id: str | None = None,
    dedupe: str | None = None,
) -> PurchaseRequest:
    purchase = PurchaseRequest(
        company_id=company_id,
        agent_id=agent_id,
        task_id=task_id,
        item=f"{vendor} plan",
        vendor=vendor,
        vendor_domain=domain,
        url=f"https://{domain}/pricing",
        amount_micros=amount,
        recurrence=recurrence,
        justification="needed for the launch, " + "x" * 40,
        status=status,
        decision="human",
        checkout_path="human_checkout",
        held_micros=amount if held is None else held,
        checkout_task_id=checkout_task_id,
        dedupe_key=dedupe or f"d-{vendor}-{amount}-{recurrence}-{status}",
        period_month="2026-10",
    )
    db.add(purchase)
    db.commit()
    return purchase


@dataclass
class IssuedWorld:
    account: CardAccount
    purchase: PurchaseRequest
    card: AgentCard
    issued: IssuedCard


def issue_for(
    db: Session,
    provider: FakeCardProvider,
    account: CardAccount,
    purchase: PurchaseRequest,
    *,
    limit: int | None = None,
    kind: str = "single_use",
    interval: str | None = None,
    lock: MerchantLock | None = None,
) -> IssuedWorld:
    """Issue a card at the fake provider for ``purchase`` and record it like the issuing job does."""
    single = kind == "single_use"
    lock = lock or MerchantLock(name=purchase.vendor, domain=purchase.vendor_domain)
    issued = provider.issue_card(
        limit_micros=limit or purchase.amount_micros + 2 * USD,
        limit_interval=interval or ("all_time" if single else "monthly"),
        merchant_lock=lock,
        single_use=single,
        expires_at=None,
        idempotency_key=purchase.id,
        metadata={"cos_purchase": purchase.id, "cos_company": purchase.company_id},
    )
    card = AgentCard(
        company_id=purchase.company_id,
        card_account_id=account.id,
        provider_card_id=issued.provider_card_id,
        kind=kind,
        status="active",
        purchase_request_id=purchase.id,
        agent_id=purchase.agent_id,
        last4=issued.last4,
        brand=issued.brand,
        exp_month=issued.exp_month,
        exp_year=issued.exp_year,
        limit_micros=issued.limit_micros,
        limit_interval=issued.limit_interval,
        merchant_lock={"name": lock.name, "domain": lock.domain, "mcc_allow": list(lock.mcc_allow)},
    )
    db.add(card)
    db.flush()
    purchase.card_id = card.id
    purchase.status = "awaiting_checkout"
    db.commit()
    return IssuedWorld(account, purchase, card, issued)
