from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CompanyCtx, DbSession
from app.schemas.intensity import Intensity, IntensityUpdate
from app.services import intensity

router = APIRouter(prefix="/companies/{company_id}/intensity", tags=["intensity"])


@router.get("", response_model=Intensity)
def get_intensity(ctx: CompanyCtx, db: DbSession):
    return intensity.get(db, ctx)


@router.put("", response_model=Intensity)
def set_intensity(body: IntensityUpdate, ctx: CompanyCtx, db: DbSession):
    return intensity.set_level(db, ctx, body.level, body.confirm)
