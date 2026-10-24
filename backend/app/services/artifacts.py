"""Artifacts: metadata + versions in the DB, content in the storage interface."""

from __future__ import annotations

import mimetypes
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Forbidden, InvalidTransition, NotFound, ValidationFailed
from app.core.principal import (
    CompanyContext,
    Principal,
    PrincipalType,
    Role,
    require_human_admin,
)
from app.core.time import utcnow
from app.models.agent import Agent
from app.models.artifact import Artifact, ArtifactVersion
from app.models.enums import ArtifactStatus
from app.models.run import TaskRun
from app.models.work import Task
from app.runtimes.base import ArtifactRef
from app.schemas.artifact import ArtifactUpdate, normalize_mime
from app.services import audit, events
from app.services.common import creator_of, get_project_or_404, get_task_or_404
from app.services.pagination import paginate
from app.storage.base import ArtifactStorage

_STATUS_EDGES: dict[str, set[str]] = {
    "draft": {"submitted", "accepted", "rejected"},
    "submitted": {"accepted", "rejected"},
    "rejected": {"draft", "submitted"},
    "accepted": set(),
}
_INLINE_MIMES = {"application/json", "text/plain", "text/markdown", "text/csv"}


@dataclass(frozen=True)
class ArtifactInput:
    title: str | None
    kind: str
    mime_type: str | None
    chunks: Iterable[bytes]
    filename: str | None = None
    task_id: str | None = None
    project_id: str | None = None
    artifact_id: str | None = None
    note: str | None = None


def storage_key(company_id: str, artifact_id: str, version: int) -> str:
    return f"{company_id}/{artifact_id}/{version}"


def is_inline_safe(mime_type: str) -> bool:
    """Only plain text-ish types are shown inline; everything else downloads as attachment
    (html/svg could run script on the API origin)."""
    return mime_type in _INLINE_MIMES


# ---------------------------------------------------------------- queries


def list_artifacts(
    db: Session,
    ctx: CompanyContext,
    *,
    limit: int,
    cursor: str | None,
    project_id: str | None = None,
    task_id: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    q: str | None = None,
):
    stmt = select(Artifact).where(Artifact.company_id == ctx.company_id)
    if project_id:
        stmt = stmt.where(Artifact.project_id == project_id)
    if task_id:
        stmt = stmt.where(Artifact.task_id == task_id)
    if kind:
        stmt = stmt.where(Artifact.kind == kind)
    if status:
        stmt = stmt.where(Artifact.status == status)
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(Artifact.title.ilike(f"%{escaped}%", escape="\\"))
    return paginate(db, stmt, Artifact, limit=limit, cursor=cursor, descending=True)


def get_artifact(db: Session, ctx: CompanyContext, artifact_id: str) -> Artifact:
    artifact = db.scalar(
        select(Artifact).where(Artifact.id == artifact_id, Artifact.company_id == ctx.company_id)
    )
    if artifact is None:
        raise NotFound("Artifact not found")
    return artifact


def list_versions(db: Session, artifact: Artifact) -> list[ArtifactVersion]:
    return list(
        db.scalars(
            select(ArtifactVersion)
            .where(
                ArtifactVersion.artifact_id == artifact.id,
                ArtifactVersion.company_id == artifact.company_id,
            )
            .order_by(ArtifactVersion.version)
        )
    )


def open_version(
    db: Session, ctx: CompanyContext, storage: ArtifactStorage, artifact_id: str, version: int
) -> tuple[Artifact, ArtifactVersion, Iterator[bytes]]:
    artifact = get_artifact(db, ctx, artifact_id)
    row = db.scalar(
        select(ArtifactVersion).where(
            ArtifactVersion.artifact_id == artifact.id,
            ArtifactVersion.company_id == ctx.company_id,
            ArtifactVersion.version == version,
        )
    )
    if row is None or not storage.exists(row.storage_key):
        raise NotFound("Artifact version not found")
    return artifact, row, storage.open(row.storage_key)


# ---------------------------------------------------------------- creation


def _guess_mime(data: ArtifactInput, default: str) -> str:
    if data.mime_type:
        return normalize_mime(data.mime_type, default)
    if data.filename:
        guessed, _ = mimetypes.guess_type(data.filename)
        if guessed:
            return normalize_mime(guessed, default)
    return default


def _store_version(
    db: Session,
    storage: ArtifactStorage,
    artifact: Artifact,
    version: int,
    chunks: Iterable[bytes],
    *,
    max_bytes: int,
    actor_type: str,
    actor_id: str | None,
    note: str | None,
) -> ArtifactVersion:
    key = storage_key(artifact.company_id, artifact.id, version)
    stored = storage.put(key, chunks, max_bytes=max_bytes)
    row = ArtifactVersion(
        company_id=artifact.company_id,
        artifact_id=artifact.id,
        version=version,
        storage_key=key,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
        created_by_type=actor_type,
        created_by_id=actor_id,
        note=note,
    )
    db.add(row)
    return row


def create_or_version(
    db: Session,
    ctx: CompanyContext,
    storage: ArtifactStorage,
    data: ArtifactInput,
    *,
    max_bytes: int,
    run: TaskRun | None = None,
) -> tuple[Artifact, ArtifactVersion]:
    """Create a new artifact (title required) or, with ``artifact_id``, a new version of an
    existing one. Humans: owner/admin. Agents: only through their run (agent API)."""
    principal = ctx.principal
    if ctx.role == Role.VIEWER:
        raise Forbidden("Viewers have read-only access")
    if principal.is_agent:
        if run is None or run.agent_id != principal.id:
            raise Forbidden("Agents submit artifacts through their run")
    elif not ctx.is_human_admin:
        # M7: a member may attach files to a task assigned to them (their completion evidence)
        mine = bool(
            ctx.role == Role.MEMBER
            and ctx.member_id
            and data.task_id
            and db.scalar(
                select(Task.id).where(
                    Task.id == data.task_id,
                    Task.company_id == ctx.company_id,
                    Task.assignee_member_id == ctx.member_id,
                )
            )
        )
        if not mine:
            raise Forbidden("Only company owners and admins can upload artifacts")

    company_id = ctx.company_id
    task = get_task_or_404(db, company_id, data.task_id) if data.task_id else None
    if data.project_id:
        get_project_or_404(db, company_id, data.project_id)
    actor_type, actor_id = creator_of(principal)

    if data.artifact_id:
        artifact = get_artifact(db, ctx, data.artifact_id)
        if principal.is_agent and not (
            artifact.created_by_id == principal.id
            or (artifact.task_id is not None and artifact.task_id == run.task_id)  # type: ignore[union-attr]
        ):
            raise Forbidden("Agents can only add versions to artifacts of their own task")
        version = artifact.current_version + 1
        row = _store_version(
            db,
            storage,
            artifact,
            version,
            data.chunks,
            max_bytes=max_bytes,
            actor_type=actor_type,
            actor_id=actor_id,
            note=data.note,
        )
        artifact.current_version = version
        artifact.updated_at = utcnow()
        if artifact.status == ArtifactStatus.REJECTED:
            artifact.status = ArtifactStatus.DRAFT
        db.flush()
        _audit_artifact(db, ctx, artifact, "artifact.version_added", version)
        events.emit(
            db,
            company_id,
            "artifact.updated",
            {
                "artifact_id": artifact.id,
                "title": artifact.title,
                "version": version,
                "task_id": artifact.task_id,
            },
        )
        return _commit_stored(db, storage, artifact, row)

    if not data.title:
        raise ValidationFailed(
            "title is required for a new artifact",
            details=[{"loc": ["body", "title"], "msg": "field required"}],
        )
    mime = _guess_mime(data, "application/octet-stream")
    artifact = Artifact(
        company_id=company_id,
        project_id=data.project_id or (task.project_id if task else None),
        task_id=data.task_id,
        run_id=run.id if run else None,
        created_by_type=actor_type,
        created_by_id=actor_id,
        title=data.title,
        kind=data.kind,
        mime_type=mime,
        status=ArtifactStatus.DRAFT,
        current_version=1,
    )
    db.add(artifact)
    db.flush()
    row = _store_version(
        db,
        storage,
        artifact,
        1,
        data.chunks,
        max_bytes=max_bytes,
        actor_type=actor_type,
        actor_id=actor_id,
        note=data.note,
    )
    db.flush()
    _audit_artifact(db, ctx, artifact, "artifact.created", 1)
    events.emit(
        db,
        company_id,
        "artifact.created",
        {
            "artifact_id": artifact.id,
            "title": artifact.title,
            "kind": artifact.kind,
            "task_id": artifact.task_id,
            "project_id": artifact.project_id,
            "version": 1,
        },
    )
    return _commit_stored(db, storage, artifact, row)


def _commit_stored(
    db: Session, storage: ArtifactStorage, artifact: Artifact, row: ArtifactVersion
) -> tuple[Artifact, ArtifactVersion]:
    try:
        db.commit()
    except Exception:
        db.rollback()
        storage.delete(row.storage_key)
        raise
    return artifact, row


def _audit_artifact(
    db: Session, ctx: CompanyContext, artifact: Artifact, action: str, version: int
) -> None:
    audit.record(
        db,
        company_id=artifact.company_id,
        principal=ctx.principal,
        action=action,
        target_type="artifact",
        target_id=artifact.id,
        summary=f"{'Added v' + str(version) + ' to' if version > 1 else 'Created'} artifact "
        f"'{artifact.title}'",
        data={"version": version, "kind": artifact.kind, "task_id": artifact.task_id},
    )


def create_from_ref(
    db: Session,
    storage: ArtifactStorage,
    *,
    run: TaskRun,
    task: Task,
    agent: Agent,
    ref: ArtifactRef,
    max_bytes: int = 25 * 1024 * 1024,
) -> Artifact:
    """Persist an artifact reported in a run result (no commit; part of run finalization)."""
    content = ref.content.encode() if isinstance(ref.content, str) else bytes(ref.content)
    artifact = Artifact(
        company_id=run.company_id,
        project_id=task.project_id,
        task_id=task.id,
        run_id=run.id,
        created_by_type="agent",
        created_by_id=agent.id,
        title=ref.title[:300],
        kind=ref.kind
        if ref.kind in {"document", "code", "design", "report", "data", "link"}
        else "other",
        mime_type=normalize_mime(ref.mime_type, "application/octet-stream"),
        status=ArtifactStatus.DRAFT,
        current_version=1,
    )
    db.add(artifact)
    db.flush()
    _store_version(
        db,
        storage,
        artifact,
        1,
        [content],
        max_bytes=max_bytes,
        actor_type="agent",
        actor_id=agent.id,
        note=None,
    )
    db.flush()
    audit.record(
        db,
        company_id=run.company_id,
        principal=Principal(
            type=PrincipalType.AGENT,
            id=agent.id,
            label=agent.name,
            company_id=run.company_id,
        ),
        action="artifact.created",
        target_type="artifact",
        target_id=artifact.id,
        summary=f"Created artifact '{artifact.title}'",
        data={"version": 1, "kind": artifact.kind, "task_id": task.id, "run_id": run.id},
    )
    events.emit(
        db,
        run.company_id,
        "artifact.created",
        {
            "artifact_id": artifact.id,
            "title": artifact.title,
            "kind": artifact.kind,
            "task_id": task.id,
            "project_id": task.project_id,
            "version": 1,
        },
    )
    return artifact


# ---------------------------------------------------------------- update


def update_artifact(
    db: Session, ctx: CompanyContext, artifact_id: str, data: ArtifactUpdate
) -> Artifact:
    require_human_admin(ctx, "update artifacts")
    artifact = get_artifact(db, ctx, artifact_id)
    changed: list[str] = []
    if "title" in data.model_fields_set and data.title != artifact.title:
        artifact.title = data.title  # type: ignore[assignment]
        changed.append("title")
    if "status" in data.model_fields_set and data.status != artifact.status:
        if data.status not in _STATUS_EDGES[artifact.status]:
            raise InvalidTransition(
                f"Cannot move an artifact from {artifact.status} to {data.status}",
                details={"from": artifact.status, "to": data.status},
            )
        artifact.status = data.status  # type: ignore[assignment]
        changed.append("status")
    if changed:
        artifact.updated_at = utcnow()
        audit.record(
            db,
            company_id=ctx.company_id,
            principal=ctx.principal,
            action="artifact.updated",
            target_type="artifact",
            target_id=artifact.id,
            summary=f"Updated artifact '{artifact.title}': {', '.join(changed)}",
            data={"changed": changed, "status": artifact.status},
        )
        events.emit(
            db,
            ctx.company_id,
            "artifact.updated",
            {
                "artifact_id": artifact.id,
                "title": artifact.title,
                "status": artifact.status,
                "task_id": artifact.task_id,
            },
        )
        db.commit()
    return artifact
