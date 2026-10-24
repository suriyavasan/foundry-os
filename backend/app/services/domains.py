"""Domains: a free subdomain by registry pull request, a free domain from a dashboard provider
(assisted), or a paid domain from a registrar -- plus DNS planning/verification (docs/specs/m8 2.3).

Authorization is in the services, not the routes:

* Opening a registry pull request and buying a domain are **human-only decisions**. A registration
  request creates an approval bound to the exact content (the registry file and PR text, or the
  domain and the price ceiling) and nothing happens until an owner/admin approves it. Approving
  consumes it (single use, scope-hash checked) and only then does the control plane act.
* Agents reach the same actions only as guarded provider actions
  (``app/integrations/executors.py``), where the guardrail engine's human floor applies and the
  approval is bound to the identical call.
* DNS changes through Cloudflare, by the Founder directly or by an agent after approval, use the
  same plan/apply core.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.principal import CompanyContext, Principal, require_human_admin
from app.core.time import utcnow
from app.integrations import dns as dnslib
from app.integrations import registries
from app.integrations.base import ProviderError
from app.integrations.cloudflare import CloudflareClient, apply_dns_plan, plan_dns
from app.integrations.context import IntegrationContext
from app.integrations.github import GithubClient, submit_registry_pr
from app.integrations.namecheap import NamecheapClient
from app.integrations.porkbun import PorkbunClient
from app.models.enums import Approver
from app.models.governance import Approval
from app.models.guardrails import ExternalExpense
from app.models.reachability import CompanyDomain, DomainRegistration, HostedUi
from app.schemas.reachability import (
    DomainRegistrationCreate,
    DomainRegistrationPreviewRequest,
)
from app.services import approvals, audit, connected_accounts, events

REGISTRARS = ("porkbun", "namecheap")
SEARCH_TLDS = ("com", "net", "org", "io")
MAX_SEARCH = 4
VERCEL_CNAME = "cname.vercel-dns.com"
IN_PROGRESS = ("awaiting_approval", "pr_open", "awaiting_founder", "pending_dns")
RECORD_TYPES = ("A", "AAAA", "CNAME", "TXT", "MX")
MAX_DNS_CHANGES = 20
LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


# ------------------------------------------------------------------ rows & views


def get_row(db: Session, company_id: str, *, create: bool = False) -> CompanyDomain | None:
    row = db.scalar(select(CompanyDomain).where(CompanyDomain.company_id == company_id))
    if row is None and create:
        row = CompanyDomain(company_id=company_id, status="none")
        db.add(row)
        db.flush()
    return row


def registration_view(reg: DomainRegistration) -> dict[str, Any]:
    payload = reg.payload or {}
    return {
        "id": reg.id,
        "company_id": reg.company_id,
        "path": reg.path,
        "domain": reg.domain,
        "registry_id": reg.registry_id,
        "provider_id": reg.provider_id,
        "registrar": reg.registrar,
        "status": reg.status,
        "approval_id": reg.approval_id,
        "pr_url": reg.pr_url,
        "price_micros": reg.price_micros,
        "error": reg.error,
        "created_at": reg.created_at,
        "updated_at": reg.updated_at,
        "cost_kind": payload.get("cost_kind"),
        "max_price_micros": reg.max_price_micros,
    }


def plan_view(db: Session, company_id: str) -> dict[str, Any]:
    row = get_row(db, company_id)
    if row is None or row.domain is None:
        return {
            "domain": None,
            "path": None,
            "status": "none",
            "dns_status": "unknown",
            "tls_status": "unknown",
            "dns_records": [],
            "steps": [],
            "registration": None,
            "last_checked_at": None,
            "error": None,
        }
    reg = db.get(DomainRegistration, row.registration_id) if row.registration_id else None
    return {
        "domain": row.domain,
        "path": row.path,
        "status": row.status,
        "dns_status": row.dns_status,
        "tls_status": row.tls_status,
        "dns_records": [
            {k: r.get(k) for k in ("type", "name", "value", "ttl", "status")}
            for r in (row.dns_records or [])
        ],
        "steps": list(row.steps or []),
        "registration": registration_view(reg) if reg else None,
        "last_checked_at": row.last_checked_at,
        "error": row.error,
    }


def list_registries() -> list[dict[str, Any]]:
    return [
        {
            "id": r.id,
            "name": r.name,
            "suffix": r.suffix,
            "repo": r.repo,
            "rules_url": r.rules_url,
            "eligibility": list(r.eligibility),
            "review": r.review,
            "name_min_length": r.name_min_length,
            "name_max_length": r.name_max_length,
            "name_pattern": f"^{r.name_pattern}$",
            "warnings": list(r.warnings),
        }
        for r in registries.REGISTRIES.values()
    ]


def list_free_providers() -> list[dict[str, Any]]:
    return [p.as_dict() for p in registries.FREE_PROVIDERS.values()]


# ------------------------------------------------------------------ accounts


def _account(db: Session, company_id: str, provider: str):
    account = connected_accounts.find_account(db, company_id, provider)
    if account is None:
        raise Conflict(
            f"Connect a {provider.title()} account first",
            details={"reason": "no_account", "provider": provider},
        )
    return account


def github_login(db: Session, ic: IntegrationContext, company_id: str) -> str | None:
    account = connected_accounts.find_account(db, company_id, "github")
    if account is None:
        return None
    login = (account.meta or {}).get("login")
    if login:
        return login
    client = connected_accounts.client_for(db, ic, account)
    try:
        login = str(client.identity().get("login") or "") or None
    finally:
        client.http.close()
    if login:
        account.meta = {**(account.meta or {}), "login": login}
    return login


# ------------------------------------------------------------------ registry preview


def default_target(db: Session, ic: IntegrationContext, company_id: str) -> str | None:
    """A stable CNAME target for a registry file: the hosted UI on Vercel, else the hostname of a
    running named tunnel. A quick tunnel changes every time, so it is never a target."""
    ui = db.scalar(select(HostedUi).where(HostedUi.company_id == company_id))
    if ui is not None and ui.status in ("ready", "deploying"):
        return VERCEL_CNAME
    st = ic.supervisor.status()
    if st["state"] == "running" and st["provider"] == "cloudflare_named" and st["hostname"]:
        return st["hostname"]
    return None


def build_preview(
    db: Session,
    ic: IntegrationContext,
    company_id: str,
    registry_id: str,
    name: str,
    target: str | None = None,
) -> dict[str, Any]:
    registry = registries.get_registry(registry_id)
    if registry is None:
        raise NotFound("Registry not found")
    label = name.strip().lower()
    login = github_login(db, ic, company_id)
    target = (target or "").strip().lower() or default_target(db, ic, company_id)
    reasons: list[str] = []
    problem = registry.label_problem(label)
    if problem:
        reasons.append(problem)
    if login is None:
        reasons.append("Connect a GitHub account first: the pull request is opened under it")
    if target is None:
        reasons.append(
            "There is no stable address to point the name at yet: deploy the hosted UI or start "
            "a named tunnel first (or pass a target hostname)"
        )
    elif not registries.valid_hostname(target):
        reasons.append("The target must be a hostname such as cname.vercel-dns.com")
    ui = db.scalar(select(HostedUi).where(HostedUi.company_id == company_id))
    site = ui.url if ui is not None and ui.url else None
    shown_login = login or "YOUR-GITHUB-USERNAME"
    shown_target = target or "TARGET-HOSTNAME"
    fqdn = registry.fqdn(label) if label else registry.suffix
    return {
        "fqdn": fqdn,
        "repo": registry.repo,
        "branch": f"company-os/register-{label}" if label else None,
        "file_path": registry.file_path(label or "name"),
        "file_content": registry.render_file(shown_login, shown_target),
        "pr_title": registry.pr_title(label or "name"),
        "pr_body": registry.pr_body(label or "name", shown_login, shown_target, site),
        "publishes_as": login,
        "eligible": not reasons,
        "ineligible_reason": "; ".join(reasons) or None,
        "warnings": list(registry.warnings),
        "target": target,
        "registry_id": registry.id,
        "name": label,
    }


def owner_search(
    db: Session, ctx: CompanyContext, ic: IntegrationContext, query: str
) -> list[dict[str, Any]]:
    require_human_admin(ctx, "search for domains")
    return search(db, ic, ctx.company_id, query)


def preview(
    db: Session, ctx: CompanyContext, ic: IntegrationContext, data: DomainRegistrationPreviewRequest
) -> dict[str, Any]:
    require_human_admin(ctx, "prepare a domain registration")
    return build_preview(db, ic, ctx.company_id, data.registry_id, data.name, data.target)


# ------------------------------------------------------------------ registration requests


def _ensure_free_slot(db: Session, company_id: str) -> CompanyDomain:
    row = get_row(db, company_id, create=True)
    assert row is not None
    if row.status in IN_PROGRESS or row.status == "active":
        raise Conflict(
            f"This company already has a domain ({row.domain}, {row.status})",
            details={"reason": "domain_exists", "domain": row.domain, "status": row.status},
        )
    return row


def pr_scope(reg: DomainRegistration) -> dict[str, Any]:
    """What the approval is bound to: the exact registry file and pull request text."""
    p = reg.payload or {}
    return {
        "v": 1,
        "action": "domain.register_pr",
        "registration_id": reg.id,
        "fqdn": reg.domain,
        "repo": p.get("repo"),
        "branch": p.get("branch"),
        "file_path": p.get("file_path"),
        "file_content": p.get("file_content"),
        "pr_title": p.get("pr_title"),
        "pr_body": p.get("pr_body"),
        "publishes_as": p.get("publishes_as"),
    }


def purchase_scope(reg: DomainRegistration) -> dict[str, Any]:
    p = reg.payload or {}
    return {
        "v": 1,
        "action": "domain.purchase",
        "registration_id": reg.id,
        "domain": reg.domain,
        "registrar": reg.registrar,
        "years": reg.years,
        "price_micros": p.get("quoted_micros"),
        "max_price_micros": reg.max_price_micros,
        "renewal_price_micros": reg.renewal_price_micros,
    }


def create_registration(
    db: Session, ctx: CompanyContext, ic: IntegrationContext, data: DomainRegistrationCreate
) -> DomainRegistration:
    require_human_admin(ctx, "register domains")
    body = data.root
    if body.path == "github_registry":
        reg = _create_registry_registration(db, ctx, ic, body)
    elif body.path == "free_provider":
        reg = _create_free_registration(db, ctx, body)
    else:
        reg = _create_paid_registration(db, ctx, ic, body)
    events.emit(db, ctx.company_id, "domain.updated", {"registration_id": reg.id})
    events.emit(db, ctx.company_id, "reachability.updated", {})
    db.commit()
    return reg


def _new_approval(
    db: Session,
    ctx: CompanyContext,
    reg: DomainRegistration,
    *,
    kind: str,
    tier: str,
    action: str,
    title: str,
    reason: str,
    scope: dict[str, Any],
    amount: int | None = None,
    vendor: str | None = None,
) -> Approval:
    approval = approvals.create_approval_record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        kind=kind,
        title=title[:300],
        reason=reason,
        amount_micros=amount,
        vendor=vendor,
        risk="high",
        evidence={"registration_id": reg.id},
        scope=scope,
        subject=("domain_registration", reg.id),
        approver=Approver.HUMAN,
        tier=tier,
        action=action,
        requested_by_agent_id=None,
    )
    reg.approval_id = approval.id
    return approval


def _create_registry_registration(db, ctx, ic, body) -> DomainRegistration:
    row = _ensure_free_slot(db, ctx.company_id)
    pv = build_preview(db, ic, ctx.company_id, body.registry_id, body.name, body.target)
    if not pv["eligible"]:
        raise ValidationFailed(
            pv["ineligible_reason"] or "Not eligible", details={"reason": "ineligible"}
        )
    reg = DomainRegistration(
        company_id=ctx.company_id,
        path="github_registry",
        domain=pv["fqdn"],
        registry_id=body.registry_id,
        status="awaiting_approval",
        payload={k: pv[k] for k in pv if k not in ("eligible", "ineligible_reason", "warnings")},
        created_by_type=ctx.principal.type.value,
        created_by_id=ctx.principal.id,
    )
    db.add(reg)
    db.flush()
    _new_approval(
        db,
        ctx,
        reg,
        kind="sensitive_action",
        tier="external_commitment",
        action="domain.register_pr",
        title=f"Register {pv['fqdn']}: open a pull request to {pv['repo']}",
        reason="Publishes this exact registration file under your GitHub identity and accepts "
        "the registry's terms.",
        scope=pr_scope(reg),
    )
    _set_domain(
        row,
        reg,
        status="awaiting_approval",
        records=[{"type": "CNAME", "name": pv["fqdn"], "value": pv["target"], "ttl": 1}],
        steps=[
            "Approve the pull request in Approvals (you see the exact file).",
            "A registry maintainer reviews the pull request; this can take days.",
            "When it is merged, press Verify: DNS and TLS are checked.",
        ],
    )
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="domain.registration_requested",
        target_type="domain_registration",
        target_id=reg.id,
        summary=f"Requested {pv['fqdn']} via {pv['repo']} (waiting for approval)",
        data={"path": "github_registry", "domain": pv["fqdn"], "approval_id": reg.approval_id},
    )
    return reg


def _create_free_registration(db, ctx, body) -> DomainRegistration:
    provider = registries.FREE_PROVIDERS.get(body.provider_id)
    if provider is None:
        raise NotFound("Free domain provider not found")
    domain = body.domain.strip().lower()
    if not registries.valid_hostname(domain):
        raise ValidationFailed("Enter a domain like name.dpdns.org")
    if not any(domain.endswith("." + s) for s in provider.suffixes):
        raise ValidationFailed(
            f"{provider.name} offers: {', '.join(provider.suffixes)}",
            details={"reason": "suffix_not_offered"},
        )
    row = _ensure_free_slot(db, ctx.company_id)
    reg = DomainRegistration(
        company_id=ctx.company_id,
        path="free_provider",
        domain=domain,
        provider_id=provider.id,
        status="awaiting_founder",
        payload={"dashboard_url": provider.dashboard_url},
        created_by_type=ctx.principal.type.value,
        created_by_id=ctx.principal.id,
    )
    db.add(reg)
    db.flush()
    steps = [s.replace("{domain}", domain) for s in provider.steps]
    steps.insert(0, f"Dashboard: {provider.dashboard_url}")
    _set_domain(row, reg, status="awaiting_founder", records=[], steps=steps)
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="domain.registration_requested",
        target_type="domain_registration",
        target_id=reg.id,
        summary=f"Started the assisted flow for {domain} at {provider.name}",
        data={"path": "free_provider", "domain": domain},
    )
    return reg


def _create_paid_registration(db, ctx, ic, body) -> DomainRegistration:
    registrar = body.registrar.strip().lower()
    if registrar not in REGISTRARS:
        raise ValidationFailed(f"Unknown registrar {registrar!r}")
    if body.years != 1:
        raise ValidationFailed(
            "Only one-year purchases are supported (the registrars' API charges the minimum term)",
            details=[{"loc": ["body", "years"], "msg": "must be 1"}],
        )
    domain = body.domain.strip().lower()
    if not registries.valid_hostname(domain) or domain.count(".") != 1:
        raise ValidationFailed("Enter a registrable domain such as example.com")
    row = _ensure_free_slot(db, ctx.company_id)
    quote = quote_domain(db, ic, ctx.company_id, registrar, domain)
    if not quote["available"] or quote["price_micros"] is None:
        raise Conflict(
            f"{domain} is not available at {registrar}", details={"reason": "unavailable"}
        )
    ceiling = body.max_price_micros if body.max_price_micros is not None else quote["price_micros"]
    if ceiling < quote["price_micros"]:
        raise ValidationFailed(
            "The price ceiling is below the registrar's current price",
            details={"reason": "ceiling_below_quote", "quote_micros": quote["price_micros"]},
        )
    reg = DomainRegistration(
        company_id=ctx.company_id,
        path="paid_registrar",
        domain=domain,
        registrar=registrar,
        status="awaiting_approval",
        years=1,
        price_micros=quote["price_micros"],
        max_price_micros=ceiling,
        renewal_price_micros=quote["renewal_price_micros"],
        payload={"quoted_micros": quote["price_micros"], "cost_kind": "estimated"},
        created_by_type=ctx.principal.type.value,
        created_by_id=ctx.principal.id,
    )
    db.add(reg)
    db.flush()
    _new_approval(
        db,
        ctx,
        reg,
        kind="external_purchase",
        tier="spend",
        action="domain.purchase",
        title=f"Buy {domain} from {registrar.title()} (up to ${ceiling / 1_000_000:.2f})",
        reason="Spends real money at the registrar, bound to this price ceiling.",
        scope=purchase_scope(reg),
        amount=ceiling,
        vendor=registrar.title(),
    )
    _set_domain(
        row,
        reg,
        status="awaiting_approval",
        records=[],
        steps=["Approve the purchase in Approvals (the exact price ceiling is shown)."],
    )
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="domain.registration_requested",
        target_type="domain_registration",
        target_id=reg.id,
        summary=f"Requested to buy {domain} from {registrar} (waiting for approval)",
        data={"path": "paid_registrar", "domain": domain, "max_price_micros": ceiling},
    )
    return reg


def _set_domain(
    row: CompanyDomain,
    reg: DomainRegistration,
    *,
    status: str,
    records: list[dict[str, Any]],
    steps: list[str],
    error: str | None = None,
) -> None:
    row.domain = reg.domain
    row.path = reg.path
    row.status = status
    row.registration_id = reg.id
    row.dns_records = records
    row.steps = steps
    row.dns_status = "unknown"
    row.tls_status = "unknown"
    row.error = error
    row.updated_at = utcnow()


# ------------------------------------------------------------------ the human decision


def apply_decision(db: Session, ctx: CompanyContext, approval: Approval, decision: str) -> None:
    """Called by ``approvals.decide`` for ``subject_type == "domain_registration"``. Rejecting
    cancels the request. Approving spends the approval (bound to the stored content / price
    ceiling) and then acts. A failure is recorded on the registration; the approval stays spent."""
    reg = db.scalar(
        select(DomainRegistration).where(
            DomainRegistration.id == approval.subject_id,
            DomainRegistration.company_id == ctx.company_id,
        )
    )
    if reg is None or reg.status != "awaiting_approval":
        return
    row = get_row(db, ctx.company_id, create=True)
    assert row is not None
    if decision != "approve":
        reg.status = "rejected"
        reg.error = approval.decision_note
        row.status, row.domain, row.registration_id = "none", None, None
        row.dns_records, row.steps = [], []
        _announce(db, ctx, reg, f"{reg.domain}: the request was not approved")
        return
    scope = pr_scope(reg) if reg.path == "github_registry" else purchase_scope(reg)
    ic = db.info.get("integrations")
    try:
        if ic is None:
            raise ProviderError("The integrations are not available in this process")
        approvals.consume(db, ctx, approval.id, scope)
        if reg.path == "github_registry":
            execute_registry_pr(db, ic, ctx.company_id, reg.payload, reg)
            row.status = "pr_open"
            row.steps = [
                f"Pull request opened: {reg.pr_url}",
                "A registry maintainer reviews it; this can take days.",
                "When it is merged, press Verify: DNS and TLS are checked.",
            ]
        else:
            purchase_core(db, ic, ctx.company_id, reg, ctx.principal, registrar=reg.registrar or "")
            row.status = "pending_dns"
            row.steps = purchase_steps(reg.domain)
        row.error = None
        _announce(db, ctx, reg, f"{reg.domain}: {reg.status}")
    except Conflict:
        raise
    except ProviderError as exc:
        reg.status = "failed"
        reg.error = exc.message[:500]
        row.status, row.error = "error", reg.error
        _announce(db, ctx, reg, f"{reg.domain}: failed - {reg.error}")


def purchase_steps(domain: str) -> list[str]:
    return [
        f"{domain} is registered. Add it as a zone in your Cloudflare account (free plan).",
        "At the registrar, set the domain's nameservers to the two Cloudflare shows you.",
        "Start a named tunnel for the domain (Reachability) or let the AI Co-founder do it.",
        "Press Verify: DNS and TLS are checked.",
    ]


def _announce(db: Session, ctx: CompanyContext, reg: DomainRegistration, summary: str) -> None:
    reg.updated_at = utcnow()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action=f"domain.registration_{reg.status}",
        target_type="domain_registration",
        target_id=reg.id,
        summary=summary,
        data={"path": reg.path, "domain": reg.domain, "status": reg.status},
    )
    events.emit(db, ctx.company_id, "domain.updated", {"registration_id": reg.id})
    events.emit(db, ctx.company_id, "reachability.updated", {})


# ------------------------------------------------------------------ the actions themselves


def execute_registry_pr(
    db: Session,
    ic: IntegrationContext,
    company_id: str,
    content: dict[str, Any],
    reg: DomainRegistration | None,
) -> DomainRegistration:
    """Open the pull request with EXACTLY ``content`` (callers prove it was approved). The file is
    regenerated from the registry definition and must equal what was approved."""
    registry = registries.get_registry(str(content.get("registry_id")))
    if registry is None:
        raise ProviderError("Unknown registry")
    account = _account(db, company_id, "github")
    client = connected_accounts.client_for(db, ic, account)
    assert isinstance(client, GithubClient)
    try:
        login = str(client.identity().get("login") or "")
        if login != content.get("publishes_as"):
            raise ProviderError(
                "The connected GitHub account is not the one the approval names; nothing was opened"
            )
        expected = registry.render_file(login, str(content.get("target")))
        if expected != content.get("file_content"):
            raise ProviderError("The registration file does not match the approved content")
        result = submit_registry_pr(
            client,
            upstream=registry.repo,
            login=login,
            branch=str(content["branch"]),
            file_path=str(content["file_path"]),
            file_content=str(content["file_content"]),
            commit_message=str(content["pr_title"]),
            pr_title=str(content["pr_title"]),
            pr_body=str(content["pr_body"]),
        )
    finally:
        client.http.close()
    if reg is None:
        reg = DomainRegistration(
            company_id=company_id,
            path="github_registry",
            domain=str(content["fqdn"]),
            registry_id=registry.id,
            payload={k: v for k, v in content.items()},
            status="pr_open",
        )
        db.add(reg)
    reg.status = "pr_open"
    reg.pr_url = result.get("url")
    reg.pr_number = result.get("number")
    reg.error = None
    db.flush()
    row = get_row(db, company_id, create=True)
    assert row is not None
    if row.registration_id in (None, reg.id):
        _set_domain(
            row,
            reg,
            status="pr_open",
            records=[
                {"type": "CNAME", "name": reg.domain, "value": content.get("target"), "ttl": 1}
            ],
            steps=[f"Pull request opened: {reg.pr_url}", "When it is merged, press Verify."],
        )
    return reg


def _registrar_client(db: Session, ic: IntegrationContext, company_id: str, registrar: str):
    account = _account(db, company_id, registrar)
    return account, connected_accounts.client_for(db, ic, account)


def quote_domain(
    db: Session, ic: IntegrationContext, company_id: str, registrar: str, domain: str
) -> dict[str, Any]:
    """The registrar's current quote (an estimate until a purchase reports the real charge)."""
    _, client = _registrar_client(db, ic, company_id, registrar)
    try:
        return client.check(domain)
    finally:
        client.http.close()


def search(
    db: Session, ic: IntegrationContext, company_id: str, query: str, *, only: str | None = None
) -> list[dict[str, Any]]:
    query = query.strip().lower()
    if not query:
        raise ValidationFailed("Enter a name to search")
    candidates = (
        [query]
        if "." in query
        else [f"{re.sub(r'[^a-z0-9-]', '', query)}.{tld}" for tld in SEARCH_TLDS]
    )[:MAX_SEARCH]
    for c in candidates:
        if not registries.valid_hostname(c):
            raise ValidationFailed(f"{c!r} is not a valid domain name")
    accounts = [
        a
        for r in REGISTRARS
        if (only is None or r == only) and (a := connected_accounts.find_account(db, company_id, r))
    ]
    if not accounts:
        raise Conflict(
            "Connect a registrar account (Porkbun or Namecheap) to search for domains",
            details={"reason": "no_account"},
        )
    out: list[dict[str, Any]] = []
    for account in accounts:
        client = connected_accounts.client_for(db, ic, account)
        try:
            for candidate in candidates:
                res = client.check(candidate)
                out.append(
                    {
                        "domain": candidate,
                        "available": bool(res["available"]),
                        "registrar": account.provider,
                        "price_micros": res["price_micros"],
                        "renewal_price_micros": res["renewal_price_micros"],
                        # a quote, never a charge
                        "cost_kind": "estimated" if res["price_micros"] is not None else "unknown",
                    }
                )
        finally:
            client.http.close()
    return out


def purchase_core(
    db: Session,
    ic: IntegrationContext,
    company_id: str,
    reg: DomainRegistration | None,
    principal: Principal | None,
    *,
    registrar: str,
    domain: str | None = None,
    max_price_micros: int | None = None,
) -> DomainRegistration:
    """Buy the domain. Only ever called with a consumed human approval whose scope carries the
    price ceiling. The price is quoted again right now; above the ceiling nothing is bought."""
    domain = (reg.domain if reg else domain) or ""
    ceiling = reg.max_price_micros if reg else max_price_micros
    if ceiling is None:
        raise ProviderError("A purchase needs a price ceiling")
    account, client = _registrar_client(db, ic, company_id, registrar)
    try:
        quote = client.check(domain)
        if not quote["available"] or quote["price_micros"] is None:
            raise ProviderError(f"{domain} is no longer available at {registrar}")
        if quote["price_micros"] > ceiling:
            raise ProviderError(
                f"The price rose to ${quote['price_micros'] / 1_000_000:.2f}, above the approved "
                f"ceiling of ${ceiling / 1_000_000:.2f}. Nothing was charged.",
                details={"reason": "price_exceeds_ceiling", "quote_micros": quote["price_micros"]},
            )
        if isinstance(client, PorkbunClient):
            done = client.register(domain, quote["price_micros"])
        elif isinstance(client, NamecheapClient):
            contact = {
                k.removeprefix("registrant_"): v
                for k, v in (account.meta or {}).items()
                if k.startswith("registrant_")
            }
            done = client.register(domain, contact)
        else:  # pragma: no cover - guarded by REGISTRARS
            raise ProviderError("Unsupported registrar")
    finally:
        client.http.close()
    charged = done.get("charged_micros")
    if reg is None:
        reg = DomainRegistration(
            company_id=company_id,
            path="paid_registrar",
            domain=domain,
            registrar=registrar,
            years=1,
            max_price_micros=ceiling,
            payload={},
            status="purchased",
        )
        db.add(reg)
    reg.status = "purchased"
    reg.error = None
    reg.renewal_price_micros = quote.get("renewal_price_micros")
    verified = isinstance(charged, int) and charged >= 0
    reg.price_micros = charged if verified else quote["price_micros"]
    reg.payload = {
        **(reg.payload or {}),
        "cost_kind": "verified" if verified else "estimated",
        "order_id": str(done.get("order_id") or ""),
    }
    db.flush()
    if verified:  # a verified charge is real spend: it shows up in finance
        db.add(
            ExternalExpense(
                company_id=company_id,
                vendor=registrar.title(),
                description=f"Domain {domain}, first year (order {done.get('order_id') or 'n/a'})",
                amount_micros=int(charged),
                recurrence="one_time",
                incurred_on=utcnow().date(),
                created_by_type=principal.type.value if principal else "system",
                created_by_id=principal.id if principal else None,
            )
        )
    row = get_row(db, company_id, create=True)
    assert row is not None
    if row.registration_id in (None, reg.id):
        _set_domain(row, reg, status="pending_dns", records=[], steps=purchase_steps(domain))
    return reg


# ------------------------------------------------------------------ DNS (Cloudflare)


def check_changes(changes: list[dict[str, Any]]) -> None:
    """Shape checks that need no zone (used before an approval is requested)."""
    _normalize_changes(None, changes)


def _normalize_changes(
    zone_name: str | None, changes: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if not changes or len(changes) > MAX_DNS_CHANGES:
        raise ValidationFailed(f"Provide 1-{MAX_DNS_CHANGES} DNS changes")
    out: list[dict[str, Any]] = []
    for change in changes:
        if not isinstance(change, dict):
            raise ValidationFailed("Each DNS change must be an object")
        type_ = str(change.get("type", "")).upper()
        if type_ not in RECORD_TYPES:
            raise ValidationFailed(
                f"DNS type {type_!r} is not supported here ({', '.join(RECORD_TYPES)}); "
                "nameserver and SOA changes are never made by Foundry AI OS"
            )
        name = str(change.get("name", "")).strip().lower().rstrip(".")
        if zone_name is not None:
            name = zone_name if name in ("", "@") else name
            if name != zone_name and not name.endswith("." + zone_name):
                raise ValidationFailed(f"{name!r} is not inside the zone {zone_name}")
        elif not name or not (name == "@" or registries.valid_hostname(name)):
            raise ValidationFailed(f"{name!r} is not a valid record name")
        value = str(change.get("value", "")).strip()
        if not value or len(value) > 2000:
            raise ValidationFailed("Each DNS change needs a value")
        ttl = change.get("ttl")
        out.append(
            {
                "type": type_,
                "name": name,
                "value": value,
                "ttl": ttl if isinstance(ttl, int) and ttl >= 1 else 1,
                "proxied": change.get("proxied")
                if isinstance(change.get("proxied"), bool)
                else None,
            }
        )
    return out


def dns_plan(
    db: Session, ic: IntegrationContext, company_id: str, domain: str, changes: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]], CloudflareClient]:
    """(zone, plan, client). The caller must close ``client.http``."""
    account = _account(db, company_id, "cloudflare")
    client = connected_accounts.client_for(db, ic, account)
    assert isinstance(client, CloudflareClient)
    try:
        zone = client.zone_for(domain.strip().lower())
        if zone is None:
            raise ValidationFailed(
                f"The connected Cloudflare account has no zone for {domain}",
                details={"reason": "no_zone"},
            )
        wanted = _normalize_changes(str(zone["name"]).lower(), changes)
        existing: list[dict[str, Any]] = []
        for name in {c["name"] for c in wanted}:
            existing += client.dns_list(zone["id"], name=name)
        return zone, plan_dns(wanted, existing), client
    except Exception:
        client.http.close()
        raise


def dns_change_core(
    db: Session,
    ic: IntegrationContext,
    company_id: str,
    domain: str,
    changes: list[dict[str, Any]],
    *,
    apply: bool,
) -> dict[str, Any]:
    zone, steps, client = dns_plan(db, ic, company_id, domain, changes)
    try:
        if apply:
            apply_dns_plan(client, zone["id"], steps)
    finally:
        client.http.close()
    if apply:
        row = get_row(db, company_id)
        if (
            row is not None
            and row.domain
            and (row.domain == zone["name"] or row.domain.endswith("." + zone["name"]))
        ):
            desired = [
                {k: s[k] for k in ("type", "name", "value", "ttl", "proxied")} for s in steps
            ]
            row.dns_records = desired
            row.dns_provider, row.zone_id = "cloudflare", zone["id"]
            row.dns_status = "pending"
            row.updated_at = utcnow()
    return {
        "zone": zone["name"],
        "provider": "cloudflare",
        "steps": [
            {k: s.get(k) for k in ("op", "type", "name", "value", "ttl", "previous")} for s in steps
        ],
        "applied": apply,
    }


def dns_read_core(
    db: Session, ic: IntegrationContext, company_id: str, domain: str, type_: str | None = None
) -> dict[str, Any]:
    account = _account(db, company_id, "cloudflare")
    client = connected_accounts.client_for(db, ic, account)
    assert isinstance(client, CloudflareClient)
    try:
        zone = client.zone_for(domain.strip().lower())
        if zone is None:
            raise ValidationFailed(f"The connected Cloudflare account has no zone for {domain}")
        records = client.dns_list(zone["id"], type_=type_.upper() if type_ else None)
    finally:
        client.http.close()
    return {
        "zone": zone["name"],
        "records": [
            {k: r.get(k) for k in ("type", "name", "content", "ttl", "proxied")} for r in records
        ],
    }


def owner_dns(
    db: Session,
    ctx: CompanyContext,
    ic: IntegrationContext,
    changes: list[dict[str, Any]],
    apply: bool,
) -> dict[str, Any]:
    """The Founder changes DNS themselves (a human acting directly: no approval needed)."""
    require_human_admin(ctx, "change DNS")
    row = get_row(db, ctx.company_id)
    if row is None or not row.domain:
        raise Conflict("This company has no domain yet", details={"reason": "no_domain"})
    result = dns_change_core(db, ic, ctx.company_id, row.domain, changes, apply=apply)
    if apply:
        audit.record(
            db,
            company_id=ctx.company_id,
            principal=ctx.principal,
            action="dns.changed",
            target_type="domain",
            target_id=row.id,
            summary=f"Changed DNS for {result['zone']} ({len(result['steps'])} records)",
            data={"zone": result["zone"], "changes": result["steps"]},
        )
        events.emit(db, ctx.company_id, "domain.updated", {})
        db.commit()
    return result


# ------------------------------------------------------------------ verify


def verify(db: Session, ctx: CompanyContext, ic: IntegrationContext) -> dict[str, Any]:
    require_human_admin(ctx, "verify the domain")
    return verify_core(db, ic, ctx.company_id, ctx.principal, commit=True)


def _check_pr(
    db: Session, ic: IntegrationContext, company_id: str, reg: DomainRegistration
) -> None:
    registry = registries.get_registry(reg.registry_id or "")
    account = connected_accounts.find_account(db, company_id, "github")
    if registry is None or account is None or reg.pr_number is None:
        return
    client = connected_accounts.client_for(db, ic, account)
    try:
        pr = client.pull(registry.repo, reg.pr_number)
    finally:
        client.http.close()
    if pr.get("merged"):
        reg.status = "merged"
    elif pr.get("state") == "closed":
        reg.status, reg.error = (
            "rejected",
            "The registry closed the pull request without merging it",
        )


def verify_core(
    db: Session,
    ic: IntegrationContext,
    company_id: str,
    principal: Principal | None,
    *,
    commit: bool,
) -> dict[str, Any]:
    row = get_row(db, company_id)
    if row is None or not row.domain:
        return plan_view(db, company_id)
    reg = db.get(DomainRegistration, row.registration_id) if row.registration_id else None
    if row.status == "awaiting_approval":
        return plan_view(db, company_id)
    error: str | None = None
    if reg is not None and reg.status == "pr_open":
        try:
            _check_pr(db, ic, company_id, reg)
        except ProviderError as exc:
            error = exc.message[:300]
        if reg.status == "rejected":
            row.status, row.error = "error", reg.error
            row.last_checked_at = utcnow()
            return _finish(db, company_id, principal, commit)
        if reg.status == "pr_open":  # not merged yet: nothing to resolve
            row.last_checked_at = utcnow()
            row.error = error
            return _finish(db, company_id, principal, commit)
        row.status = "pending_dns"
    resolver = ic.dns_resolver()
    desired = [
        {k: r.get(k) for k in ("type", "name", "value", "ttl", "proxied")}
        for r in (row.dns_records or [])
        if r.get("value")
    ]
    if desired:
        checked, overall = dnslib.verify_records(desired, resolver)
        row.dns_records = checked
    else:
        resolves = any(resolver.lookup(row.domain, t) for t in ("A", "AAAA", "CNAME"))
        overall = "ok" if resolves else "pending"
    row.dns_status = overall
    if overall == "ok":
        tls, detail = ic.tls_check(row.domain)
        row.tls_status = tls
        if tls == "error":
            error = detail
    else:
        row.tls_status = "unknown"
    done = reg is not None and reg.status in ("merged", "purchased", "awaiting_founder")
    if overall == "ok" and row.tls_status == "ok":
        row.status = "active"
    elif overall == "ok" and done:
        row.status = "pending_dns"
    elif row.status != "pr_open" and done:
        row.status = "awaiting_founder" if reg and reg.path == "free_provider" else "pending_dns"
    row.error = error
    row.last_checked_at = utcnow()
    return _finish(db, company_id, principal, commit)


def _finish(
    db: Session, company_id: str, principal: Principal | None, commit: bool
) -> dict[str, Any]:
    row = get_row(db, company_id)
    assert row is not None
    row.updated_at = utcnow()
    events.emit(db, company_id, "domain.updated", {})
    events.emit(db, company_id, "reachability.updated", {})
    if principal is not None:
        audit.record(
            db,
            company_id=company_id,
            principal=principal,
            action="domain.verified",
            target_type="domain",
            target_id=row.id,
            summary=f"Checked {row.domain}: DNS {row.dns_status}, TLS {row.tls_status}",
            data={"status": row.status, "dns": row.dns_status, "tls": row.tls_status},
        )
    if commit:
        db.commit()
    return plan_view(db, company_id)
