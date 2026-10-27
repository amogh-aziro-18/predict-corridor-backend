"""Incident audit event repository."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import IncidentEvent


class IncidentEventRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, event_id: str) -> IncidentEvent | None:
        return self._db.get(IncidentEvent, event_id)

    def list_for_incident(self, incident_id: str) -> list[IncidentEvent]:
        stmt = (
            select(IncidentEvent)
            .where(IncidentEvent.incident_id == incident_id)
            .order_by(IncidentEvent.created_at.asc())
        )
        return list(self._db.scalars(stmt).all())

    def add(self, event: IncidentEvent) -> IncidentEvent:
        self._db.add(event)
        self._db.flush()
        return event

    def save(self, event: IncidentEvent) -> IncidentEvent:
        self._db.add(event)
        self._db.commit()
        self._db.refresh(event)
        return event
