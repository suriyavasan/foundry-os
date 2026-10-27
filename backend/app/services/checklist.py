"""The Founder's one-time steps (docs/specs/m8 section 4), computed from facts."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.principal import CompanyContext
from app.integrations.context import IntegrationContext
from app.models.purchasing import CardAccount
from app.models.reachability import CompanyDomain
from app.services import connected_accounts, email_identity
from app.services import reachability as reach


def _connect_status(db: Session, company_id: str, providers: list[str]) -> str:
    accounts = [a for p in providers for a in connected_accounts.live_accounts(db, company_id, p)]
    if any(a.status == "connected" for a in accounts):
        return "done"
    return "attention" if accounts else "todo"


def _wallet_status(db: Session, company_id: str) -> str:
    """Done once purchasing is switched on with an active card account; attention when an account
    needs a look; todo otherwise."""
    from app.services import policy, wallet

    accounts = list(db.scalars(select(CardAccount).where(CardAccount.company_id == company_id)))
    live = [a for a in accounts if a.status != "disabled"]
    if (
        wallet.active_account(db, company_id) is not None
        and policy.load_policy(db, company_id).purchasing_enabled
    ):
        return "done"
    return "attention" if any(a.status == "attention" for a in live) else "todo"


def build(db: Session, ctx: CompanyContext, ic: IntegrationContext) -> dict:
    connected_accounts.require_human(ctx)
    cid = ctx.company_id
    block = reach.exposure_block(db, ic.settings)
    tunnel = ic.supervisor.status()
    domain = db.scalar(select(CompanyDomain).where(CompanyDomain.company_id == cid))
    identity = email_identity.identity_view(db, cid)

    def step(id_, title, description, after, status, optional=False, providers=()):
        return {
            "id": id_,
            "title": title,
            "description": description,
            "after": after,
            "status": status,
            "optional": optional,
            "providers": list(providers),
        }

    wallet_status = _wallet_status(db, cid)
    steps = [
        step(
            "password",
            "Protect sign-in with a strong password",
            "Public access is only allowed with password sign-in and a strong owner password "
            "(2FA is recommended).",
            "Once done, you can switch on local-network access and a public URL.",
            "todo" if block else "done",
        ),
        step(
            "cloudflare",
            "Connect Cloudflare",
            "A Cloudflare API token lets the AI Co-founder run a stable tunnel and manage DNS.",
            "The AI Co-founder can start a named tunnel; DNS changes still need your approval.",
            _connect_status(db, cid, ["cloudflare"]),
            providers=["cloudflare"],
        ),
        step(
            "tunnel",
            "Start a public tunnel",
            "A quick tunnel needs no account; a named tunnel gives a stable address.",
            "The URL appears on the landing screen and is health-checked.",
            "done"
            if tunnel["state"] == "running"
            else ("attention" if tunnel["state"] == "error" else "todo"),
            optional=True,
        ),
        step(
            "github",
            "Connect GitHub",
            "Needed to register a free subdomain by pull request (published under your account).",
            "The AI Co-founder prepares the registration file; you approve the exact content.",
            _connect_status(db, cid, ["github"]),
            optional=True,
            providers=["github"],
        ),
        step(
            "domain",
            "Get a domain",
            "A free subdomain, a free domain from a dashboard provider, or a paid domain.",
            "The AI Co-founder wires DNS to the tunnel once the domain is yours.",
            "done"
            if domain and domain.status == "active"
            else ("attention" if domain and domain.status == "error" else "todo"),
            optional=True,
        ),
        step(
            "vercel",
            "Connect Vercel (optional)",
            "Host the control UI on Vercel; it forwards /api to this device.",
            "The AI Co-founder can redeploy the hosted UI when it changes.",
            _connect_status(db, cid, ["vercel"]),
            optional=True,
            providers=["vercel"],
        ),
        step(
            "email",
            "Give the company an e-mail identity",
            "AgentMail, or Resend on your own domain.",
            "The AI Co-founder creates company@, cofounder@ and per-agent mailboxes; invitations "
            "are e-mailed.",
            "done"
            if identity["status"] == "connected"
            else ("attention" if identity["status"] == "error" else "todo"),
            providers=["agentmail", "resend"],
        ),
        step(
            "agent_cards",
            "Set up agent cards (optional)",
            "Lets agents request purchases. A person always completes the checkout; the system "
            "issues the card and records the charge. Until then a request becomes your task.",
            "Agents can request purchases within the limits you set; everything else comes to you.",
            wallet_status,
            optional=True,
        ),
    ]
    done = sum(1 for s in steps if s["status"] == "done")
    return {"steps": steps, "done": done, "total": len(steps)}
