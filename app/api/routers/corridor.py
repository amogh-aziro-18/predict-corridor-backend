"""GET /corridor — real database-backed corridor API."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.api.schemas.corridor import CorridorOut
from app.core.errors import NotFoundError
from app.services.corridor_service import CorridorService

router = APIRouter()


@router.get("/corridor", response_model=CorridorOut, response_model_by_alias=True)
def get_corridor(db: Session = Depends(db_dep)) -> CorridorOut:
    service = CorridorService(db)
    corridors = service.list_corridors()
    if not corridors:
        raise NotFoundError("No corridor seeded")
    return corridors[0]
