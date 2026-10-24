"""Compact, bounded views of tasks and their deliverables for agent-facing context. Text
artifacts are excerpted (secret-looking spans redacted) so a reviewer or a downstream worker
can read what an earlier task produced without a separate tool."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.artifact import Artifact, ArtifactVersion
from app.services import secrets_scan
from app.storage.base import ArtifactStorage

TEXT_MIMES = ("text/", "application/json")
EXCERPT_CHARS = 2000


def _read_excerpt(storage: ArtifactStorage, key: str, chars: int) -> str | None:
    try:
        if not storage.exists(key):
            return None
        data = b""
        for chunk in storage.open(key):
            data += chunk
            if len(data) >= chars * 4:  # utf-8 can use up to 4 bytes per character
                break
    except Exception:  # missing / unreadable content must not break a run's context
        return None
    text = data.decode("utf-8", errors="replace")
    clipped = text[:chars] + ("\n[... truncated]" if len(text) > chars else "")
    return secrets_scan.scrub(clipped)


def artifact_briefs(
    db: Session,
    storage: ArtifactStorage | None,
    company_id: str,
    task_id: str,
    *,
    max_artifacts: int = 3,
    chars: int = EXCERPT_CHARS,
) -> list[dict[str, Any]]:
    """The newest artifacts of a task with a text excerpt of their current version."""
    artifacts = list(
        db.scalars(
            select(Artifact)
            .where(Artifact.company_id == company_id, Artifact.task_id == task_id)
            .order_by(Artifact.created_at.desc(), Artifact.id)
            .limit(max_artifacts)
        )
    )
    out: list[dict[str, Any]] = []
    for artifact in reversed(artifacts):
        excerpt = None
        if storage is not None and artifact.mime_type.startswith(TEXT_MIMES):
            version = db.scalar(
                select(ArtifactVersion).where(
                    ArtifactVersion.artifact_id == artifact.id,
                    ArtifactVersion.company_id == company_id,
                    ArtifactVersion.version == artifact.current_version,
                )
            )
            if version is not None:
                excerpt = _read_excerpt(storage, version.storage_key, chars)
        out.append(
            {
                "id": artifact.id,
                "title": artifact.title,
                "kind": artifact.kind,
                "status": artifact.status,
                "version": artifact.current_version,
                "excerpt": excerpt,
            }
        )
    return out
