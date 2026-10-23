from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.api.deps import CompanyCtx, DbSession, get_storage
from app.api.uploads import read_artifact_input
from app.schemas.artifact import (
    Artifact,
    ArtifactDetail,
    ArtifactKindLiteral,
    ArtifactStatusLiteral,
    ArtifactUpdate,
    ArtifactVersion,
)
from app.schemas.common import Page
from app.services import artifacts
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT
from app.storage.base import ArtifactStorage

router = APIRouter(prefix="/companies/{company_id}/artifacts", tags=["artifacts"])
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


def _detail(db, artifact) -> ArtifactDetail:
    detail = ArtifactDetail.model_validate(artifact)
    detail.versions = [
        ArtifactVersion.model_validate(v) for v in artifacts.list_versions(db, artifact)
    ]
    return detail


@router.get("", response_model=Page[Artifact])
def list_artifacts(
    ctx: CompanyCtx,
    db: DbSession,
    project_id: str | None = None,
    task_id: str | None = None,
    kind: ArtifactKindLiteral | None = None,
    status: ArtifactStatusLiteral | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    items, nxt = artifacts.list_artifacts(
        db,
        ctx,
        limit=limit,
        cursor=cursor,
        project_id=project_id,
        task_id=task_id,
        kind=kind,
        status=status,
        q=q,
    )
    return {"items": items, "next_cursor": nxt}


@router.post("", response_model=ArtifactDetail, status_code=201)
async def upload_artifact(
    request: Request,
    ctx: CompanyCtx,
    db: DbSession,
    storage: ArtifactStorage = Depends(get_storage),
):
    """Human upload: multipart (``file`` + title/kind/task_id/project_id/note) or JSON text.
    Give ``artifact_id`` to add a new version."""
    max_bytes = request.app.state.settings.max_upload_bytes
    data = await read_artifact_input(request, max_bytes)

    def work():
        artifact, _ = artifacts.create_or_version(db, ctx, storage, data, max_bytes=max_bytes)
        return _detail(db, artifact)

    return await run_in_threadpool(work)


@router.get("/{artifact_id}", response_model=ArtifactDetail)
def get_artifact(artifact_id: str, ctx: CompanyCtx, db: DbSession):
    return _detail(db, artifacts.get_artifact(db, ctx, artifact_id))


@router.patch("/{artifact_id}", response_model=ArtifactDetail)
def update_artifact(artifact_id: str, body: ArtifactUpdate, ctx: CompanyCtx, db: DbSession):
    return _detail(db, artifacts.update_artifact(db, ctx, artifact_id, body))


def _download_name(title: str, version: int) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", title).strip("._") or "artifact"
    return f"{base[:80]}-v{version}"


@router.get("/{artifact_id}/versions/{version}/content")
def download_version(
    artifact_id: str,
    version: int,
    ctx: CompanyCtx,
    db: DbSession,
    storage: ArtifactStorage = Depends(get_storage),
):
    artifact, row, chunks = artifacts.open_version(db, ctx, storage, artifact_id, version)
    disposition = "inline" if artifacts.is_inline_safe(artifact.mime_type) else "attachment"
    filename = _download_name(artifact.title, version)
    return StreamingResponse(
        chunks,
        media_type=artifact.mime_type,
        headers={
            "Content-Length": str(row.size_bytes),
            "Content-Disposition": f'{disposition}; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
            "ETag": f'"{row.sha256}"',
            "Cache-Control": "private, max-age=0, must-revalidate",
        },
    )
