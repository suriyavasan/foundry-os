from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator

from app.schemas.common import ApiModel, RequestModel, Timestamp
from app.schemas.work import ActorTypeLiteral

ArtifactKindLiteral = Literal["document", "code", "design", "report", "data", "link", "other"]
ArtifactStatusLiteral = Literal["draft", "submitted", "accepted", "rejected"]
_MIME = re.compile(r"^[A-Za-z0-9][\w.+-]*/[A-Za-z0-9][\w.+-]*$")


def normalize_mime(value: str | None, default: str) -> str:
    if not value:
        return default
    base = value.split(";", 1)[0].strip().lower()
    if not _MIME.match(base) or len(base) > 160:
        raise ValueError("invalid mime_type")
    return base


class ArtifactVersion(ApiModel):
    id: str
    artifact_id: str
    version: int
    size_bytes: int
    sha256: str
    created_by_type: ActorTypeLiteral
    created_by_id: str | None
    note: str | None
    created_at: Timestamp


class Artifact(ApiModel):
    id: str
    company_id: str
    project_id: str | None
    task_id: str | None
    run_id: str | None
    created_by_type: ActorTypeLiteral
    created_by_id: str | None
    title: str
    kind: ArtifactKindLiteral
    mime_type: str
    status: ArtifactStatusLiteral
    current_version: int
    created_at: Timestamp
    updated_at: Timestamp


class ArtifactDetail(Artifact):
    versions: list[ArtifactVersion] = Field(default_factory=list)


class ArtifactUpdate(RequestModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    status: ArtifactStatusLiteral | None = None

    @field_validator("title", "status")
    @classmethod
    def _not_null(cls, v):
        if v is None:
            raise ValueError("must not be null")
        return v


class ArtifactJsonCreate(RequestModel):
    """Text artifact via JSON (agent API). Give ``artifact_id`` to add a new version."""

    title: str | None = Field(default=None, min_length=1, max_length=300)
    kind: ArtifactKindLiteral = "document"
    content: str = Field(max_length=5_000_000)
    mime_type: str | None = None
    task_id: str | None = None
    project_id: str | None = None
    artifact_id: str | None = None
    note: str | None = Field(default=None, max_length=2000)
