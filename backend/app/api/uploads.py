"""Parse artifact uploads: multipart (file + fields) or JSON text content."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from app.core.errors import PayloadTooLarge, ValidationFailed
from app.schemas.artifact import ArtifactJsonCreate
from app.services.artifacts import ArtifactInput

CHUNK = 64 * 1024
_KINDS = {"document", "code", "design", "report", "data", "link", "other"}


def _file_chunks(upload: UploadFile) -> Iterator[bytes]:
    try:
        while chunk := upload.file.read(CHUNK):
            yield chunk
    finally:
        upload.file.close()


def _field(form, name: str) -> str | None:
    value = form.get(name)
    if value is None or isinstance(value, UploadFile):
        return None
    value = str(value).strip()
    return value or None


async def read_artifact_input(request: Request, max_bytes: int) -> ArtifactInput:
    content_type = request.headers.get("content-type", "").lower()
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes + 1_000_000:
        raise PayloadTooLarge(
            f"Upload exceeds the {max_bytes} byte limit", details={"max_bytes": max_bytes}
        )

    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise ValidationFailed(
                "A 'file' part is required",
                details=[{"loc": ["body", "file"], "msg": "field required"}],
            )
        kind = _field(form, "kind") or "other"
        if kind not in _KINDS:
            raise ValidationFailed(
                "Invalid artifact kind", details=[{"loc": ["body", "kind"], "msg": "invalid value"}]
            )
        title = _field(form, "title") or (
            upload.filename if not _field(form, "artifact_id") else None
        )
        return ArtifactInput(
            title=title[:300] if title else None,
            kind=kind,
            mime_type=_field(form, "mime_type") or upload.content_type,
            chunks=_file_chunks(upload),
            filename=upload.filename,
            task_id=_field(form, "task_id"),
            project_id=_field(form, "project_id"),
            artifact_id=_field(form, "artifact_id"),
            note=_field(form, "note"),
        )

    if content_type.startswith("application/json"):
        try:
            body = ArtifactJsonCreate.model_validate(await request.json())
        except ValidationError as exc:
            raise ValidationFailed(
                "Request validation failed",
                details=[
                    {"loc": ["body", *map(str, e["loc"])], "msg": e["msg"]} for e in exc.errors()
                ],
            ) from exc
        except ValueError as exc:
            raise ValidationFailed("Malformed JSON body") from exc
        return ArtifactInput(
            title=body.title,
            kind=body.kind,
            mime_type=body.mime_type
            or ("text/markdown" if body.kind in ("document", "report") else "text/plain"),
            chunks=[body.content.encode()],
            task_id=body.task_id,
            project_id=body.project_id,
            artifact_id=body.artifact_id,
            note=body.note,
        )
    raise ValidationFailed("Send multipart/form-data (file upload) or application/json (text)")
