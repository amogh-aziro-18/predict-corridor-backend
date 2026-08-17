"""GET /utilities — real database-backed utilities API."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.api.schemas.utility import UtilitySummaryOut
from app.services.utility_service import UtilityService

router = APIRouter()


@router.get("/utilities", response_model=list[UtilitySummaryOut], response_model_by_alias=True)
def list_utilities(db: Session = Depends(db_dep)) -> list[UtilitySummaryOut]:
    return UtilityService(db).list_utilities()
