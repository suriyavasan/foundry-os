"""The hosted control UI on Vercel (docs/specs/m8 section 2.4).

The default path stays "the device serves the UI through the tunnel". This is the optional extra:
the built ``web/dist`` is deployed to the connected Vercel account with a generated ``vercel.json``
that rewrites ``/api/*`` to this device's public URL, so the browser only talks to the Vercel
origin. A deploy needs a public URL to forward to; the Founder clicking *Deploy* is the
authorization for the human path, an agent's ``hosted_ui.deploy`` provider action needs the
Co-founder's approval (tier ``identity``) bound to the exact proxy target.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict
from app.core.principal import CompanyContext, Principal, require_human_admin
from app.core.time import utcnow
from app.integrations import vercel
from app.integrations.base import ProviderError
from app.integrations.context import IntegrationContext
from app.models.company import Company
from app.models.reachability import CompanyDomain, HostedUi
from app.services import audit, connected_accounts, events
from app.services import reachability as reach
from app.services.domains import VERCEL_CNAME

READY_STATES = {"READY"}
FAILED_STATES = {"ERROR", "CANCELED"}


def get_row(db: Session, company_id: str) -> HostedUi | None:
    return db.scalar(select(HostedUi).where(HostedUi.company_id == company_id))


def view(row: HostedUi | None) -> dict[str, Any] | None:
    if row is None or row.status == "none":
        return None
    return {
        "status": row.status,
        "provider": "vercel",
        "project_name": row.project_name,
        "url": row.url,
        "proxy_target": row.proxy_target,
        "last_deployed_at": row.last_deployed_at,
        "error": row.error,
        "approval_id": None,
    }


def _points_at_vercel(domain: CompanyDomain) -> bool:
    return any(
        str(r.get("value", "")).rstrip(".").lower() == VERCEL_CNAME
        for r in (domain.dns_records or [])
    )


def current_proxy_target(db: Session, ic: IntegrationContext, company_id: str) -> str | None:
    """The public https origin of this device: the active domain that points at the device, else
    the running tunnel. A domain that points at the hosted UI itself is never the target (the UI
    would forward ``/api`` to itself)."""
    domain = db.scalar(select(CompanyDomain).where(CompanyDomain.company_id == company_id))
    if (
        domain is not None
        and domain.status == "active"
        and domain.domain
        and not _points_at_vercel(domain)
    ):
        return f"https://{domain.domain}"
    return reach.public_url(ic)


def get_status(db: Session, ctx: CompanyContext, ic: IntegrationContext) -> dict[str, Any] | None:
    connected_accounts.require_human(ctx)
    row = get_row(db, ctx.company_id)
    if row is not None and row.status == "deploying":
        refresh(db, ic, row)  # the deployment finishes asynchronously at Vercel
        db.commit()
    return view(row)


def refresh(db: Session, ic: IntegrationContext, row: HostedUi) -> None:
    account = connected_accounts.find_account(db, row.company_id, "vercel")
    if account is None or not row.deployment_id:
        return
    try:
        client = connected_accounts.client_for(db, ic, account)
    except ProviderError:
        return
    try:
        dep = client.deployment(row.deployment_id)
    except ProviderError as exc:
        row.error = exc.message[:300]
        return
    finally:
        client.http.close()
    state = str(dep.get("readyState") or dep.get("status") or "").upper()
    if state in READY_STATES:
        aliases = dep.get("alias") or dep.get("aliasAssigned") or []
        alias = aliases[0] if isinstance(aliases, list) and aliases else None
        host = alias or dep.get("url")
        row.status, row.error = "ready", None
        row.url = f"https://{host}" if host else row.url
        row.last_deployed_at = utcnow()
    elif state in FAILED_STATES:
        row.status = "error"
        row.error = f"Vercel reported the deployment as {state.lower()}"
    row.updated_at = utcnow()


def _attach_domain(
    db: Session, ic: IntegrationContext, account, company_id: str, project: str
) -> None:
    """When the company's domain (registry file / DNS) points at Vercel, add it to the project so
    Vercel serves it (best effort: the deployment itself must not fail because of it)."""
    domain = db.scalar(select(CompanyDomain).where(CompanyDomain.company_id == company_id))
    if (
        domain is None
        or not domain.domain
        or domain.status not in ("pending_dns", "active")
        or not _points_at_vercel(domain)
    ):
        return
    client = connected_accounts.client_for(db, ic, account)
    try:
        client.add_domain(project, domain.domain)
    except ProviderError as exc:
        if (exc.details or {}).get("status") != 409:  # 409: already attached
            domain.error = f"Vercel did not attach {domain.domain}: {exc.message[:200]}"
    finally:
        client.http.close()


def deploy_core(
    db: Session,
    ic: IntegrationContext,
    company_id: str,
    principal: Principal | None,
    *,
    proxy_target: str | None = None,
    project_name: str | None = None,
) -> HostedUi:
    """Deploy ``web/dist`` to Vercel with ``/api/*`` forwarded to ``proxy_target``. Authorization is
    the caller's (a human click, or a consumed Co-founder approval for an agent)."""
    account = connected_accounts.find_account(db, company_id, "vercel")
    if account is None:
        raise Conflict(
            "Connect a Vercel account first", details={"reason": "no_account", "provider": "vercel"}
        )
    target = vercel.normalize_proxy_target(
        proxy_target or current_proxy_target(db, ic, company_id) or ""
    )
    company = db.get(Company, company_id)
    assert company is not None
    name = project_name or vercel.project_name_for(company.name)
    files = vercel.collect_dist(
        ic.settings.resolved_web_dist, target, max_bytes=ic.settings.hosted_ui_max_bytes
    )
    row = get_row(db, company_id)
    if row is None:
        row = HostedUi(company_id=company_id, provider="vercel", status="none")
        db.add(row)
    client = connected_accounts.client_for(db, ic, account)
    try:
        project = client.project(name) or client.project_create(name)
        deployment = client.deploy(name, files)
    except ProviderError as exc:
        row.status, row.error, row.project_name, row.proxy_target = (
            "error",
            exc.message[:500],
            name,
            target,
        )
        row.updated_at = utcnow()
        db.flush()
        return row
    finally:
        client.http.close()
    _attach_domain(db, ic, account, company_id, name)
    row.project_name = name
    row.project_id = str(project.get("id") or "") or None
    row.deployment_id = str(deployment.get("id") or "") or None
    row.proxy_target = target
    row.status, row.error = "deploying", None
    host = deployment.get("url")
    row.url = f"https://{host}" if host else row.url
    row.updated_at = utcnow()
    db.flush()
    refresh(db, ic, row)  # a static site is often READY on the first read
    return row


def deploy(db: Session, ctx: CompanyContext, ic: IntegrationContext) -> dict[str, Any]:
    require_human_admin(ctx, "deploy the hosted UI")
    reach.require_exposure_allowed(db, ic.settings)
    row = deploy_core(db, ic, ctx.company_id, ctx.principal)
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="hosted_ui.deployed" if row.status != "error" else "hosted_ui.failed",
        target_type="hosted_ui",
        target_id=row.id,
        summary=f"Deployed the control UI to Vercel project {row.project_name} ({row.status})",
        data={"project": row.project_name, "proxy_target": row.proxy_target, "status": row.status},
    )
    events.emit(db, ctx.company_id, "hosted_ui.updated", {})
    events.emit(db, ctx.company_id, "reachability.updated", {})
    db.commit()
    return view(row) or {"status": "none", "provider": "vercel"}
