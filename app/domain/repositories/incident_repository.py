"""Incident persistence repository."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.domain.enums import IncidentStatus
from app.domain.models import Incident

_ACTIVE_RECOMMENDATION_STATUSES = (
    IncidentStatus.OPEN.value,
    IncidentStatus.ACKNOWLEDGED.value,
    IncidentStatus.IN_PROGRESS.value,
    IncidentStatus.RESOLVED.value,
)


class IncidentRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, incident_id: str) -> Incident | None:
        stmt = (
            select(Incident)
            .where(Incident.id == incident_id)
            .options(selectinload(Incident.events))
        )
        return self._db.scalars(stmt).first()

    def list(
        self,
        *,
        status: str | None = None,
        severity: str | None = None,
        zone_id: str | None = None,
        utility_kind: str | None = None,
        scope: str | None = None,
        asset_id: str | None = None,
    ) -> list[Incident]:
        stmt = select(Incident).order_by(Incident.created_at.desc())
        if status:
            stmt = stmt.where(Incident.status == status)
        if severity:
            stmt = stmt.where(Incident.severity == severity)
        if zone_id:
            stmt = stmt.where(Incident.zone_id == zone_id)
        if utility_kind:
            stmt = stmt.where(Incident.utility_kind == utility_kind)
        if scope:
            stmt = stmt.where(Incident.scope == scope)
        if asset_id:
            stmt = stmt.where(Incident.asset_id == asset_id)
        return list(self._db.scalars(stmt).all())

    def find_active_by_recommendation_id(self, recommendation_id: str) -> Incident | None:
        stmt = (
            select(Incident)
            .where(Incident.recommendation_id == recommendation_id)
            .where(Incident.status.in_(_ACTIVE_RECOMMENDATION_STATUSES))
            .order_by(Incident.created_at.desc())
            .limit(1)
        )
        return self._db.scalars(stmt).first()

    def add(self, incident: Incident) -> Incident:
        self._db.add(incident)
        self._db.flush()
        return incident

    def save(self, incident: Incident) -> Incident:
        self._db.add(incident)
        self._db.commit()
        self._db.refresh(incident)
        return incident

    def delete(self, incident: Incident) -> None:
        self._db.delete(incident)
        self._db.commit()
