"""Company-level M8 routes: connected accounts, domain, hosted UI, e-mail identity, mailboxes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool  # noqa: F401  (sync routes run in the pool)

from app.api.deps import CompanyCtx, DbSession, human_only
from app.schemas import reachability as schemas
from app.schemas.common import Page
from app.services import (
    checklist,
    connected_accounts,
    domains,
    email_identity,
    hosted_ui,
)
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

router = APIRouter(prefix="/companies/{company_id}", tags=["reachability"])
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]
AccountGate = Annotated[object, Depends(human_only("connected_account.manage"))]


def _ic(request: Request):
    return request.app.state.integrations


# ------------------------------------------------------------ connected accounts


@router.get("/connected-accounts", response_model=Page[schemas.ConnectedAccount])
def list_accounts(
    ctx: CompanyCtx,
    db: DbSession,
    _: AccountGate,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    items, nxt = connected_accounts.list_accounts(db, ctx, limit=limit, cursor=cursor)
    return {"items": [_account(a) for a in items], "next_cursor": nxt}


def _account(row) -> dict:
    return {
        "id": row.id,
        "company_id": row.company_id,
        "provider": row.provider,
        "label": row.label,
        "credential_id": row.credential_id,
        "scopes": list(row.scopes or []),
        "status": row.status,
        "last_checked_at": row.last_checked_at,
        "metadata": {k: str(v) for k, v in (row.meta or {}).items()},
        "created_at": row.created_at,
        "last_error": row.last_error,
    }


@router.get("/connected-accounts/providers", response_model=list[schemas.ProviderInfo])
def provider_catalog(ctx: CompanyCtx, db: DbSession, _: AccountGate):
    """What can be connected, which secret fields each needs and which agent actions it enables."""
    connected_accounts.require_human(ctx)
    return connected_accounts.providers_overview()


@router.post("/connected-accounts", response_model=schemas.ConnectedAccount, status_code=201)
def create_account(
    body: schemas.ConnectedAccountCreate,
    request: Request,
    ctx: CompanyCtx,
    db: DbSession,
    _: AccountGate,
):
    return _account(connected_accounts.create_account(db, ctx, _ic(request), body))


@router.post("/connected-accounts/oauth/start", response_model=schemas.OAuthStart)
def oauth_start(body: schemas.OAuthStartRequest, ctx: CompanyCtx, db: DbSession, _: AccountGate):
    connected_accounts.oauth_start(ctx, body.provider)


@router.post("/connected-accounts/{account_id}/test", response_model=schemas.ProviderCheck)
def test_account(account_id: str, request: Request, ctx: CompanyCtx, db: DbSession, _: AccountGate):
    return connected_accounts.check_account(db, ctx, _ic(request), account_id)


@router.delete("/connected-accounts/{account_id}", response_model=schemas.ConnectedAccount)
def revoke_account(account_id: str, ctx: CompanyCtx, db: DbSession, _: AccountGate):
    return _account(connected_accounts.revoke_account(db, ctx, account_id))


# ------------------------------------------------------------ domain


@router.get("/domain", response_model=schemas.DomainPlan)
def get_domain(ctx: CompanyCtx, db: DbSession):
    connected_accounts.require_human(ctx)
    return domains.plan_view(db, ctx.company_id)


@router.get("/domain/registries", response_model=Page[schemas.DomainRegistry])
def list_registries(ctx: CompanyCtx):
    connected_accounts.require_human(ctx)
    return {"items": domains.list_registries(), "next_cursor": None}


@router.get("/domain/free-providers", response_model=Page[schemas.FreeDomainProvider])
def list_free_providers(ctx: CompanyCtx):
    connected_accounts.require_human(ctx)
    return {"items": domains.list_free_providers(), "next_cursor": None}


@router.get("/domain/search", response_model=Page[schemas.DomainAvailability])
def search_domains(
    request: Request, ctx: CompanyCtx, db: DbSession, q: str = Query(min_length=1, max_length=253)
):
    return {"items": domains.owner_search(db, ctx, _ic(request), q), "next_cursor": None}


@router.post("/domain/registrations/preview", response_model=schemas.DomainRegistrationPreview)
def preview_registration(
    body: schemas.DomainRegistrationPreviewRequest, request: Request, ctx: CompanyCtx, db: DbSession
):
    result = domains.preview(db, ctx, _ic(request), body)
    db.commit()  # the GitHub login lookup may have been cached on the account
    return result


@router.post("/domain/registrations", response_model=schemas.DomainRegistration, status_code=201)
def create_registration(
    body: schemas.DomainRegistrationCreate, request: Request, ctx: CompanyCtx, db: DbSession
):
    """Creates the registration and the human approval it waits for (registry PR / purchase), or
    starts the assisted flow (free dashboard provider). Nothing is published or bought until the
    approval is decided."""
    return domains.registration_view(domains.create_registration(db, ctx, _ic(request), body))


@router.post("/domain/verify", response_model=schemas.DomainPlan)
def verify_domain(request: Request, ctx: CompanyCtx, db: DbSession):
    return domains.verify(db, ctx, _ic(request))


@router.post("/domain/dns/plan", response_model=schemas.DnsPlanResult)
def plan_dns(body: schemas.DnsApplyRequest, request: Request, ctx: CompanyCtx, db: DbSession):
    """Added: what changing these records at Cloudflare would do (nothing is changed)."""
    return domains.owner_dns(db, ctx, _ic(request), body.changes, apply=False)


@router.post("/domain/dns/apply", response_model=schemas.DnsPlanResult)
def apply_dns(body: schemas.DnsApplyRequest, request: Request, ctx: CompanyCtx, db: DbSession):
    """Added: the Founder applies DNS changes through the connected Cloudflare account."""
    return domains.owner_dns(db, ctx, _ic(request), body.changes, apply=True)


# ------------------------------------------------------------ hosted UI


@router.get("/hosted-ui", response_model=schemas.HostedUiDeployment | None)
def get_hosted_ui(request: Request, ctx: CompanyCtx, db: DbSession):
    return hosted_ui.get_status(db, ctx, _ic(request))


@router.post("/hosted-ui/deploy", response_model=schemas.HostedUiDeployment)
def deploy_hosted_ui(request: Request, ctx: CompanyCtx, db: DbSession):
    return hosted_ui.deploy(db, ctx, _ic(request))


# ------------------------------------------------------------ e-mail identity


@router.get("/email", response_model=schemas.EmailIdentity)
def get_email(ctx: CompanyCtx, db: DbSession):
    return email_identity.get_identity(db, ctx)


@router.patch("/email", response_model=schemas.EmailIdentity)
def update_email(body: schemas.EmailIdentityUpdate, ctx: CompanyCtx, db: DbSession):
    return email_identity.update_identity(
        db, ctx, "provider" in body.model_fields_set, body.provider, body.ai_disclosure_footer
    )


@router.post("/email/refresh", response_model=schemas.EmailIdentity)
def refresh_email(request: Request, ctx: CompanyCtx, db: DbSession):
    """Added: re-check the sending domain and activate mailboxes waiting for it."""
    return email_identity.refresh(db, ctx, _ic(request))


@router.get("/mailboxes", response_model=Page[schemas.Mailbox])
def list_mailboxes(
    ctx: CompanyCtx, db: DbSession, limit: Limit = DEFAULT_LIMIT, cursor: str | None = None
):
    items, nxt = email_identity.list_mailboxes(db, ctx, limit=limit, cursor=cursor)
    return {"items": items, "next_cursor": nxt}


@router.post("/mailboxes", response_model=schemas.Mailbox, status_code=201)
def create_mailbox(body: schemas.MailboxCreate, request: Request, ctx: CompanyCtx, db: DbSession):
    return email_identity.create_mailbox(db, ctx, _ic(request), body)


# ------------------------------------------------------------ checklist


@router.get("/reachability/checklist", response_model=schemas.Checklist)
def get_checklist(request: Request, ctx: CompanyCtx, db: DbSession):
    return checklist.build(db, ctx, _ic(request))
