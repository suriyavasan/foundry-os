"""Provider-action executors: what an agent's ``company_provider_action`` actually does.

These run INSIDE the control plane, after the guardrail engine decided (tier, rules, rate limits,
DLP, review / approval consumed). The agent never sees a credential: the executor finds the
connected account behind the credential id and uses its secrets itself. Every executor validates
its parameters again (an approval binds the call, not its sanity) and returns only non-secret data.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.guardrails.providers import ProviderCall, ProviderRegistry
from app.integrations import catalog, registries
from app.integrations.context import IntegrationContext
from app.integrations.github import GithubClient
from app.models.reachability import ConnectedAccount
from app.services import connected_accounts, domains, email_identity, hosted_ui
from app.services import reachability as reach

CONTENT_KEYS = ("fqdn", "repo", "branch", "file_path", "file_content", "pr_title", "pr_body")


class IntegrationExecutor:
    provider = ""
    actions: frozenset[str] = frozenset()

    def __init__(self, ic: IntegrationContext) -> None:
        self.ic = ic

    # The plain contract is not usable without a database; the framework calls execute_in_session.
    def execute(self, call: ProviderCall) -> dict[str, Any]:  # pragma: no cover - never reached
        raise Conflict("This provider needs the control plane's database")

    def account(self, db: Session, call: ProviderCall) -> ConnectedAccount:
        account = connected_accounts.account_for_credential(db, call.company_id, call.credential_id)
        if account is None or account.provider != self.provider or account.status != "connected":
            raise Conflict(
                f"This credential is not a working connected {self.provider} account: connect or "
                "test the account first",
                details={"reason": "no_account"},
            )
        return account

    def prevalidate(self, db: Session, rc, credential, action: str, params: dict[str, Any]) -> None:
        """Cheap checks before the guardrail engine can park the call behind a review."""
        account = connected_accounts.account_for_credential(db, rc.ctx.company_id, credential.id)
        if account is None or account.provider != self.provider or account.status != "connected":
            raise Conflict(
                f"This credential is not a working connected {self.provider} account: connect or "
                "test the account first",
                details={"reason": "no_account"},
            )
        check = getattr(self, "check_" + action.replace(".", "_"), None)
        if check is not None:
            check(db, rc, params)

    def execute_in_session(self, db: Session, call: ProviderCall) -> dict[str, Any]:
        handler = getattr(self, "do_" + call.action.replace(".", "_"), None)
        if handler is None or call.action not in self.actions:
            raise NotFound(f"{self.provider} does not support {call.action}")
        self.account(db, call)
        return handler(db, call, dict(call.params or {}))


def _slot_free(db: Session, company_id: str) -> None:
    row = domains.get_row(db, company_id)
    if row is not None and (row.status in domains.IN_PROGRESS or row.status == "active"):
        raise Conflict(
            f"This company already has a domain ({row.domain}, {row.status})",
            details={"reason": "domain_exists"},
        )


def _s(params: dict[str, Any], key: str, *, required: bool = True) -> str | None:
    value = params.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    if required:
        raise ValidationFailed(
            f"`{key}` is required", details=[{"loc": ["params", key], "msg": "required"}]
        )
    return None


# ------------------------------------------------------------------------- cloudflare


class CloudflareExecutor(IntegrationExecutor):
    provider = "cloudflare"
    actions = catalog.SPECS["cloudflare"].actions

    def check_dns_change(self, db, rc, params):
        domain = (_s(params, "domain") or "").lower()
        if not registries.valid_hostname(domain):
            raise ValidationFailed("`domain` must be a hostname inside the zone")
        changes = params.get("changes")
        if not isinstance(changes, list):
            raise ValidationFailed("`changes` must be a list")
        domains.check_changes(changes)

    def check_tunnel_start(self, db, rc, params):
        reach.require_exposure_allowed(db, self.ic.settings)
        if _s(params, "provider") not in ("cloudflare_quick", "cloudflare_named"):
            raise ValidationFailed("`provider` must be cloudflare_quick or cloudflare_named")

    def do_dns_read(self, db, call, params):
        return domains.dns_read_core(
            db,
            self.ic,
            call.company_id,
            _s(params, "domain") or "",
            _s(params, "type", required=False),
        )

    def do_dns_change(self, db, call, params):
        changes = params.get("changes")
        if not isinstance(changes, list):
            raise ValidationFailed("`changes` must be a list")
        return domains.dns_change_core(
            db, self.ic, call.company_id, _s(params, "domain") or "", changes, apply=True
        )

    def do_tunnel_status(self, db, call, params):
        st = self.ic.supervisor.status()
        return {
            k: st[k]
            for k in ("provider", "state", "url", "hostname", "healthy", "restarts", "error")
        }

    def do_tunnel_start(self, db, call, params):
        provider = _s(params, "provider") or ""
        if provider not in ("cloudflare_quick", "cloudflare_named"):
            raise ValidationFailed("`provider` must be cloudflare_quick or cloudflare_named")
        spec = reach.start_tunnel_core(
            db, self.ic, provider, _s(params, "hostname", required=False)
        )
        return {"started": True, "provider": spec.provider, "hostname": spec.hostname}

    def do_tunnel_stop(self, db, call, params):
        reach.stop_tunnel_core(db, self.ic)
        return {"stopped": True}


# ------------------------------------------------------------------------- vercel


class VercelExecutor(IntegrationExecutor):
    provider = "vercel"
    actions = catalog.SPECS["vercel"].actions

    def check_hosted_ui_deploy(self, db, rc, params):
        from app.integrations.vercel import normalize_proxy_target

        reach.require_exposure_allowed(db, self.ic.settings)
        normalize_proxy_target(_s(params, "proxy_target") or "")

    def do_hosted_ui_deploy(self, db, call, params):
        # The approval binds the proxy target: it must be in the call, never inferred.
        target = _s(params, "proxy_target")
        reach.require_exposure_allowed(db, self.ic.settings)
        row = hosted_ui.deploy_core(
            db,
            self.ic,
            call.company_id,
            None,
            proxy_target=target,
            project_name=_s(params, "project", required=False),
        )
        return hosted_ui.view(row) or {"status": row.status}

    def do_hosted_ui_status(self, db, call, params):
        row = hosted_ui.get_row(db, call.company_id)
        if row is not None and row.status == "deploying":
            hosted_ui.refresh(db, self.ic, row)
        return hosted_ui.view(row) or {"status": "none"}


# ------------------------------------------------------------------------- github


class GithubExecutor(IntegrationExecutor):
    provider = "github"
    actions = catalog.SPECS["github"].actions

    def do_github_read(self, db, call, params):
        repo = _s(params, "repo") or ""
        if repo.count("/") != 1:
            raise ValidationFailed("`repo` must look like owner/name")
        client = connected_accounts.client_for(db, self.ic, self.account(db, call))
        assert isinstance(client, GithubClient)
        try:
            path = _s(params, "path", required=False)
            if path:
                body = client.json(
                    "GET", f"/repos/{repo}/contents/{path.lstrip('/')}", what="read the file"
                )
                return {"repo": repo, "path": path, "entry": _slim(body)}
            info = client.repo(repo)
        finally:
            client.http.close()
        if info is None:
            raise NotFound("Repository not found")
        return {
            k: info.get(k)
            for k in ("full_name", "description", "default_branch", "html_url", "private")
        }

    def do_domain_preview(self, db, call, params):
        pv = domains.build_preview(
            db,
            self.ic,
            call.company_id,
            _s(params, "registry_id") or "",
            _s(params, "name") or "",
            _s(params, "target", required=False),
        )
        return pv

    def check_domain_register_pr(self, db, rc, params):
        _slot_free(db, rc.ctx.company_id)
        self._verify_content(db, rc.ctx.company_id, params)

    def _verify_content(self, db, company_id, params):
        pv = domains.build_preview(
            db,
            self.ic,
            company_id,
            _s(params, "registry_id") or "",
            _s(params, "name") or "",
            _s(params, "target", required=False),
        )
        if not pv["eligible"]:
            raise ValidationFailed(pv["ineligible_reason"] or "Not eligible")
        for key in (*CONTENT_KEYS, "publishes_as"):
            if params.get(key) != pv[key]:
                raise ValidationFailed(
                    f"`{key}` does not match what the registry definition generates; call "
                    "domain.preview and submit exactly its content",
                    details={"reason": "content_mismatch", "field": key},
                )
        return pv

    def do_domain_register_pr(self, db, call, params):
        """Open the registry pull request with exactly the content the approval was bound to. The
        content must be what the registry definition generates for (registry_id, name, target)."""
        pv = self._verify_content(db, call.company_id, params)
        _slot_free(db, call.company_id)
        reg = domains.execute_registry_pr(db, self.ic, call.company_id, pv, None)
        return {"status": reg.status, "pr_url": reg.pr_url, "domain": reg.domain}


def _slim(body: Any) -> Any:
    if isinstance(body, list):
        return [{"name": e.get("name"), "type": e.get("type")} for e in body[:100]]
    if isinstance(body, dict):
        return {k: body.get(k) for k in ("name", "type", "size", "html_url")}
    return None


# ------------------------------------------------------------------------- e-mail providers


class MailExecutor(IntegrationExecutor):
    def _check_identity(self, db: Session, call: ProviderCall) -> None:
        row = email_identity.get_row(db, call.company_id)
        mine = {"agentmail": "agentmail", "resend": "cloudflare_resend"}[self.provider]
        if row is None or row.provider != mine:
            raise Conflict(
                f"{self.provider} is not the company's e-mail provider",
                details={"reason": "not_identity"},
            )

    def check_mailbox_create(self, db, rc, params):
        self._check_identity_for(db, rc.ctx.company_id)
        email_identity.check_new_mailbox(
            db,
            rc.ctx.company_id,
            _s(params, "local_part") or "",
            _s(params, "owner_agent_id") or "",
        )

    def check_email_send(self, db, rc, params):
        self._check_identity_for(db, rc.ctx.company_id)
        email_identity.check_send(db, rc.ctx.company_id, rc.agent.id, params)

    def check_email_read(self, db, rc, params):
        self._check_identity_for(db, rc.ctx.company_id)
        email_identity.mailbox_for_agent(db, rc.ctx.company_id, rc.agent.id, params.get("mailbox"))

    def _check_identity_for(self, db, company_id):
        row = email_identity.get_row(db, company_id)
        mine = {"agentmail": "agentmail", "resend": "cloudflare_resend"}[self.provider]
        if row is None or row.provider != mine:
            raise Conflict(
                f"{self.provider} is not the company's e-mail provider",
                details={"reason": "not_identity"},
            )

    def do_mailbox_create(self, db, call, params):
        self._check_identity(db, call)
        mailbox = email_identity.provision_mailbox(
            db,
            self.ic,
            call.company_id,
            local_part=_s(params, "local_part") or "",
            owner_agent_id=_s(params, "owner_agent_id") or "",
            display_name=_s(params, "display_name", required=False),
        )
        return {
            "address": mailbox.address,
            "status": mailbox.status,
            "owner_agent_id": mailbox.owner_agent_id,
        }

    def do_email_send(self, db, call, params):
        self._check_identity(db, call)
        return email_identity.send_core(db, self.ic, call.company_id, call.agent_id, params)

    def do_email_read(self, db, call, params):
        self._check_identity(db, call)
        return email_identity.read_core(db, call.company_id, call.agent_id, params)


class AgentMailExecutor(MailExecutor):
    provider = "agentmail"
    actions = catalog.SPECS["agentmail"].actions


class ResendExecutor(MailExecutor):
    provider = "resend"
    actions = catalog.SPECS["resend"].actions


# ------------------------------------------------------------------------- registrars


class RegistrarExecutor(IntegrationExecutor):
    def check_domain_purchase(self, db, rc, params):
        _slot_free(db, rc.ctx.company_id)
        self._purchase_args(params)

    def _purchase_args(self, params):
        domain = (_s(params, "domain") or "").lower()
        ceiling = params.get("max_price_micros")
        if isinstance(ceiling, bool) or not isinstance(ceiling, int) or ceiling <= 0:
            raise ValidationFailed(
                "`max_price_micros` (the most it may cost, in micro-USD) is required"
            )
        if params.get("years", 1) != 1:
            raise ValidationFailed("Only one-year purchases are supported")
        if not registries.valid_hostname(domain) or domain.count(".") != 1:
            raise ValidationFailed("`domain` must be a registrable domain such as example.com")
        registrar = (_s(params, "registrar", required=False) or self.provider).lower()
        if registrar != self.provider:
            raise ValidationFailed(f"This credential buys from {self.provider}, not {registrar}")
        return domain, ceiling, registrar

    def do_domain_search(self, db, call, params):
        results = domains.search(
            db, self.ic, call.company_id, _s(params, "query") or "", only=self.provider
        )
        return {
            "results": results,
            "note": "Prices are the registrar's quote (an estimate), not a charge.",
        }

    def do_domain_purchase(self, db, call, params):
        """Reached only after a human approval bound to this exact call (domain, price ceiling)."""
        domain, ceiling, registrar = self._purchase_args(params)
        _slot_free(db, call.company_id)
        reg = domains.purchase_core(
            db,
            self.ic,
            call.company_id,
            None,
            None,
            registrar=registrar,
            domain=domain,
            max_price_micros=ceiling,
        )
        return {
            "domain": reg.domain,
            "status": reg.status,
            "charged_micros": reg.price_micros,
            "cost_kind": (reg.payload or {}).get("cost_kind"),
        }


class PorkbunExecutor(RegistrarExecutor):
    provider = "porkbun"
    actions = catalog.SPECS["porkbun"].actions


class NamecheapExecutor(RegistrarExecutor):
    provider = "namecheap"
    actions = catalog.SPECS["namecheap"].actions


def register_all(registry: ProviderRegistry, ic: IntegrationContext) -> None:
    for cls in (
        CloudflareExecutor,
        VercelExecutor,
        GithubExecutor,
        AgentMailExecutor,
        ResendExecutor,
        PorkbunExecutor,
        NamecheapExecutor,
    ):
        registry.register(cls(ic))
