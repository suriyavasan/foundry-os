"""The Wallet and purchases API (docs/specs/m15, section 4.8). Everything is under
``/api/companies/{company_id}``: members read, owners and admins write, a non-member gets 404.

An agent never reaches the writes here (it has its own three routes in the agent API); the routes
that map to a human-only action are additionally refused with a guardrail event.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.deps import CompanyCtx, CurrentAuth, DbSession, human_only
from app.core.errors import Forbidden
from app.core.time import utcnow
from app.schemas.common import Page
from app.schemas.purchasing import (
    AgentCardOut,
    AgentCardPage,
    AgentPurchaseLimitOut,
    AgentPurchaseLimitUpdate,
    CardAccountCheck,
    CardAccountCreate,
    CardAccountOut,
    CardActionRequest,
    CardProviderList,
    CardReveal,
    CardTransactionOut,
    FundingInstructions,
    PurchaseCancelRequest,
    PurchaseDecisionRequest,
    PurchaseMarkPaid,
    PurchaseRequestDetail,
    PurchaseRequestOut,
    SubscriptionHumanAction,
    SubscriptionOut,
    WalletOverview,
    WalletSyncResult,
)
from app.services import purchasing, subscriptions, wallet
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

router = APIRouter(prefix="/companies/{company_id}", tags=["wallet"])

Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


# ---------------------------------------------------------------- overview and providers


@router.get("/wallet", response_model=WalletOverview)
def get_overview(ctx: CompanyCtx, db: DbSession):
    return wallet.overview(db, ctx)


@router.get("/wallet/providers", response_model=CardProviderList)
def get_providers(ctx: CompanyCtx, db: DbSession):
    return wallet.list_providers(db, ctx)


# ---------------------------------------------------------------- card accounts


@router.get("/wallet/accounts", response_model=Page[CardAccountOut])
def list_accounts(ctx: CompanyCtx, db: DbSession):
    return {"items": wallet.list_accounts(db, ctx), "next_cursor": None}


@router.post(
    "/wallet/accounts",
    response_model=CardAccountOut,
    status_code=201,
    dependencies=[Depends(human_only("card_account.connect"))],
)
def create_account(body: CardAccountCreate, request: Request, ctx: CompanyCtx, db: DbSession):
    allow_fake = bool(request.app.state.settings.enable_test_runtimes)
    return wallet.create_account(db, ctx, body, allow_fake=allow_fake)


@router.post(
    "/wallet/accounts/{account_id}/check",
    response_model=CardAccountCheck,
    dependencies=[Depends(human_only("card_account.connect"))],
)
def check_account(account_id: str, ctx: CompanyCtx, db: DbSession):
    return wallet.check_account(db, ctx, account_id)


@router.get(
    "/wallet/accounts/{account_id}/funding",
    response_model=FundingInstructions,
    dependencies=[Depends(human_only("card_account.connect"))],
)
def get_funding(account_id: str, response: Response, ctx: CompanyCtx, db: DbSession):
    response.headers["Cache-Control"] = "no-store"
    return wallet.funding_instructions(db, ctx, account_id)


@router.post(
    "/wallet/accounts/{account_id}/default",
    response_model=CardAccountOut,
    dependencies=[Depends(human_only("card_account.connect"))],
)
def set_default(account_id: str, ctx: CompanyCtx, db: DbSession):
    return wallet.set_default_account(db, ctx, account_id)


@router.delete(
    "/wallet/accounts/{account_id}",
    response_model=CardAccountOut,
    dependencies=[Depends(human_only("card_account.connect"))],
)
def disable_account(account_id: str, ctx: CompanyCtx, db: DbSession):
    """Disables the account and freezes its cards (the history stays)."""
    return wallet.disable_account(db, ctx, account_id)


# ---------------------------------------------------------------- cards


@router.get("/wallet/cards", response_model=AgentCardPage)
def list_cards(
    ctx: CompanyCtx,
    db: DbSession,
    status: str | None = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    items, next_cursor = wallet.list_cards(db, ctx, status=status, cursor=cursor, limit=limit)
    return {"items": items, "next_cursor": next_cursor}


@router.post("/wallet/cards/{card_id}/freeze", response_model=AgentCardOut)
def freeze_card(
    card_id: str, ctx: CompanyCtx, db: DbSession, body: CardActionRequest | None = None
):
    return wallet.freeze_card(db, ctx, card_id, body or CardActionRequest())


@router.post(
    "/wallet/cards/{card_id}/unfreeze",
    response_model=AgentCardOut,
    dependencies=[Depends(human_only("card.unfreeze"))],
)
def unfreeze_card(
    card_id: str, ctx: CompanyCtx, db: DbSession, body: CardActionRequest | None = None
):
    return wallet.unfreeze_card(db, ctx, card_id, body or CardActionRequest())


@router.post("/wallet/cards/{card_id}/close", response_model=AgentCardOut)
def close_card(card_id: str, ctx: CompanyCtx, db: DbSession, body: CardActionRequest | None = None):
    return wallet.close_card(db, ctx, card_id, body or CardActionRequest())


@router.get(
    "/wallet/cards/{card_id}/reveal",
    response_model=CardReveal,
    dependencies=[Depends(human_only("card.reveal"))],
)
def reveal_card(
    card_id: str, response: Response, ctx: CompanyCtx, db: DbSession, info: CurrentAuth
):
    """A link to the provider's own page. Needs a browser session signed in within five minutes (or
    the owner token on the CLI); a personal API token cannot reveal."""
    response.headers["Cache-Control"] = "no-store"
    if info.kind == "owner":
        recent = True
    elif info.kind == "session" and info.session is not None:
        recent = utcnow() - info.session.created_at <= timedelta(
            seconds=wallet.REVEAL_MAX_AGE_SECONDS
        )
    else:
        raise Forbidden(
            "Revealing a card needs a browser session",
            details={"reason": "token_cannot_reveal"},
        )
    return wallet.reveal_card(db, ctx, card_id, recent_auth=recent)


@router.get("/wallet/transactions", response_model=Page[CardTransactionOut])
def list_transactions(
    ctx: CompanyCtx, db: DbSession, limit: Limit = DEFAULT_LIMIT, cursor: str | None = None
):
    items, next_cursor = wallet.list_transactions(db, ctx, cursor=cursor, limit=limit)
    return {"items": items, "next_cursor": next_cursor}


@router.post("/wallet/sync", response_model=WalletSyncResult)
def sync_wallet(ctx: CompanyCtx, db: DbSession):
    return wallet.sync(db, ctx)


@router.patch(
    "/wallet/agents/{agent_id}/purchase-limit",
    response_model=AgentPurchaseLimitOut,
    dependencies=[Depends(human_only("purchase_policy.update"))],
)
def set_agent_purchase_limit(
    agent_id: str, body: AgentPurchaseLimitUpdate, ctx: CompanyCtx, db: DbSession
):
    agent = wallet.set_agent_purchase_limit(db, ctx, agent_id, body)
    return AgentPurchaseLimitOut(
        agent_id=agent.id, purchase_monthly_limit_micros=agent.purchase_monthly_limit_micros
    )


# ---------------------------------------------------------------- purchases


@router.get("/purchases", response_model=Page[PurchaseRequestOut])
def list_purchases(
    ctx: CompanyCtx,
    db: DbSession,
    status: str | None = None,
    agent_id: str | None = None,
    checkout_task_id: str | None = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    items, next_cursor = purchasing.list_purchases(
        db,
        ctx,
        status=status,
        agent_id=agent_id,
        checkout_task_id=checkout_task_id,
        cursor=cursor,
        limit=limit,
    )
    return {"items": purchasing.serialize_purchases(db, items), "next_cursor": next_cursor}


@router.get("/purchases/{purchase_id}", response_model=PurchaseRequestDetail)
def get_purchase(purchase_id: str, ctx: CompanyCtx, db: DbSession):
    return purchasing.purchase_detail(db, purchasing.get_purchase(db, ctx, purchase_id))


@router.post("/purchases/{purchase_id}/approve", response_model=PurchaseRequestDetail)
def approve_purchase(
    purchase_id: str, ctx: CompanyCtx, db: DbSession, body: PurchaseDecisionRequest | None = None
):
    row = purchasing.approve(db, ctx, purchase_id, body or PurchaseDecisionRequest())
    return purchasing.purchase_detail(db, row)


@router.post("/purchases/{purchase_id}/reject", response_model=PurchaseRequestDetail)
def reject_purchase(
    purchase_id: str, ctx: CompanyCtx, db: DbSession, body: PurchaseDecisionRequest | None = None
):
    row = purchasing.reject(db, ctx, purchase_id, body or PurchaseDecisionRequest())
    return purchasing.purchase_detail(db, row)


@router.post("/purchases/{purchase_id}/mark-paid", response_model=PurchaseRequestDetail)
def mark_paid(purchase_id: str, body: PurchaseMarkPaid, ctx: CompanyCtx, db: DbSession):
    return purchasing.purchase_detail(db, purchasing.mark_paid(db, ctx, purchase_id, body))


@router.post("/purchases/{purchase_id}/cancel", response_model=PurchaseRequestDetail)
def cancel_purchase(
    purchase_id: str, ctx: CompanyCtx, db: DbSession, body: PurchaseCancelRequest | None = None
):
    row = purchasing.cancel(db, ctx, purchase_id, body or PurchaseCancelRequest())
    return purchasing.purchase_detail(db, row)


# ---------------------------------------------------------------- subscriptions


@router.get("/subscriptions", response_model=Page[SubscriptionOut])
def list_subscriptions(
    ctx: CompanyCtx,
    db: DbSession,
    status: str | None = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    wallet.require_member_read(ctx)
    items, next_cursor = subscriptions.list_subscriptions(
        db, ctx, status=status, cursor=cursor, limit=limit
    )
    return {"items": wallet.serialize_subscriptions(db, items), "next_cursor": next_cursor}


@router.post("/subscriptions/{subscription_id}/cancelled", response_model=SubscriptionOut)
def subscription_cancelled(
    subscription_id: str,
    ctx: CompanyCtx,
    db: DbSession,
    body: SubscriptionHumanAction | None = None,
):
    sub = subscriptions.mark_cancelled(db, ctx, subscription_id, body or SubscriptionHumanAction())
    return wallet.serialize_subscriptions(db, [sub])[0]


@router.post("/subscriptions/{subscription_id}/keep", response_model=SubscriptionOut)
def subscription_keep(
    subscription_id: str,
    ctx: CompanyCtx,
    db: DbSession,
    body: SubscriptionHumanAction | None = None,
):
    sub = subscriptions.keep(db, ctx, subscription_id, body or SubscriptionHumanAction())
    return wallet.serialize_subscriptions(db, [sub])[0]
