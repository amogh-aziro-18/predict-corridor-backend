"""GET /technicians — real database-backed technician registry."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.api.schemas.technician import TechnicianOut
from app.services.technician_service import TechnicianService

router = APIRouter()


@router.get("/technicians", response_model=list[TechnicianOut])
def list_technicians(
    primary_zone_id: str | None = Query(default=None),
    availability: str | None = Query(default=None),
    utility: str | None = Query(
        default=None,
        description=(
            "Filter by utility domain qualification "
            "(water | power | gas | communications)."
        ),
    ),
    db: Session = Depends(db_dep),
) -> list[TechnicianOut]:
    return TechnicianService(db).list_technicians(
        primary_zone_id=primary_zone_id,
        availability=availability,
        utility=utility,
    )


@router.get("/technicians/{technician_id}", response_model=TechnicianOut)
def get_technician(technician_id: str, db: Session = Depends(db_dep)) -> TechnicianOut:
    return TechnicianService(db).get_technician(technician_id)
