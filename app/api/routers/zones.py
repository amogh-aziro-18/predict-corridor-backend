"""GET /zones — real database-backed zones API."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.api.schemas.zone import ZoneOut
from app.services.zone_service import ZoneService

router = APIRouter()


@router.get("/zones", response_model=list[ZoneOut], response_model_by_alias=True)
def list_zones(db: Session = Depends(db_dep)) -> list[ZoneOut]:
    return ZoneService(db).list_zones()
