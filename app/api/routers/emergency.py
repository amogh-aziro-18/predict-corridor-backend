"""GET /emergency/status — qualifying emergency events (Phase 14.14)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.api.schemas.emergency import EmergencyStatusOut
from app.services.emergency_status_service import EmergencyStatusService

router = APIRouter()


@router.get("/emergency/status", response_model=EmergencyStatusOut)
def get_emergency_status(db: Session = Depends(db_dep)) -> EmergencyStatusOut:
    """
    Answer: is there an active emergency event right now?

    Conservative qualification from existing incidents and critical alerts.
    Does not invent emergencies from warnings. Does not create work orders.
    """
    return EmergencyStatusService(db).get_status()
