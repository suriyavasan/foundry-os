"""Connected accounts: the vault that lets the control plane act on third-party services.

The Founder stores each secret once through the credential store (M3b) and then connects the
account (this module). Owner/admin only. Agents never read an account or its secrets: they ask for
a guarded provider action, which runs in the control plane (``app/integrations/executors.py``).
Connecting an account scopes its credential to the provider and to the actions that provider
enables; every action is still gated by the guardrail tiers.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import CredentialCipher, CredentialDecryptError
from app.core.errors import Conflict, Forbidden, NotAvailable, NotFound, ValidationFailed
from app.core.principal import CompanyContext, require_human_admin
from app.core.time import utcnow
from app.integrations import catalog
from app.integrations.base import ProviderError
from app.integrations.context import IntegrationContext
from app.integrations.tailscale import find_tailscale
from app.models.provider import Credential
from app.models.reachability import ConnectedAccount
from app.schemas.reachability import ConnectedAccountCreate
from app.services import audit, credentials, events
from app.services.agents import looks_like_secret_key
from app.services.pagination import paginate
from app.services.secrets_scan import find_secret

MAX_META_VALUE = 200


def require_human(ctx: CompanyContext) -> None:
    if not ctx.principal.is_human:
        raise Forbidden("Agents cannot use this endpoint; ask for a guarded provider action")


def _load(db: Session, company_id: str, account_id: str) -> ConnectedAccount:
    row = db.scalar(
        select(ConnectedAccount).where(
            ConnectedAccount.id == account_id, ConnectedAccount.company_id == company_id
        )
    )
    if row is None:
        raise NotFound("Connected account not found")
    return row


def list_accounts(db: Session, ctx: CompanyContext, *, limit: int, cursor: str | None):
    require_human_admin(ctx, "see connected accounts")
    stmt = select(ConnectedAccount).where(ConnectedAccount.company_id == ctx.company_id)
    return paginate(db, stmt, ConnectedAccount, limit=limit, cursor=cursor, descending=False)


def get_account(db: Session, ctx: CompanyContext, account_id: str) -> ConnectedAccount:
    require_human_admin(ctx, "see connected accounts")
    return _load(db, ctx.company_id, account_id)


def live_accounts(db: Session, company_id: str, provider: str | None = None):
    stmt = select(ConnectedAccount).where(
        ConnectedAccount.company_id == company_id, ConnectedAccount.status != "revoked"
    )
    if provider:
        stmt = stmt.where(ConnectedAccount.provider == provider)
    return list(db.scalars(stmt.order_by(ConnectedAccount.created_at, ConnectedAccount.id)))


def find_account(
    db: Session, company_id: str, provider: str, *, usable_only: bool = True
) -> ConnectedAccount | None:
    for account in live_accounts(db, company_id, provider):
        if not usable_only or account.status == "connected":
            return account
    return None


def account_for_credential(
    db: Session, company_id: str, credential_id: str
) -> ConnectedAccount | None:
    return db.scalar(
        select(ConnectedAccount).where(
            ConnectedAccount.company_id == company_id,
            ConnectedAccount.credential_id == credential_id,
            ConnectedAccount.status != "revoked",
        )
    )


# ------------------------------------------------------------------ secrets (never leave here)


def secrets_for(db: Session, cipher: CredentialCipher, account: ConnectedAccount) -> dict[str, str]:
    """Decrypted secrets by field name. For the control plane only: never returned or logged."""
    spec = catalog.spec_for(account.provider)
    out: dict[str, str] = {}
    if spec and spec.needs_secret and account.credential_id:
        out[spec.secrets[0]] = credentials.reveal(
            db, cipher, account.company_id, account.credential_id
        )
    for key, credential_id in (account.extra_credential_ids or {}).items():
        out[key] = credentials.reveal(db, cipher, account.company_id, credential_id)
    return out


def client_for(db: Session, ic: IntegrationContext, account: ConnectedAccount):
    """An API client for the account (call ``client.http.close()`` when done)."""
    if account.status == "revoked":
        raise Conflict("This connected account was revoked")
    try:
        secrets = secrets_for(db, ic.cipher, account)
    except CredentialDecryptError as exc:
        raise ProviderError(
            "The stored secret cannot be decrypted with this server's key; re-enter it"
        ) from exc
    return catalog.build_client(
        account.provider, secrets, dict(account.meta or {}), ic.http, ic.settings
    )


# ------------------------------------------------------------------ create / test / revoke


def _clean_metadata(provider: str, raw: dict[str, str]) -> dict[str, str]:
    spec = catalog.spec_for(provider)
    assert spec is not None
    out: dict[str, str] = {}
    for key, value in raw.items():
        allowed = key in spec.metadata or (
            provider == "namecheap" and key.startswith("registrant_")
        )
        if not allowed:
            raise ValidationFailed(
                f"Unknown field {key!r} for {spec.label}",
                details=[{"loc": ["body", "metadata", key], "msg": "not a field of this provider"}],
            )
        text = str(value).strip()
        if len(text) > MAX_META_VALUE or looks_like_secret_key(key) or find_secret(text):
            raise ValidationFailed(
                f"{key!r} must not hold a secret or be longer than {MAX_META_VALUE} characters; "
                "secrets go through the credential store",
                details=[{"loc": ["body", "metadata", key], "msg": "secret-like value"}],
            )
        out[key] = text
    return out


def _bind_credential(
    db: Session, row: Credential, provider: str, actions: list[str], *, main: bool
) -> None:
    if row.provider and row.provider != provider:
        raise Conflict(
            f"Credential {row.name!r} already belongs to provider {row.provider!r}",
            details={"credential_id": row.id},
        )
    row.provider = provider
    # Only the main secret is offered to agents (as the credential_id of a provider action). Extra
    # secrets (a second key, a webhook signing secret) are used by the control plane alone.
    row.allowed_actions = sorted(actions) if main else []
    row.updated_at = utcnow()


def create_account(
    db: Session, ctx: CompanyContext, ic: IntegrationContext, data: ConnectedAccountCreate
) -> ConnectedAccount:
    require_human_admin(ctx, "connect accounts")
    provider = data.provider.strip().lower()
    spec = catalog.spec_for(provider)
    if spec is None:
        raise ValidationFailed(
            f"Unknown provider {provider!r}",
            details=[{"loc": ["body", "provider"], "msg": "unknown provider"}],
        )
    if spec.oauth:
        raise NotAvailable(
            f"Connecting {spec.label} needs OAuth, which this install has not been configured for",
            milestone="M8",
            details={"provider": provider, "reason": "oauth_not_configured"},
        )
    live = [a for a in live_accounts(db, ctx.company_id, provider) if a.label == data.label]
    if live:
        raise Conflict(f"An account labelled {data.label!r} is already connected for {spec.label}")

    main: Credential | None = None
    if spec.needs_secret:
        if not data.credential_id:
            raise ValidationFailed(
                f"{spec.label} needs an API secret: store it first, then pass its credential_id",
                details=[{"loc": ["body", "credential_id"], "msg": "required"}],
            )
        main = credentials.require_usable(db, ctx.company_id, data.credential_id)
    extras: dict[str, Credential] = {}
    allowed_extra = (*spec.secrets[1:], *spec.optional_secrets)
    for key, credential_id in data.extra_credential_ids.items():
        if key not in allowed_extra:
            raise ValidationFailed(
                f"Unknown secret field {key!r} for {spec.label}",
                details=[{"loc": ["body", "extra_credential_ids", key], "msg": "unknown field"}],
            )
        extras[key] = credentials.require_usable(db, ctx.company_id, credential_id)
    missing = [k for k in spec.secrets[1:] if k not in extras]
    if missing:
        raise ValidationFailed(
            f"{spec.label} also needs: {', '.join(missing)}",
            details=[{"loc": ["body", "extra_credential_ids"], "msg": f"missing {missing}"}],
        )
    meta = _clean_metadata(provider, data.metadata)
    absent = [k for k in spec.required_metadata if not meta.get(k)]
    if absent:
        raise ValidationFailed(
            f"{spec.label} also needs: {', '.join(absent)}",
            details=[{"loc": ["body", "metadata"], "msg": f"missing {absent}"}],
        )

    if main is not None:
        _bind_credential(db, main, provider, list(spec.actions), main=True)
    for credential in extras.values():
        _bind_credential(db, credential, provider, [], main=False)

    account = ConnectedAccount(
        company_id=ctx.company_id,
        provider=provider,
        label=data.label,
        credential_id=main.id if main else None,
        extra_credential_ids={k: c.id for k, c in extras.items()},
        meta=meta,
        status="connected",
        created_by_type=ctx.principal.type.value,
        created_by_id=ctx.principal.id,
    )
    db.add(account)
    db.flush()
    _run_check(db, ic, account)
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="connected_account.created",
        target_type="connected_account",
        target_id=account.id,
        summary=f"Connected {spec.label} account {account.label} ({account.status})",
        data={"provider": provider, "label": account.label, "status": account.status},
    )
    events.emit(db, ctx.company_id, "connected_account.updated", {"account_id": account.id})
    events.emit(db, ctx.company_id, "reachability.updated", {})
    db.commit()
    return account


def _run_check(
    db: Session, ic: IntegrationContext, account: ConnectedAccount
) -> catalog.CheckResult:
    spec = catalog.spec_for(account.provider)
    assert spec is not None
    result: catalog.CheckResult
    if account.provider == "tailscale":
        result = catalog.check_tailscale(find_tailscale(ic.settings.tailscale_path), ic.run)
    else:
        try:
            client = client_for(db, ic, account)
        except ProviderError as exc:
            result = catalog.CheckResult(False, exc.message)
        except Exception as exc:  # a revoked / missing credential
            result = catalog.CheckResult(
                False, getattr(exc, "message", None) or "secret unavailable"
            )
        else:
            try:
                result = catalog.check_connection(
                    account.provider, client, dict(account.meta or {})
                )
            finally:
                client.http.close()
    now = utcnow()
    account.last_checked_at = now
    account.status = "connected" if result.ok else "invalid"
    account.last_error = None if result.ok else (result.message or "The test failed")[:500]
    account.scopes = list(result.scopes)
    if result.metadata:
        merged = dict(account.meta or {})
        merged.update({k: str(v)[:MAX_META_VALUE] for k, v in result.metadata.items()})
        account.meta = merged
    account.updated_at = now
    return result


def refresh_scope(db: Session, account: ConnectedAccount) -> bool:
    """Grant the account's main credential the provider's CURRENT actions (M14: a GitHub token
    connected in M8 gains the repository actions when a repository is bound). Only adds: nothing a
    human configured is removed. Audited as ``connected_account.scope_refreshed``; no commit.
    Returns True when the grant changed."""
    spec = catalog.spec_for(account.provider)
    if spec is None or not spec.needs_secret or not account.credential_id or not spec.actions:
        return False
    row = db.get(Credential, account.credential_id)
    if row is None or row.company_id != account.company_id or row.revoked_at is not None:
        return False
    current = list(row.allowed_actions or [])
    added = sorted(a for a in spec.actions if a not in current)
    if not added:
        return False
    _bind_credential(db, row, account.provider, sorted({*current, *spec.actions}), main=True)
    audit.record(
        db,
        company_id=account.company_id,
        principal=None,
        action="connected_account.scope_refreshed",
        target_type="connected_account",
        target_id=account.id,
        summary=f"Granted {account.provider} account {account.label} {len(added)} new action(s)",
        data={"provider": account.provider, "added": added},
    )
    events.emit(db, account.company_id, "connected_account.updated", {"account_id": account.id})
    return True


def check_account(
    db: Session, ctx: CompanyContext, ic: IntegrationContext, account_id: str
) -> dict[str, Any]:
    require_human_admin(ctx, "test connected accounts")
    account = _load(db, ctx.company_id, account_id)
    if account.status == "revoked":
        raise Conflict("This connected account was revoked")
    result = _run_check(db, ic, account)
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="connected_account.tested",
        target_type="connected_account",
        target_id=account.id,
        summary=f"Tested {account.provider} account {account.label}: "
        + ("ok" if result.ok else "failed"),
        data={"provider": account.provider, "ok": result.ok},
    )
    events.emit(db, ctx.company_id, "connected_account.updated", {"account_id": account.id})
    events.emit(db, ctx.company_id, "reachability.updated", {})
    db.commit()
    return {
        "ok": result.ok,
        "message": result.message,
        "checked_at": account.last_checked_at,
        "account_label": result.account_label,
        "scopes": list(result.scopes),
    }


def revoke_account(db: Session, ctx: CompanyContext, account_id: str) -> ConnectedAccount:
    """Revoke: agents lose the provider at once. Its stored secrets are revoked too unless another
    live account or model provider still uses them."""
    require_human_admin(ctx, "revoke connected accounts")
    account = _load(db, ctx.company_id, account_id)
    if account.status == "revoked":
        return account
    now = utcnow()
    account.status = "revoked"
    account.revoked_at = now
    account.updated_at = now
    db.flush()
    ids = [account.credential_id, *(account.extra_credential_ids or {}).values()]
    for credential_id in filter(None, ids):
        still_used = db.scalar(
            select(ConnectedAccount.id).where(
                ConnectedAccount.company_id == ctx.company_id,
                ConnectedAccount.status != "revoked",
                ConnectedAccount.credential_id == credential_id,
            )
        )
        if still_used:
            continue
        try:
            credentials.revoke_credential(db, ctx, credential_id)
        except Conflict:
            # e.g. used by an enabled model provider: keep it, but agents lose the scope
            row = db.get(Credential, credential_id)
            if row is not None:
                row.allowed_actions = []
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="connected_account.revoked",
        target_type="connected_account",
        target_id=account.id,
        summary=f"Revoked {account.provider} account {account.label}",
        data={"provider": account.provider},
    )
    events.emit(db, ctx.company_id, "connected_account.updated", {"account_id": account.id})
    events.emit(db, ctx.company_id, "reachability.updated", {})
    db.commit()
    return account


def providers_overview() -> list[dict[str, Any]]:
    return [
        {
            "id": s.id,
            "label": s.label,
            "category": s.category,
            "needs_secret": s.needs_secret,
            "secret_fields": list(s.secrets) if s.needs_secret else [],
            "optional_secret_fields": list(s.optional_secrets),
            "metadata_fields": list(s.metadata),
            "required_metadata": list(s.required_metadata),
            "agent_actions": sorted(s.actions),
            "oauth": s.oauth,
            "help": s.help,
        }
        for s in catalog.SPECS.values()
    ]


def oauth_start(ctx: CompanyContext, provider: str) -> None:
    """Gmail / Outlook are connection-only OAuth providers. The OAuth client id and secret belong to
    the operator, not to this repository, and none is configured: say so (never fake a URL)."""
    require_human_admin(ctx, "connect accounts")
    spec = catalog.spec_for(provider)
    if spec is None or not spec.oauth:
        raise ValidationFailed(f"{provider!r} does not use OAuth")
    raise NotAvailable(
        f"{spec.label} OAuth is not configured on this install: it needs an OAuth client "
        "registered by the operator (no client secret ships with Foundry AI OS)",
        milestone="M8",
        details={"provider": provider, "reason": "oauth_not_configured"},
    )
