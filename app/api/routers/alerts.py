"""Alert list, detail, and status API (Phase 12)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.api.schemas.alert import AlertOut, AlertStatusUpdate
from app.services.alert_service import AlertService

router = APIRouter()


@router.get("/alerts", response_model=list[AlertOut])
def list_alerts(
    status: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    utility_kind: str | None = Query(default=None),
    zone_id: str | None = Query(default=None, alias="zone"),
    source: str | None = Query(default=None),
    db: Session = Depends(db_dep),
) -> list[AlertOut]:
    return AlertService(db).list_alerts(
        status=status,
        severity=severity,
        utility_kind=utility_kind,
        zone_id=zone_id,
        source=source,
    )


@router.get("/alerts/{alert_id}", response_model=AlertOut)
def get_alert(alert_id: str, db: Session = Depends(db_dep)) -> AlertOut:
    return AlertService(db).get_alert(alert_id)


@router.patch("/alerts/{alert_id}/status", response_model=AlertOut)
def update_alert_status(
    alert_id: str,
    body: AlertStatusUpdate,
    db: Session = Depends(db_dep),
) -> AlertOut:
    return AlertService(db).update_status(alert_id, body)
