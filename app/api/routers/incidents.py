"""Incident CRUD, lifecycle, and intelligence API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.api.schemas.incident import (
    IncidentCreate,
    IncidentOut,
    IncidentStatusUpdate,
    IncidentUpdate,
)
from app.api.schemas.incident_intelligence import IncidentIntelligenceOut
from app.services.incident_intelligence_service import IncidentIntelligenceService
from app.services.incident_service import IncidentService

router = APIRouter()


@router.post("/incidents", response_model=IncidentOut, status_code=201)
def create_incident(
    body: IncidentCreate,
    db: Session = Depends(db_dep),
) -> IncidentOut:
    return IncidentService(db).create_incident(body)


@router.get("/incidents", response_model=list[IncidentOut])
def list_incidents(
    status: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    zone_id: str | None = Query(default=None, alias="zone"),
    utility_kind: str | None = Query(default=None),
    scope: str | None = Query(default=None),
    db: Session = Depends(db_dep),
) -> list[IncidentOut]:
    return IncidentService(db).list_incidents(
        status=status,
        severity=severity,
        zone_id=zone_id,
        utility_kind=utility_kind,
        scope=scope,
    )


@router.get("/incidents/{incident_id}/intelligence", response_model=IncidentIntelligenceOut)
def get_incident_intelligence(
    incident_id: str,
    hours: int = Query(
        default=24,
        ge=1,
        le=168,
        description="Forecast horizon forwarded to weather/environment/resilience assessments",
    ),
    db: Session = Depends(db_dep),
) -> IncidentIntelligenceOut:
    """Compose-only situational context for one incident (Phase 11B)."""
    return IncidentIntelligenceService(db).get_context(incident_id, hours=hours)


@router.get("/incidents/{incident_id}", response_model=IncidentOut)
def get_incident(incident_id: str, db: Session = Depends(db_dep)) -> IncidentOut:
    return IncidentService(db).get_incident(incident_id)


@router.patch("/incidents/{incident_id}", response_model=IncidentOut)
def update_incident(
    incident_id: str,
    body: IncidentUpdate,
    db: Session = Depends(db_dep),
) -> IncidentOut:
    return IncidentService(db).update_incident(incident_id, body)


@router.patch("/incidents/{incident_id}/status", response_model=IncidentOut)
def update_incident_status(
    incident_id: str,
    body: IncidentStatusUpdate,
    db: Session = Depends(db_dep),
) -> IncidentOut:
    return IncidentService(db).update_incident_status(incident_id, body)
