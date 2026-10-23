"""Credential store: encrypted secrets with metadata-only access.

Owners/admins create and revoke; viewers may list metadata; agents get nothing. The plaintext
leaves this module only through ``reveal`` which the runtime adapter calls right before writing
the run spec to the runtime's stdin -- it never reaches an API response, audit row, event or log.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.crypto import CredentialCipher
from app.core.errors import Conflict, Forbidden, NotFound
from app.core.principal import CompanyContext, require_human_admin
from app.core.time import utcnow
from app.models.provider import Credential, ModelProvider
from app.schemas.providers import CredentialCreate
from app.services import audit, events
from app.services.pagination import paginate


def _require_member_human(ctx: CompanyContext) -> None:
    if not ctx.principal.is_human:
        raise Forbidden("Agents cannot access credentials")


def list_credentials(
    db: Session, ctx: CompanyContext, *, limit: int, cursor: str | None
) -> tuple[list[Credential], str | None]:
    _require_member_human(ctx)
    stmt = select(Credential).where(Credential.company_id == ctx.company_id)
    return paginate(db, stmt, Credential, limit=limit, cursor=cursor, descending=False)


def get_credential(db: Session, ctx: CompanyContext, credential_id: str) -> Credential:
    _require_member_human(ctx)
    return _load(db, ctx.company_id, credential_id)


def _load(db: Session, company_id: str, credential_id: str) -> Credential:
    row = db.scalar(
        select(Credential).where(
            Credential.id == credential_id, Credential.company_id == company_id
        )
    )
    if row is None:
        raise NotFound("Credential not found")
    return row


def create_credential(
    db: Session, ctx: CompanyContext, cipher: CredentialCipher, data: CredentialCreate
) -> Credential:
    require_human_admin(ctx, "store credentials")
    live = db.scalar(
        select(Credential.id).where(
            Credential.company_id == ctx.company_id,
            Credential.name == data.name,
            Credential.revoked_at.is_(None),
        )
    )
    if live:
        raise Conflict(f"A credential named {data.name!r} already exists")
    row = Credential(
        company_id=ctx.company_id,
        name=data.name,
        kind=data.kind,
        encrypted_value=cipher.encrypt(data.value.get_secret_value()),
        created_by_type=ctx.principal.type.value,
        created_by_id=ctx.principal.id,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict(f"A credential named {data.name!r} already exists") from exc
    # Name and kind only: never the value, a fragment or a fingerprint of it.
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="credential.created",
        target_type="credential",
        target_id=row.id,
        summary=f"Stored credential {row.name}",
        data={"name": row.name, "kind": row.kind},
    )
    events.emit(db, ctx.company_id, "credential.created", {"credential_id": row.id})
    db.commit()
    return row


def revoke_credential(db: Session, ctx: CompanyContext, credential_id: str) -> Credential:
    require_human_admin(ctx, "revoke credentials")
    row = _load(db, ctx.company_id, credential_id)
    if row.revoked_at is not None:
        return row
    in_use = list(
        db.scalars(
            select(ModelProvider.name).where(
                ModelProvider.company_id == ctx.company_id,
                ModelProvider.credential_id == row.id,
                ModelProvider.enabled.is_(True),
            )
        )
    )
    if in_use:
        raise Conflict(
            "Credential is used by an enabled model provider: "
            + ", ".join(in_use)
            + ". Disable or change the provider first."
        )
    now = utcnow()
    row.revoked_at = now
    row.updated_at = now
    row.encrypted_value = ""  # a revoked secret is not kept
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="credential.revoked",
        target_type="credential",
        target_id=row.id,
        summary=f"Revoked credential {row.name}",
        data={"name": row.name, "kind": row.kind},
    )
    events.emit(db, ctx.company_id, "credential.revoked", {"credential_id": row.id})
    db.commit()
    return row


def require_usable(db: Session, company_id: str, credential_id: str) -> Credential:
    """Validate a credential reference (same company, not revoked) before attaching it."""
    row = db.scalar(
        select(Credential).where(
            Credential.id == credential_id, Credential.company_id == company_id
        )
    )
    if row is None:
        raise NotFound("Credential not found")
    if row.revoked_at is not None:
        raise Conflict("Credential has been revoked")
    return row


def reveal(db: Session, cipher: CredentialCipher, company_id: str, credential_id: str) -> str:
    """Plaintext for the runtime adapter only. Raises NotFound/Conflict for foreign or revoked ids
    and ``CredentialDecryptError`` when the key changed."""
    row = require_usable(db, company_id, credential_id)
    return cipher.decrypt(row.encrypted_value)


def revoked_at(row: Credential) -> datetime | None:
    return row.revoked_at
