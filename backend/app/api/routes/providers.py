"""Credentials and model providers (owner API). Thin: rules live in the services."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from app.api.deps import CompanyCtx, DbSession, get_cipher
from app.core.crypto import CredentialCipher
from app.schemas.common import Page
from app.schemas.providers import (
    Credential,
    CredentialCreate,
    DetectRequest,
    DetectResult,
    ModelProvider,
    ModelProviderCreate,
    ModelProviderUpdate,
)
from app.services import credentials, model_providers
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

router = APIRouter(prefix="/companies/{company_id}", tags=["model-providers"])

Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]
Cipher = Annotated[CredentialCipher, Depends(get_cipher)]


def _transport(request: Request):
    """Tests inject an httpx transport here; production uses the network."""
    return getattr(request.app.state, "provider_transport", None)


# ---------------------------------------------------------------- credentials (metadata only)


@router.get("/credentials", response_model=Page[Credential])
def list_credentials(
    ctx: CompanyCtx, db: DbSession, limit: Limit = DEFAULT_LIMIT, cursor: str | None = None
):
    items, next_cursor = credentials.list_credentials(db, ctx, limit=limit, cursor=cursor)
    return {"items": items, "next_cursor": next_cursor}


@router.post("/credentials", response_model=Credential, status_code=201)
def create_credential(body: CredentialCreate, ctx: CompanyCtx, db: DbSession, cipher: Cipher):
    return credentials.create_credential(db, ctx, cipher, body)


@router.get("/credentials/{credential_id}", response_model=Credential)
def get_credential(credential_id: str, ctx: CompanyCtx, db: DbSession):
    return credentials.get_credential(db, ctx, credential_id)


@router.delete("/credentials/{credential_id}", response_model=Credential)
def revoke_credential(credential_id: str, ctx: CompanyCtx, db: DbSession):
    """Revoke (the stored ciphertext is wiped). Refused while an enabled provider uses it."""
    return credentials.revoke_credential(db, ctx, credential_id)


# ---------------------------------------------------------------- model providers


@router.get("/model-providers", response_model=Page[ModelProvider])
def list_providers(
    ctx: CompanyCtx, db: DbSession, limit: Limit = DEFAULT_LIMIT, cursor: str | None = None
):
    items, next_cursor = model_providers.list_providers(db, ctx, limit=limit, cursor=cursor)
    return {
        "items": model_providers.serialize(db, ctx.company_id, items),
        "next_cursor": next_cursor,
    }


@router.post("/model-providers", response_model=ModelProvider, status_code=201)
def create_provider(body: ModelProviderCreate, ctx: CompanyCtx, db: DbSession):
    row = model_providers.create_provider(db, ctx, body)
    return model_providers.serialize(db, ctx.company_id, [row])[0]


# Static paths before /{provider_id}.
@router.get("/model-providers/detect", response_model=DetectResult)
async def detect_get(request: Request, ctx: CompanyCtx, db: DbSession, cipher: Cipher):
    """Probe local Ollama (read-only: nothing is registered)."""
    return await run_in_threadpool(
        model_providers.detect_ollama, db, ctx, None, cipher, transport=_transport(request)
    )


@router.post("/model-providers/detect", response_model=DetectResult)
async def detect_post(
    request: Request,
    ctx: CompanyCtx,
    db: DbSession,
    cipher: Cipher,
    body: DetectRequest | None = None,
):
    """Probe local Ollama; with ``register: true`` and a tool-capable model, register it (and make
    it the company default) in one call. Idempotent per base URL."""
    return await run_in_threadpool(
        model_providers.detect_ollama, db, ctx, body, cipher, transport=_transport(request)
    )


@router.get("/model-providers/{provider_id}", response_model=ModelProvider)
def get_provider(provider_id: str, ctx: CompanyCtx, db: DbSession):
    row = model_providers.get_provider(db, ctx, provider_id)
    return model_providers.serialize(db, ctx.company_id, [row])[0]


@router.patch("/model-providers/{provider_id}", response_model=ModelProvider)
def update_provider(provider_id: str, body: ModelProviderUpdate, ctx: CompanyCtx, db: DbSession):
    row = model_providers.update_provider(db, ctx, provider_id, body)
    return model_providers.serialize(db, ctx.company_id, [row])[0]


@router.delete("/model-providers/{provider_id}", status_code=204)
def delete_provider(provider_id: str, ctx: CompanyCtx, db: DbSession):
    model_providers.delete_provider(db, ctx, provider_id)
    return Response(status_code=204)


@router.post("/model-providers/{provider_id}/test", response_model=ModelProvider)
async def test_provider(
    provider_id: str, request: Request, ctx: CompanyCtx, db: DbSession, cipher: Cipher
):
    """Connectivity test. For Ollama it loads the model and reports the *served* context window,
    warning (with the fix) when it is smaller than the configured one. Result in ``last_test``."""
    row = await run_in_threadpool(
        model_providers.test_provider, db, ctx, provider_id, cipher, transport=_transport(request)
    )
    return model_providers.serialize(db, ctx.company_id, [row])[0]
